import os
import json
import uuid
import time
import base64
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, asdict
import tempfile
import shutil
from dotenv import load_dotenv
import tiktoken
import requests
import threading
from concurrent.futures import ThreadPoolExecutor

from azure.cognitiveservices.vision.computervision import ComputerVisionClient  # type: ignore[import-untyped]
from azure.cognitiveservices.vision.computervision.models import OperationStatusCodes  # type: ignore[import-untyped]
from msrest.authentication import CognitiveServicesCredentials  # type: ignore[import-untyped]

from file_processors import FileProcessor
from image_extraction import VideoFrameExtractor
import config
from backend import SummaryJob


class AISummaryError(Exception):
    """Base exception for AI summary operations."""


class APIRequestError(AISummaryError):
    """Raised when Azure OpenAI API request fails."""


class ContentFilterError(AISummaryError):
    """Raised when content is filtered by Azure OpenAI."""


class ConnectionTestError(AISummaryError):
    """Raised when Azure OpenAI connection test fails."""


class SummaryProcessingError(AISummaryError):
    """Raised when summary processing encounters an error."""

DEFAULT_MODEL = "gpt-5.4-nano"

# Load Environment
load_dotenv()

# Check for LOCAL_TESTING_MODE
LOCAL_TESTING_MODE = config.LOCAL_TESTING_MODE

# Mock services for local testing
get_mock_ai = None
get_mock_ocr = None
if LOCAL_TESTING_MODE:
    print("🧪 [AI SUMMARY] Local Testing Mode enabled - using mock AI services")
    from local_mock import get_mock_ai, get_mock_ocr  # type: ignore[assignment]

class TokenManager:
    """Token counting for gpt-5.4-nano with 1M context - NO CHUNKING"""
    
    def __init__(self, model_name: str = DEFAULT_MODEL):
        try:
            model_encoding_map = {
                "gpt-5.4-nano": "o200k_base",
                "gpt-4.1-mini": "o200k_base",
            }
            
            encoding_name = model_encoding_map.get(model_name, "o200k_base")
            self.encoder = tiktoken.get_encoding(encoding_name)
        except Exception as e:
            print(f"Warning: Could not load tokenizer for {model_name}, using fallback: {e}")
            self.encoder = tiktoken.get_encoding("cl100k_base")
        
        # gpt-5.4-nano capacity: 1M input, 32k output
        self.max_input_tokens = config.AZURE_OPENAI_MAX_TOKENS
        self.max_completion_tokens = config.AZURE_OPENAI_COMPLETION_TOKENS
        
        # Reserve tokens for system/user prompts
        self.system_prompt_tokens = 3000
        self.user_prompt_tokens = 1000
        
        # Calculate available tokens for content
        self.max_content_tokens = (
            self.max_input_tokens - 
            self.system_prompt_tokens - 
            self.user_prompt_tokens - 
            self.max_completion_tokens
        )
        
        print(f"[STATS] Token Manager initialized ({DEFAULT_MODEL} {self.max_input_tokens // 1000}k - NO CHUNKING):")
        print(f"   - Max input tokens: {self.max_input_tokens:,}")
        print(f"   - Max content tokens: {self.max_content_tokens:,}")
        print(f"   - Max completion: {self.max_completion_tokens:,}")
    
    def count_tokens(self, text: str) -> int:
        """Count tokens in text"""
        try:
            safe_text = text.encode("utf-8", errors="replace").decode("utf-8")
            return len(self.encoder.encode(safe_text))
        except Exception:
            return len(text) // 4
    
    def truncate_text(self, text: str, max_tokens: int) -> str:
        """Truncate text to fit within token limit"""
        if not text:
            return text
        
        current_tokens = self.count_tokens(text)
        if current_tokens <= max_tokens:
            return text
        
        # Simple truncation - keep beginning and note truncation
        lines = text.split('\n')
        truncated_lines = []
        current_tokens = 0
        
        for line in lines:
            line_tokens = self.count_tokens(line + '\n')
            if current_tokens + line_tokens > max_tokens:
                truncated_lines.append("[Content truncated to fit token limit]")
                break
            truncated_lines.append(line)
            current_tokens += line_tokens
        
        return '\n'.join(truncated_lines)
    
    def optimize_content_for_tokens(self, transcripts: List[Dict], documents: List[Dict], 
                                  image_insights: List[Dict], user_prompt: str) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """
        SIMPLIFIED: Just ensure total content fits in budget - NO CHUNKING
        """
        prompt_tokens = self.count_tokens(user_prompt)
        
        # Calculate current content tokens
        total_transcript_tokens = sum(self.count_tokens(t.get('content', '')) for t in transcripts)
        total_document_tokens = sum(self.count_tokens(d.get('content', '')) for d in documents)
        total_image_tokens = len(image_insights) * 400  # Estimate per image
        
        current_total = total_transcript_tokens + total_document_tokens + total_image_tokens + prompt_tokens
        
        print("[STATS] Content Token Analysis:")
        print(f"   - Transcripts: {total_transcript_tokens:,} tokens")
        print(f"   - Documents: {total_document_tokens:,} tokens")
        print(f"   - Images: ~{total_image_tokens:,} tokens")
        print(f"   - User prompt: {prompt_tokens:,} tokens")
        print(f"   - TOTAL: {current_total:,} tokens")
        print(f"   - Available: {self.max_content_tokens:,} tokens")
        print(f"   - Remaining: {self.max_content_tokens - current_total:,} tokens")
        
        # If we're under the limit, return as-is
        if current_total <= self.max_content_tokens:
            print("[OK] Content fits within token budget - NO TRUNCATION NEEDED")
            return transcripts, documents, image_insights
        
        # If over limit, proportionally truncate
        print(f"[WARN] Content exceeds budget by {current_total - self.max_content_tokens:,} tokens")
        print("   Applying proportional truncation...")
        
        # Calculate how much we need to reduce
        reduction_ratio = self.max_content_tokens / current_total
        
        # Apply reduction to transcripts
        transcript_budget = int(total_transcript_tokens * reduction_ratio)
        if len(transcripts) == 1:
            transcripts[0]['content'] = self.truncate_text(
                transcripts[0].get('content', ''), 
                transcript_budget
            )
        else:
            per_transcript = transcript_budget // len(transcripts)
            for t in transcripts:
                t['content'] = self.truncate_text(t.get('content', ''), per_transcript)
        
        # Apply reduction to documents
        document_budget = int(total_document_tokens * reduction_ratio)
        if documents:
            if len(documents) == 1:
                documents[0]['content'] = self.truncate_text(
                    documents[0].get('content', ''), 
                    document_budget
                )
            else:
                per_document = document_budget // len(documents)
                for d in documents:
                    d['content'] = self.truncate_text(d.get('content', ''), per_document)
        
        # Reduce images if needed
        if len(image_insights) > 15:
            image_insights = image_insights[:15]
        
        # Verify final size
        final_transcript_tokens = sum(self.count_tokens(t.get('content', '')) for t in transcripts)
        final_document_tokens = sum(self.count_tokens(d.get('content', '')) for d in documents)
        final_image_tokens = len(image_insights) * 400
        final_total = final_transcript_tokens + final_document_tokens + final_image_tokens + prompt_tokens
        
        print("[OK] After optimization:")
        print(f"   - Transcripts: {final_transcript_tokens:,} tokens")
        print(f"   - Documents: {final_document_tokens:,} tokens")
        print(f"   - Images: ~{final_image_tokens:,} tokens")
        print(f"   - TOTAL: {final_total:,} / {self.max_content_tokens:,} tokens")
        
        return transcripts, documents, image_insights

class AISummaryManager:
    """AI-powered conference summarization - Blob Storage Only"""
    
    def __init__(self):
        # Azure OpenAI Configuration
        self.azure_openai_endpoint = (config.AZURE_OPENAI_ENDPOINT or "").rstrip('/')
        self.azure_openai_key = config.AZURE_OPENAI_KEY
        self.azure_openai_deployment = config.AZURE_OPENAI_DEPLOYMENT or DEFAULT_MODEL
        self.azure_openai_api_version = config.AZURE_OPENAI_API_VERSION
        
        # Computer Vision Configuration
        self.cv_endpoint = config.COMPUTER_VISION_ENDPOINT
        self.cv_key = config.COMPUTER_VISION_KEY
        
        # Initialize services
        self.cv_client = None
        self.file_processor = FileProcessor()
        self.frame_extractor = VideoFrameExtractor()
        self.token_manager = TokenManager(self.azure_openai_deployment)
        
        # Background processing
        self.executor = ThreadPoolExecutor(max_workers=3)
        self.running = True
        
        # Initialize blob storage integration
        self.blob_storage = None
        self._init_blob_storage()
        
        self._init_services()
        
        # Start background worker
        self.worker_thread = threading.Thread(target=self._background_summary_worker, daemon=True)
        self.worker_thread.start()
        
        print("[AI] AI Summary Manager initialized (Blob Storage Only)")
    
    def _init_blob_storage(self):
        """Initialize integration with blob storage"""
        try:
            from backend import transcription_manager
            self.blob_storage = transcription_manager.blob_storage
            print("🔗 Blob storage integration initialized successfully")
        except ImportError as e:
            print(f"Warning: Could not initialize blob storage integration: {e}")
            self.blob_storage = None
    
    def _background_summary_worker(self):
        """Background worker for AI summary processing"""
        iteration_count = 0
        while self.running:
            try:
                self._poll_and_submit_pending_jobs(iteration_count)
                time.sleep(10)
                iteration_count += 1
            except Exception as e:
                print(f"[ERROR] AI Summary background worker error: {e}")
                time.sleep(30)

    def _poll_and_submit_pending_jobs(self, iteration_count: int):
        """Poll blob storage for pending summary jobs and submit them."""
        if not self.blob_storage:
            return
        pending_summary_jobs = self.blob_storage.get_pending_summary_jobs()
        if not pending_summary_jobs:
            return
        if iteration_count % 6 == 0:
            active = len([j for j in pending_summary_jobs if j.status == 'processing'])
            queued = len([j for j in pending_summary_jobs if j.status == 'pending'])
            if active > 0 or queued > 0:
                print(f"[AI] AI Summary worker: {active} processing, {queued} queued")
        for job in pending_summary_jobs:
            if job.status == 'pending':
                self.executor.submit(self._process_summary_job_background, job.job_id, job.user_id)
    
    def _init_services(self):
        """Initialize services with validation"""
        if not all([self.azure_openai_endpoint, self.azure_openai_key, self.azure_openai_deployment]):
            print("ERROR: Missing Azure OpenAI configuration")
            raise ValueError("Azure OpenAI configuration incomplete")
        
        if not self.azure_openai_endpoint.startswith("https://"):
            raise ValueError("AZURE_OPENAI_ENDPOINT must be a valid HTTPS URL")
        
        self.azure_openai_endpoint = self.azure_openai_endpoint.rstrip('/')
        
        print(f"[AI] Azure OpenAI initialized: {self.azure_openai_deployment} at {self.azure_openai_endpoint}")
        
        try:
            self._test_azure_openai_connection()
        except Exception as e:
            print(f"WARNING: Azure OpenAI connection test failed: {e}")
        
        if self.cv_key and self.cv_endpoint:
            try:
                self.cv_client = ComputerVisionClient(
                    self.cv_endpoint,
                    CognitiveServicesCredentials(self.cv_key)
                )
                print("[VISION] Computer Vision Client initialized")
            except Exception as e:
                print(f"WARNING: Computer Vision initialization failed: {e}")
        else:
            print("Computer Vision key/endpoint not found - image processing disabled")
    
    def _test_azure_openai_connection(self):
        """Test Azure OpenAI connection"""
        url = f"{self.azure_openai_endpoint}/openai/deployments/{self.azure_openai_deployment}/chat/completions?api-version={self.azure_openai_api_version}"
        
        headers = {
            "Content-Type": "application/json",
            "api-key": self.azure_openai_key
        }
        
        test_data = {
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 5,
            "temperature": 0.1
        }
        
        try:
            response = requests.post(url, headers=headers, json=test_data, timeout=10)
            if response.status_code == 200:
                print("[OK] Azure OpenAI connection test: SUCCESS")
            else:
                print(f"Azure OpenAI connection test failed: {response.status_code} - {response.text}")
                raise ConnectionTestError(f"Connection test failed: {response.status_code}")
        except requests.exceptions.RequestException as e:
            print(f"Azure OpenAI connection test error: {e}")
            raise
    
    def submit_summary_job(
        self, 
        user_id: str,
        summary_type: str,
        user_prompt: str,
        files: Optional[List] = None,
        transcript_job_ids: Optional[List[str]] = None,
        settings: Optional[Dict] = None,
        transcript_content: Optional[str] = None
    ) -> str:
        """Submit AI summary job - save to blob storage"""
        job_id = str(uuid.uuid4())
        
        original_files = []
        if files:
            original_files.extend([f.name if hasattr(f, 'name') else str(f) for f in files])
        if transcript_job_ids:
            original_files.extend([f"transcript_{tid[:8]}..." for tid in transcript_job_ids])
        if transcript_content:
            original_files.append("direct_transcript_input")
        
        print(f"[AI] [{user_id[:8]}...] New AI summary job: {summary_type}")
        print(f"User prompt: {user_prompt[:100]}{'...' if len(user_prompt) > 100 else ''}")
        
        # Store transcript content directly in settings if provided
        if not settings:
            settings = {}
        if transcript_content:
            settings['direct_transcript'] = transcript_content
            print(f"📝 Direct transcript content provided: {len(transcript_content)} chars")
        
        job = SummaryJob(
            job_id=job_id,
            user_id=user_id,
            original_files=original_files,
            summary_type=summary_type,
            user_prompt=user_prompt,
            status="pending",
            created_at=datetime.now().isoformat(),
            settings=settings
        )
        
        if self.blob_storage:
            self.blob_storage.save_summary_job(job)
        
        if files and len(files) > 0:
            self.executor.submit(self._process_summary_job, job_id, user_id, files, transcript_job_ids)
        
        return job_id
    
    def submit_summary_job_enhanced(
        self,
        user_id: str,
        content_mode: str,
        summary_type: str,
        user_prompt: str,
        existing_transcript_ids=None,
        audio_video_files=None,
        document_files=None,
        settings=None,
    ) -> str:
        """Compatibility wrapper for app.py"""
        existing_transcript_ids = existing_transcript_ids or []
        files = (audio_video_files or []) + (document_files or [])
        settings = dict(settings or {})
        settings.setdefault("content_mode", content_mode)
        if existing_transcript_ids:
            settings.setdefault("transcript_job_ids", existing_transcript_ids)

        return self.submit_summary_job(
            user_id=user_id,
            summary_type=summary_type,
            user_prompt=user_prompt,
            files=files,
            transcript_job_ids=existing_transcript_ids,
            settings=settings,
        )
    
    def _process_summary_job_background(self, job_id: str, user_id: str):
        """Process AI summary job from background worker"""
        self._process_summary_job(job_id, user_id, [], [])
    
    def _process_summary_job(self, job_id: str, user_id: str, files: Optional[List] = None, transcript_job_ids: Optional[List[str]] = None):
        """Process AI summary job"""
        job = None
        try:
            job = self.get_summary_status(job_id)
            if not job:
                print(f"Job {job_id[:8]}... not found")
                return
            
            print(f"[AI] [{user_id[:8]}...] Processing AI summary job: {job_id[:8]}...")
            
            job.status = "processing"
            if self.blob_storage:
                self.blob_storage.save_summary_job(job)
            
            # Gather all content
            processed_content = self._gather_content(job, files, transcript_job_ids, user_id)
            
            # Analyze images with Computer Vision
            image_insights = self._analyze_all_images(processed_content)
            
            # Optimize and generate summary
            summary_result = self._optimize_and_summarize(job, processed_content, image_insights)
            
            # Store and finalize
            self._finalize_summary_job(job, user_id, job_id, summary_result, processed_content, image_insights)
            
        except Exception as e:
            print(f"[ERROR] AI summary processing failed: {e}")
            if job and self.blob_storage:
                job.status = "failed"
                job.error_message = str(e)
                job.completed_at = datetime.now().isoformat()
                self.blob_storage.save_summary_job(job)

    def _gather_content(self, job: SummaryJob, files: Optional[List], transcript_job_ids: Optional[List[str]], user_id: str) -> Dict:
        """Gather transcripts, documents, images from all sources."""
        processed_content: Dict[str, list] = {
            'transcripts': [], 'documents': [], 'images': [], 'extracted_frames': []
        }
        # PRIORITY 1: Direct transcript content
        if job.settings and 'direct_transcript' in job.settings:
            transcript_text = job.settings['direct_transcript']
            transcript_text = transcript_text.encode("utf-8", errors="replace").decode("utf-8")
            print(f"\ud83d\udcdd Using direct transcript content: {len(transcript_text)} chars")
            processed_content['transcripts'].append({
                'source': 'Direct Text Input', 'content': transcript_text, 'type': 'transcript'
            })
        # PRIORITY 2: Existing transcripts
        if transcript_job_ids:
            existing = self._get_existing_transcripts(transcript_job_ids, user_id)
            processed_content['transcripts'].extend(existing)
            print(f"\ud83d\udcdd Loaded {len(existing)} existing transcripts")
        elif job.settings and 'transcript_job_ids' in job.settings:
            settings_ids: List[str] = job.settings['transcript_job_ids']
            existing = self._get_existing_transcripts(settings_ids, user_id)
            processed_content['transcripts'].extend(existing)
            print(f"\ud83d\udcdd Loaded {len(existing)} transcripts from job settings")
        # PRIORITY 3: Uploaded files
        if files:
            self._process_uploaded_files(files, user_id, job, processed_content)
        return processed_content

    def _process_uploaded_files(self, files: List, user_id: str, job: SummaryJob, processed_content: Dict):
        """Process uploaded files and classify into content types."""
        for i, file in enumerate(files):
            file_path = getattr(file, 'name', file) if hasattr(file, 'name') else str(file)
            filename = os.path.basename(file_path) if file_path else 'unknown'
            print(f"Processing file {i+1}/{len(files)}: {filename}")
            is_transcript_file = self._is_transcript_file(job, filename)
            file_content = self._process_uploaded_file(file, user_id, is_transcript_file)
            if file_content:
                self._classify_file_content(file_content, filename, processed_content)

    def _is_transcript_file(self, job: SummaryJob, filename: str) -> bool:
        """Check if file should be treated as transcript based on job settings."""
        if not job.settings:
            return False
        return (job.settings.get('source_filename') == filename or
                job.settings.get('content_mode') == 'Text Input')

    def _classify_file_content(self, file_content: Dict, filename: str, processed_content: Dict):
        """Classify processed file content into the appropriate category."""
        content_type = file_content['type']
        if content_type == 'transcript':
            processed_content['transcripts'].append(file_content)
            print(f"\ud83d\udcdd Added as TRANSCRIPT: {filename}")
        elif content_type == 'video':
            frames = self._extract_significant_frames(file_content['path'])
            processed_content['extracted_frames'].extend(frames)
            print(f"\ud83c\udfa5 Extracted {len(frames)} frames from video")
        elif content_type == 'document':
            processed_content['documents'].append(file_content)
            print(f"[DOC] Added as DOCUMENT: {filename}")
        elif content_type == 'image':
            processed_content['images'].append(file_content)
            print(f"\ud83d\uddbc\ufe0f Added as IMAGE: {filename}")

    def _analyze_all_images(self, processed_content: Dict) -> List[Dict]:
        """Analyze all images and extracted frames with Computer Vision."""
        image_insights: List[Dict] = []
        all_images = processed_content['images'] + processed_content['extracted_frames']
        print(f"Analyzing {len(all_images)} images...")
        for image_info in all_images:
            analysis = self._analyze_image_content(image_info['path'])
            if analysis:
                image_insights.append({'source': image_info['filename'], 'analysis': analysis})
        print("\n[OK] Content Classification Complete:")
        print(f"   \ud83d\udcdd Transcripts: {len(processed_content['transcripts'])} items")
        print(f"   [DOC] Documents: {len(processed_content['documents'])} items")
        print(f"   \ud83d\uddbc\ufe0f Images: {len(image_insights)} items")
        return image_insights

    def _optimize_and_summarize(self, job: SummaryJob, processed_content: Dict, image_insights: List[Dict]) -> str:
        """Optimize content tokens and generate AI summary."""
        optimized_transcripts, optimized_documents, optimized_images = self.token_manager.optimize_content_for_tokens(
            processed_content['transcripts'], processed_content['documents'],
            image_insights, job.user_prompt
        )
        output_language = job.settings.get('output_language', 'English') if job.settings else 'English'
        return self._generate_ai_summary_with_openai(
            transcripts=optimized_transcripts, documents=optimized_documents,
            image_insights=optimized_images, user_prompt=job.user_prompt,
            summary_type=job.summary_type, output_language=output_language,
            settings=job.settings or {}
        )

    def _finalize_summary_job(self, job: SummaryJob, user_id: str, job_id: str,
                               summary_result: str, processed_content: Dict, _image_insights: List[Dict]):
        """Store summary result and update job status to completed."""
        all_images = processed_content['images'] + processed_content['extracted_frames']
        chat_url = ""
        if self.blob_storage:
            try:
                chat_url = self.blob_storage.upload_summary_result(summary_result, user_id, job_id)
                print(f"\ud83d\udcac Summary stored successfully: {chat_url}")
            except Exception as e:
                print(f"[WARN] Warning: Could not store summary: {e}")
        job.status = "completed"
        job.summary_text = summary_result
        job.completed_at = datetime.now().isoformat()
        job.processed_files = {
            'transcript_count': len(processed_content['transcripts']),
            'document_count': len(processed_content['documents']),
            'image_count': len(all_images),
            'extracted_frames': len(processed_content['extracted_frames'])
        }
        job.extracted_images = [img['filename'] for img in processed_content['extracted_frames']]
        job.chat_response_url = chat_url
        if self.blob_storage:
            self.blob_storage.save_summary_job(job)
        print(f"[OK] [{user_id[:8]}...] AI summary completed: {job_id[:8]}...")
    
    def _get_existing_transcripts(self, transcript_job_ids: List[str], user_id: str) -> List[Dict]:
        """Get existing transcripts from blob storage"""
        transcripts = []
        
        if self.blob_storage:
            for job_id in transcript_job_ids:
                try:
                    job = self.blob_storage.find_transcription_job(job_id)
                    if job and job.user_id == user_id and job.transcript_text:
                        safe_text = job.transcript_text.encode("utf-8", errors="replace").decode("utf-8")
                        transcripts.append({
                            'source': f"Previous transcript: {job.original_filename}",
                            'content': safe_text,
                            'type': 'transcript'
                        })
                except Exception as e:
                    print(f"Error getting transcript {job_id[:8]}...: {e}")
        
        return transcripts
    
    def _process_uploaded_file(self, file, _user_id: str, is_transcript: bool = False) -> Optional[Dict]:
        """Process uploaded file with transcript marking"""
        try:
            if hasattr(file, 'name'):
                file_path = file.name
                filename = os.path.basename(file_path)
            elif isinstance(file, str):
                file_path = file
                filename = os.path.basename(file_path)
            else:
                return None
            
            if not os.path.exists(file_path):
                return None
            
            file_size = os.path.getsize(file_path)
            if file_size > 200 * 1024 * 1024:
                print(f"File too large: {file_size} bytes (max: 200MB)")
                return None
            
            ext = filename.split('.')[-1].lower() if '.' in filename else ''
            
            # Video files
            if ext in ['mp4', 'mov', 'avi', 'mkv', 'webm', 'flv', '3gp', 'wmv']:
                return {
                    'type': 'video',
                    'filename': filename,
                    'path': file_path,
                    'extension': ext,
                    'size': file_size
                }
            
            # Image files
            elif ext in ['jpg', 'jpeg', 'png', 'bmp', 'gif', 'tiff', 'webp']:
                return {
                    'type': 'image',
                    'filename': filename,
                    'path': file_path,
                    'extension': ext,
                    'size': file_size
                }
            
            # Text/Document files
            elif ext in ['pdf', 'docx', 'doc', 'pptx', 'ppt', 'xlsx', 'xls', 'txt', 'json', 'csv']:
                content = self.file_processor.process_file(file_path, ext)
                if content:
                    content = content.encode("utf-8", errors="replace").decode("utf-8")
                    content_type = 'transcript' if is_transcript else 'document'
                    
                    return {
                        'type': content_type,
                        'source': filename,
                        'content': content,
                        'filename': filename,
                        'path': file_path,
                        'extension': ext,
                        'size': file_size
                    }
            
            return None
                
        except Exception as e:
            print(f"Error processing file: {e}")
            return None
    
    def _extract_significant_frames(self, video_path: str) -> List[Dict]:
        """Extract significant frames from video"""
        try:
            frames = self.frame_extractor.extract_frames(video_path)
            return frames if frames else []
        except Exception as e:
            print(f"Frame extraction failed: {e}")
            return []
    
    def _analyze_image_content(self, image_path: str) -> Optional[Dict]:
        """Analyze image content with Computer Vision - FIXED for better OCR"""
        if LOCAL_TESTING_MODE and get_mock_ocr is not None:
            print(f"\ud83e\uddea [LOCAL MODE] Using mock OCR for: {os.path.basename(image_path)}")
            mock_text = get_mock_ocr().extract_text_from_image(image_path)
            return {
                'path': image_path, 'filename': os.path.basename(image_path),
                'text': mock_text, 'confidence': 0.95, 'status': 'success (mock)'
            }
        if not self.cv_client:
            print(f"[WARN] Computer Vision not available, skipping image: {image_path}")
            return None
        if not os.path.exists(image_path):
            print(f"[WARN] Image file not found: {image_path}")
            return None
        try:
            extracted_text = self._ocr_extract_text(image_path)
            description, confidence = self._describe_image(image_path)
            if extracted_text.strip() or description:
                return {
                    'extracted_text': extracted_text.strip(), 'description': description,
                    'confidence': confidence, 'has_content': True
                }
            print(f"[WARN] No content extracted from image: {image_path}")
            return None
        except Exception as e:
            print(f"[ERROR] Image analysis failed for {image_path}: {e}")
            return None

    def _ocr_extract_text(self, image_path: str) -> str:
        """Extract text from image using Azure Computer Vision OCR."""
        assert self.cv_client is not None
        try:
            with open(image_path, 'rb') as image_stream:
                ocr_result = self.cv_client.read_in_stream(image_stream, raw=True)
                operation_id = ocr_result.headers["Operation-Location"].split("/")[-1]
                read_result = self._wait_for_ocr_result(operation_id)
                if read_result is not None and read_result.status == OperationStatusCodes.succeeded:
                    lines = []
                    for text_result in read_result.analyze_result.read_results:
                        for line in text_result.lines:
                            lines.append(line.text)
                    extracted = "\n".join(lines) + "\n"
                    print(f"[OK] OCR extracted {len(extracted)} chars from image")
                    return extracted
        except Exception as ocr_error:
            print(f"[WARN] OCR failed for {image_path}: {ocr_error}")
        return ""

    def _wait_for_ocr_result(self, operation_id: str, timeout: int = 30):
        """Poll for OCR completion, return read_result or None on timeout."""
        assert self.cv_client is not None
        start_time = time.time()
        while True:
            if time.time() - start_time > timeout:
                print("[WARN] OCR timeout")
                return None
            read_result = self.cv_client.get_read_result(operation_id)
            if read_result.status not in ['notStarted', 'running']:
                return read_result
            time.sleep(1)

    def _describe_image(self, image_path: str) -> Tuple[str, float]:
        """Get image description using Azure Computer Vision."""
        assert self.cv_client is not None
        try:
            with open(image_path, 'rb') as image_stream:
                result = self.cv_client.describe_image_in_stream(image_stream, max_candidates=3, language='en')
                if result.captions:
                    desc = result.captions[0].text
                    conf = result.captions[0].confidence
                    print(f"[OK] Image description: {desc[:50]}...")
                    return desc, conf
        except Exception as desc_error:
            print(f"[WARN] Description failed for {image_path}: {desc_error}")
        return "", 0
        
    def _generate_ai_summary_with_openai(
        self, 
        transcripts: List[Dict], 
        documents: List[Dict], 
        image_insights: List[Dict],
        user_prompt: str,
        summary_type: str,
        output_language: str = "English",
        settings: Optional[Dict] = None
    ) -> str:
        """Generate AI summary with Azure OpenAI - SUPPORT FILE UPLOAD"""
        try:
            if LOCAL_TESTING_MODE and get_mock_ai is not None:
                return self._mock_summarize(transcripts, documents, summary_type, user_prompt)
            
            print("[AI] Generating AI summary with Azure OpenAI...")
            messages = self._build_openai_messages(
                transcripts, documents, image_insights, user_prompt,
                summary_type, output_language, settings or {}
            )
            
            text_content = messages[-1]["content"] if isinstance(messages[-1]["content"], str) else ""
            final_tokens = self.token_manager.count_tokens(text_content) if text_content else 0
            print(f"[STATS] Estimated input tokens: {final_tokens:,} / {self.token_manager.max_content_tokens:,}")
            
            if text_content and final_tokens > self.token_manager.max_content_tokens:
                print("[WARN] Content still too long, applying emergency truncation")
                text_content = self.token_manager.truncate_text(text_content, self.token_manager.max_content_tokens)
                messages[-1] = {"role": "user", "content": text_content}
            
            response = self._call_openai_api(messages)
            return self._parse_openai_response(response, output_language)
            
        except (AISummaryError,):
            raise
        except Exception as e:
            error_msg = f"Azure OpenAI generation failed: {str(e)}"
            print(error_msg)
            raise SummaryProcessingError(error_msg) from e

    def _mock_summarize(self, transcripts: List[Dict], documents: List[Dict],
                        summary_type: str, user_prompt: str) -> str:
        """Generate mock summary for local testing."""
        assert get_mock_ai is not None
        print("\ud83e\uddea [LOCAL MODE] Using mock AI summary service")
        all_content = ""
        for t in transcripts:
            all_content += t.get('content', '') + "\n\n"
        for d in documents:
            all_content += d.get('content', '') + "\n\n"
        return get_mock_ai().summarize(all_content, summary_type, user_prompt)

    def _build_openai_messages(self, transcripts: List[Dict], documents: List[Dict],
                                image_insights: List[Dict], user_prompt: str,
                                summary_type: str, output_language: str, settings: Dict) -> List[Dict]:
        """Build the messages array for the OpenAI API request."""
        system_prompt = self._create_system_prompt(summary_type, output_language, settings)
        text_content = self._prepare_text_content_simple(
            transcripts, documents, image_insights, user_prompt, summary_type
        )
        user_content = [{"type": "text", "text": text_content}]
        self._attach_pdf_documents(documents, user_content)
        user_msg_content = user_content if len(user_content) > 1 else text_content
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg_content}
        ]

    def _attach_pdf_documents(self, documents: List[Dict], user_content: List[Dict]):
        """Try to attach PDF documents as base64 for direct upload."""
        for doc in documents:
            if doc.get('extension') != 'pdf' or 'path' not in doc:
                continue
            if not os.path.exists(doc['path']):
                continue
            try:
                with open(doc['path'], 'rb') as f:
                    pdf_data = base64.b64encode(f.read()).decode('utf-8')
                user_content.append({
                    "type": "document",
                    "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_data}
                })
                print(f"   [DOC] Uploaded PDF directly: {doc['filename']}")
            except Exception as e:
                print(f"   [WARN] Could not upload file, using extracted text: {e}")

    def _call_openai_api(self, messages: List[Dict]) -> requests.Response:
        """Make the OpenAI API call with retries."""
        url = (f"{self.azure_openai_endpoint}/openai/deployments/"
               f"{self.azure_openai_deployment}/chat/completions"
               f"?api-version={self.azure_openai_api_version}")
        headers = {"Content-Type": "application/json", "api-key": self.azure_openai_key}
        data = {
            "messages": messages, "max_tokens": self.token_manager.max_completion_tokens,
            "temperature": 0.1, "top_p": 0.95, "frequency_penalty": 0, "presence_penalty": 0
        }
        print(f"\ud83d\ude80 Making API request to: {self.azure_openai_deployment}")
        
        max_retries = 3
        last_error: Optional[Exception] = None
        for attempt in range(max_retries):
            response, err = self._single_api_attempt(url, headers, data, attempt, max_retries)
            if response is not None:
                return response
            last_error = err
        raise last_error or APIRequestError("No response received after all retries")

    def _single_api_attempt(self, url: str, headers: Dict, data: Dict,
                            attempt: int, max_retries: int) -> Tuple[Optional[requests.Response], Optional[Exception]]:
        """Execute a single API call attempt; return (response, None) on success or (None, error)."""
        try:
            response = requests.post(url, headers=headers, json=data, timeout=config.AI_PROCESSING_TIMEOUT)
            print(f"\ud83d\udce1 API Response Status: {response.status_code}")
            if response.status_code == 200:
                return response, None
            if response.status_code == 429:
                time.sleep(2 ** attempt)
                return None, APIRequestError(f"Rate limited: {response.status_code}")
            error_msg = f"Azure OpenAI API error: {response.status_code} - {response.text}"
            if attempt == max_retries - 1:
                return None, APIRequestError(error_msg)
            time.sleep(1)
            return None, APIRequestError(error_msg)
        except requests.exceptions.Timeout:
            err = APIRequestError("Azure OpenAI request timed out")
            if attempt < max_retries - 1:
                print(f"\u23f3 Request timeout, retrying... (attempt {attempt + 1})")
                time.sleep(2)
            return None, err
        except requests.exceptions.RequestException as e:
            err = APIRequestError(f"Azure OpenAI request failed: {str(e)}")
            if attempt < max_retries - 1:
                print(f"[ERROR] Request error, retrying... (attempt {attempt + 1}): {e}")
                time.sleep(2)
            return None, err

    def _parse_openai_response(self, response: requests.Response, output_language: str) -> str:
        """Parse and validate the OpenAI API response."""
        try:
            result = response.json()
        except json.JSONDecodeError as e:
            raise APIRequestError(f"Invalid JSON response: {str(e)}")
        
        if 'choices' not in result or len(result['choices']) == 0:
            raise APIRequestError(f"No response from Azure OpenAI: {result}")
        
        choice = result['choices'][0]
        if 'message' not in choice or 'content' not in choice['message']:
            raise APIRequestError(f"Unexpected response format: {result}")
        
        ai_response = choice['message']['content']
        self._log_token_usage(result)
        
        finish_reason = choice.get('finish_reason', '')
        if finish_reason == 'content_filter':
            raise ContentFilterError("Content filtered by Azure OpenAI")
        if finish_reason == 'length':
            print("[WARN] Response truncated due to length")
            ai_response += "\n\n[Response was truncated]"
        
        # Sanitize lone surrogates that break .encode('utf-8')
        ai_response = ai_response.encode("utf-8", errors="replace").decode("utf-8")
        print(f"[OK] AI summary generated in {output_language}")
        return ai_response

    @staticmethod
    def _log_token_usage(result: Dict):
        """Log token usage statistics from API response."""
        if 'usage' not in result:
            return
        usage = result['usage']
        print("[STATS] Token usage:")
        print(f"   - Prompt: {usage.get('prompt_tokens', 0):,}")
        print(f"   - Completion: {usage.get('completion_tokens', 0):,}")
        print(f"   - Total: {usage.get('total_tokens', 0):,}")
    
    def _create_system_prompt(self, summary_type: str, output_language: str, settings: Dict) -> str:
        """Create optimized system prompt with detailed Thai templates for meetings and events"""
        
        # Determine output language
        lang = "ไทย" if output_language == "Auto-Detect" else output_language
        
        # CORE SYSTEM PROMPT
        core_rules = f"""คุณคือผู้เชี่ยวชาญด้านการจัดทำสรุปการประชุมและเอกสารราชการ/ธุรกิจ ที่มีประสบการณ์สูง

【ขั้นตอนการทำงาน — ปฏิบัติตามลำดับนี้เสมอ】
0. ตรวจแก้เนื้อหาต้นฉบับ: อ่าน User Instructions เพื่อดูชื่อโครงการ หัวข้อ และรายชื่อผู้พูดที่ผู้ใช้ระบุมา จากนั้นใช้ข้อมูลเหล่านี้เพื่อแก้ไขคำผิดในต้นฉบับ เช่น ชื่อบุคคลที่ถูกถอดเสียงผิด คำศัพท์เฉพาะทาง ชื่อระบบ/โปรเจกต์ ชื่อหน่วยงาน ก่อนเริ่มสรุป (แก้เงียบ ๆ ไม่ต้องรายงานสิ่งที่แก้)
1. อ่านเนื้อหาต้นฉบับ (Transcript / เอกสาร) ทั้งหมดอย่างละเอียดตั้งแต่ต้นจนจบ
2. ระบุบุคคลที่เกี่ยวข้อง: รวบรวมรายชื่อผู้พูด ผู้ถูกอ้างถึง ผู้นำเสนอ ผู้รับผิดชอบงาน ห้ามตกหล่น
3. ระบุหัวข้อ/วาระ: จัดกลุ่มเนื้อหาเป็นหัวข้อหลักและหัวข้อย่อย ตามลำดับเวลาหรือหมวดหมู่
4. สกัดมติ/การตัดสินใจ: ระบุทุกข้อสรุป มติที่ประชุม ข้อตกลงร่วมกันอย่างชัดเจน
5. รวบรวม Action Items / Next Steps: ระบุว่าใครต้องทำอะไร เมื่อไหร่ ให้ครบทุกรายการ
6. จัดรูปแบบผลลัพธ์: เขียนตามโครงสร้างที่กำหนดในรูปแบบด้านล่าง

【หลักการสำคัญ】
• ภาษา: {lang} (ใช้ภาษาราชการ/ธุรกิจที่สุภาพ สละสลวย เป็นทางการ)
• อ้างอิงข้อมูลจากเนื้อหาต้นฉบับเท่านั้น — ห้ามเดาหรือสร้างข้อมูลที่ไม่มีอยู่จริง
• หากไม่มีข้อมูลในหัวข้อใด ระบุ "ไม่ปรากฏข้อมูล" หรือ "-"
• แทนที่ชื่อจริงด้วยรูปแบบ {{ยศ/ตำแหน่ง ชื่อ-นามสกุล}} เสมอ
• ห้ามตกหล่นบุคคลที่มีส่วนร่วมหรือถูกอ้างถึง
• เขียนกระชับ ชัดเจน ไม่เยิ่นเย้อ แต่ครอบคลุมทุกเนื้อหา ไม่ตัดทอนสาระสำคัญ
• ทุกหัวข้อต้องมีเนื้อหา ห้ามเว้นว่าง
• อย่าละทิ้งรายละเอียดสำคัญ เช่น ชื่อโปรเจกต์ ชื่อระบบ เครื่องมือ ตัวเลขสถิติ งบประมาณ กำหนดเวลา ข้อมูลเชิงเทคนิค
• หากมีหลายเรื่องที่คุยในเนื้อหา ให้จัดหมวดหมู่แยกแต่ละเรื่องให้ชัดเจน
• ตัวเลข วันที่ จำนวนเงิน สถิติ ต้องระบุตรงตามต้นฉบับ ห้ามปัดเศษหรือประมาณ
• ใช้ข้อมูลบริบทจาก User Instructions (ชื่อโครงการ หัวข้อ รายชื่อผู้พูด) เพื่อแก้ไขการถอดเสียงที่ผิดพลาดให้ถูกต้อง

"""

        # FORMAT-SPECIFIC INSTRUCTIONS
        if summary_type == "บทสรุปสำหรับผู้บริหาร":
            format_instructions = """【รูปแบบ: บทสรุปสำหรับผู้บริหาร】

กรุณาเขียนผลลัพธ์แบ่งเป็น 2 ส่วน:

ส่วนที่ 1: การแบ่งช่วงเวลา (สรุปสาระสำคัญเป็นช่วง ๆ ครอบคลุมทุกส่วนของเวลา)

ช่วงการประชุม {ชื่อหัวข้อเรื่อง} - วันที่ :
ช่วงที่ 1: ...
ช่วงที่ 2: ...
(เพิ่มได้ตามจริง)

ส่วนที่ 2: สรุปสำหรับผู้บริหาร

## 📋 สรุปภาพรวม
(สรุปสาระสำคัญ 2-3 ประโยค ระบุวัตถุประสงค์และผลลัพธ์หลัก)

## มติ/การตัดสินใจสำคัญ
1. [มติที่ 1]
2. [มติที่ 2]
(หากไม่มี ระบุ "-")

## 📌 รายการสิ่งที่ต้องดำเนินการ (Action Items)
| ลำดับ | รายการ | ผู้รับผิดชอบ | กำหนดเสร็จ |
|:---:|--------|:--------:|:--------:|
| 1 | [งาน] | [ชื่อ/-] | [วันที่/-] |

## ประเด็นที่ต้องติดตาม
• [รายการ หรือ "-" หากไม่มี]
"""

        elif summary_type == "รายงานการประชุมภายใน":
            format_instructions = """【รูปแบบ: รายงานการประชุมภายใน】

ช่วยวิเคราะห์เนื้อหาประชุมนี้โดยจับประเด็นและเนื้อหาสาระสำคัญแบ่งเป็นช่วงเวลาให้ชัดเจน จากนั้นให้ออกรายงานสรุปการประชุมให้กระชับ ครอบคลุม โดยจัดกลุ่มตามประเภท และใช้รูปแบบด้านล่างนี้อย่างเคร่งครัด:

เงื่อนไขสำคัญ:
- จัดการข้อมูลบุคคลและการมีส่วนร่วม: ประมวลผลรายชื่อผู้เข้าร่วมประชุม บทบาทหน้าที่ ข้อเสนอแนะ หรือการตัดสินใจสำคัญของแต่ละบุคคลจากเนื้อหาจริง แต่เวลาเขียนลงในรายงาน ต้องแทนที่ชื่อจริงด้วยรูปแบบ {ยศ/ตำแหน่ง ชื่อ-นามสกุล} เสมอ ห้ามตกหล่นบุคคลที่มีส่วนร่วมหรือถูกอ้างถึง
- จัดกลุ่มและสรุปประเด็นวาระ: จำนวนหัวข้อหลัก (ข้อใหญ่) ไม่ควรน้อยกว่า 4 และไม่เกิน 8 ข้อ โดยแต่ละข้อใหญ่ต้องมีข้อย่อยตามเนื้อหาจริง ไม่บังคับจำนวนข้อย่อยเท่ากันทุกข้อใหญ่ และต้องลงเนื้อหาครบถ้วน บังคับให้แบ่งตามประเภทหมวดหมู่ให้ชัดเจน หากมีการตัดสินใจ (Decision) หรือข้อตกลงร่วมกัน ให้ระบุให้ชัดเจนในแต่ละหัวข้อ
- รวบรวม Next Steps: ให้ระบุชัดเจนว่าใครทำอะไรบ้าง แม้เนื้อหาจะกระจัดกระจายอยู่ใน transcript โดยจัดกลุ่มตามตัวบุคคลผู้รับผิดชอบและแยกย่อยเป็นงานหลัก-งานย่อย
- อย่าละทิ้งรายละเอียดสำคัญ เช่น ชื่อโปรเจกต์ ชื่อระบบ เครื่องมือที่ใช้ หรือตัวเลขที่ถูกกล่าวถึงในที่ประชุม
- ใช้ข้อมูลจาก User Instructions (ชื่อโครงการ หัวข้อ รายชื่อผู้พูด) เพื่อแก้ไขการถอดเสียงที่ผิดพลาดในต้นฉบับให้ถูกต้องก่อนเริ่มสรุป

กรุณาเขียนผลลัพธ์แบ่งเป็น 2 ส่วนตามรูปแบบด้านล่างนี้เท่านั้น:

ส่วนที่ 1: การแบ่งช่วงเวลาการประชุม (สรุปสาระสำคัญเป็นช่วง ๆ ให้ได้เนื้อหาและครอบคลุมทุกส่วนของเวลา)

ช่วงการประชุม {ชื่อหัวข้อเรื่อง} - วันที่ :
ช่วงที่ 1: ...
ช่วงที่ 2: ...
ช่วงที่ 3: ...
ช่วงที่ 4: ...
(เพิ่มได้ตามจริง)

ส่วนที่ 2: รายงานสรุปประชุม

สรุปประชุม {ชื่อหัวข้อเรื่อง} - วันที่

ประเด็น (ต้องมี 4-8 ข้อใหญ่ แต่ละข้อมีข้อย่อยตามเนื้อหาจริง ไม่บังคับจำนวนเท่ากัน)

1. {ชื่อหัวข้อ}
   1.1 ...
   1.2 ...
   1.3 ...
   (เพิ่มข้อย่อยได้ตามเนื้อหาจริง)

2. {ชื่อหัวข้อ}
   2.1 ...
   2.2 ...
   (เพิ่มข้อย่อยได้ตามเนื้อหาจริง)

3. {ชื่อหัวข้อ}
   3.1 ...
   3.2 ...
   (เพิ่มข้อย่อยได้ตามเนื้อหาจริง)

4. {ชื่อหัวข้อ}
   4.1 ...
   4.2 ...
   (เพิ่มข้อย่อยได้ตามเนื้อหาจริง)

... (เพิ่มข้อใหญ่ได้ถึง 8 ข้อ ตามเนื้อหาการประชุม)

Next Steps

1. ผู้รับผิดชอบ: {ยศ/ตำแหน่ง ชื่อ-นามสกุล} - {ระบุหัวข้องานหลัก}
   1.1 {ระบุหัวข้องานย่อย}
   1.2 {ระบุหัวข้องานย่อย}
   1.3 {ระบุหัวข้องานย่อย}
   (เพิ่มได้ตามจริง)

2. ผู้รับผิดชอบ: {ยศ/ตำแหน่ง ชื่อ-นามสกุล} - {ระบุหัวข้องานหลัก}
   2.1 {ระบุหัวข้องานย่อย}
   2.2 {ระบุหัวข้องานย่อย}
   (เพิ่มได้ตามจริง)

...
"""

        elif summary_type == "รายงานการประชุมภายนอก":
            format_instructions = """【รูปแบบ: รายงานการประชุมภายนอก (ทางการ)】

ช่วยวิเคราะห์เนื้อหาประชุมนี้โดยจับประเด็นและเนื้อหาสาระสำคัญแบ่งเป็นช่วงเวลาให้ชัดเจน จากนั้นให้ออกรายงานสรุปการประชุมให้กระชับ ครอบคลุม โดยจัดกลุ่มตามประเภท และใช้รูปแบบด้านล่างนี้อย่างเคร่งครัด:

เงื่อนไขสำคัญ:
- จัดการข้อมูลบุคคลและการมีส่วนร่วม: ประมวลผลรายชื่อผู้เข้าร่วมประชุม บทบาทหน้าที่ ข้อเสนอแนะ หรือการตัดสินใจสำคัญของแต่ละบุคคลจากเนื้อหาจริง แต่เวลาเขียนลงในรายงาน ต้องแทนที่ชื่อจริงด้วยรูปแบบ {ยศ/ตำแหน่ง ชื่อ-นามสกุล} เสมอ ห้ามตกหล่นบุคคลที่มีส่วนร่วมหรือถูกอ้างถึง
- จัดกลุ่มและสรุปประเด็นวาระ: จำนวนหัวข้อหลัก (ข้อใหญ่) ไม่ควรน้อยกว่า 4 และไม่เกิน 8 ข้อ โดยแต่ละข้อใหญ่ต้องมีข้อย่อยตามเนื้อหาจริง ไม่บังคับจำนวนข้อย่อยเท่ากันทุกข้อใหญ่ และต้องลงเนื้อหาครบถ้วน บังคับให้แบ่งตามประเภทหมวดหมู่ให้ชัดเจน หากมีการตัดสินใจ (Decision) หรือข้อตกลงร่วมกัน ให้ระบุให้ชัดเจนในแต่ละหัวข้อ
- รวบรวม Next Steps: ให้ระบุชัดเจนว่าใครทำอะไรบ้าง แม้เนื้อหาจะกระจัดกระจายอยู่ใน transcript โดยจัดกลุ่มตามตัวบุคคลผู้รับผิดชอบและแยกย่อยเป็นงานหลัก-งานย่อย
- อย่าละทิ้งรายละเอียดสำคัญ เช่น ชื่อโปรเจกต์ ชื่อระบบ เครื่องมือที่ใช้ หรือตัวเลขที่ถูกกล่าวถึงในที่ประชุม
- ใช้ข้อมูลจาก User Instructions (ชื่อโครงการ หัวข้อ รายชื่อผู้พูด) เพื่อแก้ไขการถอดเสียงที่ผิดพลาดในต้นฉบับให้ถูกต้องก่อนเริ่มสรุป

กรุณาเขียนผลลัพธ์แบ่งเป็น 2 ส่วนตามรูปแบบด้านล่างนี้เท่านั้น:

ส่วนที่ 1: การแบ่งช่วงเวลาการประชุม (สรุปสาระสำคัญเป็นช่วง ๆ ให้ได้เนื้อหาและครอบคลุมทุกส่วนของเวลา)

ช่วงการประชุม {ชื่อหัวข้อเรื่อง} - วันที่ :
ช่วงที่ 1: ...
ช่วงที่ 2: ...
ช่วงที่ 3: ...
ช่วงที่ 4: ...
(เพิ่มได้ตามจริง)

ส่วนที่ 2: รายงานสรุปประชุม

สรุปประชุม {ชื่อหัวข้อเรื่อง} - วันที่

ประเด็น (ต้องมี 4-8 ข้อใหญ่ แต่ละข้อมีข้อย่อยตามเนื้อหาจริง ไม่บังคับจำนวนเท่ากัน)

1. {ชื่อหัวข้อ}
   1.1 ...
   1.2 ...
   1.3 ...
   (เพิ่มข้อย่อยได้ตามเนื้อหาจริง)

2. {ชื่อหัวข้อ}
   2.1 ...
   2.2 ...
   (เพิ่มข้อย่อยได้ตามเนื้อหาจริง)

3. {ชื่อหัวข้อ}
   3.1 ...
   3.2 ...
   (เพิ่มข้อย่อยได้ตามเนื้อหาจริง)

4. {ชื่อหัวข้อ}
   4.1 ...
   4.2 ...
   (เพิ่มข้อย่อยได้ตามเนื้อหาจริง)

... (เพิ่มข้อใหญ่ได้ถึง 8 ข้อ ตามเนื้อหาการประชุม)

Next Steps

1. ผู้รับผิดชอบ: {ยศ/ตำแหน่ง ชื่อ-นามสกุล} - {ระบุหัวข้องานหลัก}
   1.1 {ระบุหัวข้องานย่อย}
   1.2 {ระบุหัวข้องานย่อย}
   (เพิ่มได้ตามจริง)

...
"""

        elif summary_type == "บทสรุปการเรียนรู้หรืองานสัมมนา":
            format_instructions = """【รูปแบบ: สรุปการเรียนรู้/สัมมนา/Online Event】

เงื่อนไขสำคัญ:
- จัดการข้อมูลบทเรียน หัวข้อการสัมมนา รายชื่อผู้บรรยาย (Speakers) ประเด็นสำคัญ รวมถึงแผนงานหรือเรื่องที่จะเกิดขึ้นในอนาคต (Future Topics / Next Steps) อย่างครบถ้วน
- สรุปประเด็นวาระและหัวข้อต่างๆ โดยสามารถเพิ่มหรือลดจำนวนข้อได้ตามเนื้อหาจริง (มีหัวข้อหลักและหัวข้อย่อยได้อิสระ) ขอให้ดึงมาให้ครบถ้วนทุกเนื้อหา โดยบังคับให้แบ่งตามประเภท/หมวดหมู่ให้ชัดเจน หากมีการตัดสินใจ (Decision) หรือข้อตกลงร่วมกัน (Action Items) ให้ระบุให้ชัดเจนในแต่ละหัวข้อ
- รวมแหล่งเรียนรู้ ข้อมูลอ้างอิง ลิงก์ เครื่องมือ หรือเอกสารภายนอกที่เกี่ยวข้องที่ถูกกล่าวถึงใน Event
- ห้ามตกหล่น: อย่าละทิ้งรายละเอียดสำคัญ เช่น ตัวเลขสถิติ ข้อมูลเชิงเทคนิค Use Cases ที่ยกตัวอย่าง ปัญหาที่พบ (Pain Points) หรือคีย์เวิร์ดสำคัญ

กรุณาเขียนผลลัพธ์แบ่งเป็น 2 ส่วนตามรูปแบบด้านล่างนี้เท่านั้น:

ส่วนที่ 1: การแบ่งช่วงเวลา (สรุปสาระสำคัญเป็นช่วง ๆ ให้ได้เนื้อหาและครอบคลุมทุกส่วนของเวลา)

ช่วง Event {ชื่อหัวข้อเรื่อง} - วันที่ :
ช่วงที่ 1: ...
ช่วงที่ 2: ...
ช่วงที่ 3: ...
ช่วงที่ 4: ...
(เพิ่มได้ตามจริง)

ส่วนที่ 2: รายงานสรุปเนื้อหา

สรุป {ชื่อหัวข้อเรื่อง} - วันที่

ประเด็น

1. {ชื่อหัวข้อ}
   1.1 ...
   1.2 ...
   1.3 ...
   (เพิ่มได้ตามจริง)

2. {ชื่อหัวข้อ}
   2.1 ...
   2.2 ...
   (เพิ่มได้ตามจริง)

...

แหล่งอ้างอิงและเครื่องมือที่กล่าวถึง
• [ลิงก์/เครื่องมือ/เอกสาร หรือ "-" หากไม่มี]
"""

        elif summary_type == "custom_format":
            format_instructions = """【รูปแบบ: กำหนดเอง】

ผู้ใช้จะระบุรูปแบบและคำสั่งที่ต้องการใน User Instructions
ปฏิบัติตามคำสั่งของผู้ใช้อย่างเคร่งครัด — ใช้รูปแบบ โครงสร้าง และข้อกำหนดตามที่ระบุ
หากผู้ใช้ไม่ได้ระบุรูปแบบเฉพาะ ให้ใช้โครงสร้างทั่วไปที่เหมาะสมกับเนื้อหา
"""

        elif summary_type == "no_format":
            format_instructions = """【รูปแบบ: ข้อความล้วน (ไม่มีโครงสร้าง)】

สรุปเนื้อหาเป็นย่อหน้าข้อความล้วน ไม่ต้องใช้ตาราง หัวข้อลำดับเลข หรือ Markdown formatting
เขียนเป็นภาษาที่อ่านง่าย สละสลวย ครอบคลุมทุกประเด็นสำคัญ
จัดเรียงตามลำดับเวลาหรือความสำคัญ
"""

        else:  # ทั่วไป
            format_instructions = """【รูปแบบ: สรุปทั่วไป】

## 📋 สรุปสาระสำคัญ
[ภาพรวมเนื้อหา 2-3 ประโยค]

## 📌 ประเด็นหลัก
1. [ประเด็นที่ 1]
2. [ประเด็นที่ 2]
3. [...]
(เพิ่มหรือลดได้ตามเนื้อหาจริง)

## 📝 รายละเอียด
[ข้อมูลเพิ่มเติมที่สำคัญ]

## ข้อสรุป
[สรุปท้าย]

## ➡️ สิ่งที่ต้องดำเนินการต่อ
• [รายการ หรือ "-"]
"""

        # Settings-based additions
        additional = ""
        if settings.get('include_timestamps', False):
            additional += "\n【การแสดงเวลา】 ระบุ [HH:MM] หน้าเนื้อหาที่มีการบันทึกเวลา"
        
        if settings.get('include_action_items', False):
            additional += "\n【เน้น Action Items】 ทำเครื่องหมาย ☐ นำหน้าทุกรายการที่ต้องดำเนินการ"
        
        # Quality check
        quality_rules = """
---
【ตรวจสอบก่อนส่ง — Checklist บังคับ】
✓ อ้างอิงจากต้นฉบับเท่านั้น — ห้ามเดาข้อมูล ห้ามเพิ่มเติมสิ่งที่ไม่มีในต้นฉบับ
✓ แก้ไขคำผิดจาก transcript ตามข้อมูลบริบทของผู้ใช้ (ชื่อโครงการ หัวข้อ รายชื่อผู้พูด) แล้ว
✓ ใช้ภาษาสุภาพ เป็นทางการ สละสลวย เหมาะสมกับเอกสารราชการ/ธุรกิจ
✓ ไม่มีหัวข้อว่าง — ทุกหัวข้อต้องมีเนื้อหา (ถ้าไม่มีข้อมูลให้ระบุ "-")
✓ ครอบคลุมทุกประเด็นสำคัญจากต้นฉบับ ไม่ตกหล่นเนื้อหา
✓ ไม่ตกหล่นบุคคล โปรเจกต์ เครื่องมือ ตัวเลข หรือวันที่ที่ถูกกล่าวถึง
✓ Action Items / Next Steps ระบุผู้รับผิดชอบชัดเจน
✓ ตัวเลข สถิติ จำนวนเงิน วันที่ ตรงตามต้นฉบับ ไม่ปัดเศษ
✓ โครงสร้างผลลัพธ์ตรงตามรูปแบบที่กำหนด
✓ มีหัวข้อหลัก (ข้อใหญ่) 4–8 ข้อ ไม่ขาดไม่เกิน
✓ ข้อย่อยแต่ละข้อมีเนื้อหาครบตามจริง ไม่บังคับจำนวนเท่ากัน"""

        return core_rules + format_instructions + additional + quality_rules
    
    def _prepare_text_content_simple(self, transcripts, documents, image_insights, user_prompt, summary_type):
        """Prepare content for AI - FIXED to prevent hallucination"""
        context_parts = [
            "# Content for Analysis",
            f"**Task**: {summary_type}",
            "", "---", "",
            "## \ud83d\udccb USER CONTEXT (\u0e0a\u0e37\u0e48\u0e2d\u0e42\u0e04\u0e23\u0e07\u0e01\u0e32\u0e23 / \u0e2b\u0e31\u0e27\u0e02\u0e49\u0e2d / \u0e23\u0e32\u0e22\u0e0a\u0e37\u0e48\u0e2d\u0e1c\u0e39\u0e49\u0e1e\u0e39\u0e14)",
            "*\u0e43\u0e0a\u0e49\u0e02\u0e49\u0e2d\u0e21\u0e39\u0e25\u0e14\u0e49\u0e32\u0e19\u0e25\u0e48\u0e32\u0e07\u0e40\u0e1e\u0e37\u0e48\u0e2d\u0e41\u0e01\u0e49\u0e44\u0e02\u0e04\u0e33\u0e1c\u0e34\u0e14\u0e43\u0e19\u0e15\u0e49\u0e19\u0e09\u0e1a\u0e31\u0e1a \u0e40\u0e0a\u0e48\u0e19 \u0e0a\u0e37\u0e48\u0e2d\u0e1a\u0e38\u0e04\u0e04\u0e25 \u0e04\u0e33\u0e28\u0e31\u0e1e\u0e17\u0e4c\u0e40\u0e09\u0e1e\u0e32\u0e30\u0e17\u0e32\u0e07 \u0e0a\u0e37\u0e48\u0e2d\u0e23\u0e30\u0e1a\u0e1a/\u0e42\u0e1b\u0e23\u0e40\u0e08\u0e01\u0e15\u0e4c*",
            "", f"{user_prompt}", "", "---", ""
        ]
        self._add_transcript_section(context_parts, transcripts)
        self._add_document_section(context_parts, documents)
        self._add_image_section(context_parts, image_insights)
        context_parts.extend([
            "", "---", "",
            "**IMPORTANT: Create summary based ONLY on the content above.**",
            "**Do NOT add information that is not present in the source content.**"
        ])
        return "\n".join(context_parts)

    @staticmethod
    def _add_transcript_section(parts: List[str], transcripts: List[Dict]):
        """Build the transcript section of the prompt content."""
        if not transcripts:
            return
        parts.append("## \ud83c\udf99\ufe0f TRANSCRIPT CONTENT (PRIMARY)")
        parts.append("*This is the main content to summarize*")
        parts.append("")
        for i, transcript in enumerate(transcripts, 1):
            content = transcript.get('content', '')
            if not content.strip():
                continue
            source = transcript.get('source', 'Unknown')
            parts.extend([f"### Transcript {i}: {source}", "", content, "", "---", ""])

    @staticmethod
    def _add_document_section(parts: List[str], documents: List[Dict]):
        """Build the supplementary documents section of the prompt content."""
        if not documents:
            return
        parts.append("## [DOC] SUPPLEMENTARY DOCUMENTS")
        parts.append("*Additional context - use only if relevant*")
        parts.append("")
        for i, doc in enumerate(documents, 1):
            content = doc.get('content', '')
            if not content.strip():
                continue
            filename = doc.get('filename', doc.get('source', 'Unknown'))
            if len(content) > 10000:
                content = content[:10000] + "\n[... content truncated ...]"
            parts.extend([f"### Document {i}: {filename}", "", content, "", "---", ""])

    @staticmethod
    def _add_image_section(parts: List[str], image_insights: List[Dict]):
        """Build the visual content section of the prompt content."""
        valid_images = [img for img in image_insights if img.get('analysis', {}).get('has_content')]
        if not valid_images:
            return
        parts.append("## [VISION] VISUAL CONTENT")
        parts.append("*Text and descriptions extracted from images*")
        parts.append("")
        for i, img in enumerate(valid_images, 1):
            analysis = img.get('analysis', {})
            extracted_text = analysis.get('extracted_text', '').strip()
            description = analysis.get('description', '').strip()
            if not extracted_text and not description:
                continue
            parts.append(f"### Image {i}: {img.get('source', 'Unknown')}")
            if extracted_text:
                parts.extend(["**Text found in image:**", extracted_text])
            if description:
                parts.append(f"**Image description:** {description}")
            parts.append("")
    
    def get_summary_status(self, job_id: str) -> Optional[SummaryJob]:
        """Get summary job status from blob storage"""
        if self.blob_storage:
            return self.blob_storage.find_summary_job(job_id)
        return None
    
    def get_user_summary_history(self, user_id: str, limit: int = 20) -> List[SummaryJob]:
        """Get user summary history from blob storage"""
        if self.blob_storage:
            return self.blob_storage.get_user_summary_history(user_id, limit)
        return []

# Global AI summary manager instance
ai_summary_manager = AISummaryManager()

