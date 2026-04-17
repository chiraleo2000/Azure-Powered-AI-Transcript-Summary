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

DEFAULT_MODEL = "gpt-4.1-mini"

# Load Environment
load_dotenv()

# Check for LOCAL_TESTING_MODE
LOCAL_TESTING_MODE = config.LOCAL_TESTING_MODE

if LOCAL_TESTING_MODE:
    print("🧪 [AI SUMMARY] Local Testing Mode enabled - using mock AI services")
    from local_mock import get_mock_ai, get_mock_ocr  # type: ignore[import-untyped]
else:
    def get_mock_ai():  # type: ignore[misc]
        raise RuntimeError("Mock AI not available outside LOCAL_TESTING_MODE")
    def get_mock_ocr():  # type: ignore[misc]
        raise RuntimeError("Mock OCR not available outside LOCAL_TESTING_MODE")

@dataclass
class SummaryJob:
    job_id: str
    user_id: str
    original_files: List[str]
    summary_type: str
    user_prompt: str
    status: str
    created_at: str
    completed_at: Optional[str] = None
    summary_text: Optional[str] = None
    processed_files: Optional[Dict] = None
    extracted_images: Optional[List[str]] = None
    transcript_text: Optional[str] = None
    error_message: Optional[str] = None
    settings: Optional[Dict] = None
    chat_response_url: Optional[str] = None

class TokenManager:
    """Token counting for gpt-4.1-mini with 128k context - NO CHUNKING"""
    
    def __init__(self, model_name: str = DEFAULT_MODEL):
        try:
            model_encoding_map = {
                DEFAULT_MODEL: "o200k_base", 
            }
            
            encoding_name = model_encoding_map.get(model_name, "cl100k_base")
            self.encoder = tiktoken.get_encoding(encoding_name)
        except Exception as e:
            print(f"Warning: Could not load tokenizer for {model_name}, using fallback: {e}")
            self.encoder = tiktoken.get_encoding("cl100k_base")
        
        # gpt-4.1-mini capacity: 128k input, 16k output
        self.max_input_tokens = 128000
        self.max_completion_tokens = 16000
        
        # Reserve tokens for system/user prompts
        self.system_prompt_tokens = 2000
        self.user_prompt_tokens = 1000
        
        # Calculate available tokens for content
        self.max_content_tokens = (
            self.max_input_tokens - 
            self.system_prompt_tokens - 
            self.user_prompt_tokens - 
            self.max_completion_tokens
        )
        
        print(f"[STATS] Token Manager initialized ({DEFAULT_MODEL} 128k - NO CHUNKING):")
        print(f"   - Max input tokens: {self.max_input_tokens:,}")
        print(f"   - Max content tokens: {self.max_content_tokens:,}")
        print(f"   - Max completion: {self.max_completion_tokens:,}")
    
    def count_tokens(self, text: str) -> int:
        """Count tokens in text"""
        try:
            return len(self.encoder.encode(text))
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
    
    def _submit_pending_jobs(self, pending_summary_jobs, iteration_count: int) -> None:
        """Log status and submit any pending jobs to the executor"""
        if pending_summary_jobs and iteration_count % 6 == 0:
            active_summaries = len([j for j in pending_summary_jobs if j.status == 'processing'])
            queued_summaries = len([j for j in pending_summary_jobs if j.status == 'pending'])
            if active_summaries > 0 or queued_summaries > 0:
                print(f"[AI] AI Summary worker: {active_summaries} processing, {queued_summaries} queued")
        for job in pending_summary_jobs:
            if job.status == 'pending':
                self.executor.submit(self._process_summary_job_background, job.job_id, job.user_id)

    def _background_summary_worker(self):
        """Background worker for AI summary processing"""
        iteration_count = 0
        while self.running:
            try:
                if self.blob_storage:
                    pending_summary_jobs = self.blob_storage.get_pending_summary_jobs()
                    self._submit_pending_jobs(pending_summary_jobs, iteration_count)
                
                time.sleep(10)
                iteration_count += 1
                
            except Exception as e:
                print(f"[ERROR] AI Summary background worker error: {e}")
                time.sleep(30)
    
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
            self.blob_storage.save_summary_job(job)  # type: ignore[arg-type]
        
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
    
    def _load_transcripts_into_content(self, job, transcript_job_ids: Optional[List[str]],
                                        user_id: str, processed_content: Dict) -> Optional[List[str]]:
        """Load all transcript sources (direct text, job IDs from args, job IDs from settings)"""
        if job.settings and 'direct_transcript' in job.settings:
            transcript_text = job.settings['direct_transcript']
            print(f"📝 Using direct transcript content: {len(transcript_text)} chars")
            processed_content['transcripts'].append({
                'source': 'Direct Text Input',
                'content': transcript_text,
                'type': 'transcript'
            })

        if transcript_job_ids:
            existing = self._get_existing_transcripts(transcript_job_ids, user_id)
            processed_content['transcripts'].extend(existing)
            print(f"📝 Loaded {len(existing)} existing transcripts")

        if not transcript_job_ids and job.settings and 'transcript_job_ids' in job.settings:
            transcript_job_ids = job.settings['transcript_job_ids'] or []
            existing = self._get_existing_transcripts(transcript_job_ids, user_id)
            processed_content['transcripts'].extend(existing)
            print(f"📝 Loaded {len(existing)} transcripts from job settings")

        return transcript_job_ids

    def _detect_is_transcript_file(self, job, filename: str) -> bool:
        """Determine whether a file should be treated as a transcript"""
        if not job.settings:
            return False
        if job.settings.get('source_filename') == filename:
            return True
        if job.settings.get('content_mode') == 'Text Input':
            return True
        return False

    def _add_file_to_content(self, file_content: Dict, filename: str, processed_content: Dict) -> None:
        """Route a processed file into the appropriate content bucket"""
        if file_content['type'] == 'transcript':
            processed_content['transcripts'].append(file_content)
            print(f"📝 Added as TRANSCRIPT: {filename}")
        elif file_content['type'] == 'video':
            frames = self._extract_significant_frames(file_content['path'])
            processed_content['extracted_frames'].extend(frames)
            print(f"🎥 Extracted {len(frames)} frames from video")
        elif file_content['type'] == 'document':
            processed_content['documents'].append(file_content)
            print(f"[DOC] Added as DOCUMENT: {filename}")
        elif file_content['type'] == 'image':
            processed_content['images'].append(file_content)
            print(f"🖼️ Added as IMAGE: {filename}")

    def _classify_uploaded_files(self, files: List, job, user_id: str, processed_content: Dict) -> None:
        """Classify and process each uploaded file into the appropriate content bucket"""
        for i, file in enumerate(files):
            file_path = getattr(file, 'name', file) if hasattr(file, 'name') else str(file)
            filename = os.path.basename(file_path) if file_path else 'unknown'
            print(f"Processing file {i+1}/{len(files)}: {filename}")

            is_transcript_file = self._detect_is_transcript_file(job, filename)
            file_content = self._process_uploaded_file(file, user_id, is_transcript_file)
            if file_content:
                self._add_file_to_content(file_content, filename, processed_content)

    def _collect_image_insights(self, all_images: List[Dict]) -> List[Dict]:
        """Analyze all images and return insight dicts"""
        insights = []
        print(f"Analyzing {len(all_images)} images...")
        for image_info in all_images:
            analysis = self._analyze_image_content(image_info['path'])
            if analysis:
                insights.append({'source': image_info['filename'], 'analysis': analysis})
        return insights

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
                self.blob_storage.save_summary_job(job)  # type: ignore[arg-type]
            
            processed_content: Dict = {'transcripts': [], 'documents': [], 'images': [], 'extracted_frames': []}
            transcript_job_ids = self._load_transcripts_into_content(job, transcript_job_ids, user_id, processed_content)
            if files:
                self._classify_uploaded_files(files, job, user_id, processed_content)

            all_images = processed_content['images'] + processed_content['extracted_frames']
            image_insights = self._collect_image_insights(all_images)

            print("\n[OK] Content Classification Complete:")
            print(f"   📝 Transcripts: {len(processed_content['transcripts'])} items")
            print(f"   [DOC] Documents: {len(processed_content['documents'])} items")
            print(f"   🖼️ Images: {len(image_insights)} items")

            optimized_transcripts, optimized_documents, optimized_images = self.token_manager.optimize_content_for_tokens(
                processed_content['transcripts'],
                processed_content['documents'],
                image_insights,
                job.user_prompt
            )

            output_language = job.settings.get('output_language', 'English') if job.settings else 'English'
            summary_result = self._generate_ai_summary_with_openai(
                transcripts=optimized_transcripts,
                documents=optimized_documents,
                image_insights=optimized_images,
                user_prompt=job.user_prompt,
                summary_type=job.summary_type,
                output_language=output_language,
                settings=job.settings or {}
            )

            chat_url = ""
            if self.blob_storage:
                try:
                    chat_url = self.blob_storage.upload_summary_result(summary_result, user_id, job_id)
                    print(f"💬 Summary stored successfully: {chat_url}")
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
                self.blob_storage.save_summary_job(job)  # type: ignore[arg-type]
            print(f"[OK] [{user_id[:8]}...] AI summary completed: {job_id[:8]}...")
            
        except Exception as e:
            print(f"[ERROR] AI summary processing failed: {e}")
            if job and self.blob_storage:
                job.status = "failed"
                job.error_message = str(e)
                job.completed_at = datetime.now().isoformat()
                self.blob_storage.save_summary_job(job)  # type: ignore[arg-type]
    
    def _get_existing_transcripts(self, transcript_job_ids: List[str], user_id: str) -> List[Dict]:
        """Get existing transcripts from blob storage"""
        transcripts = []
        
        if self.blob_storage:
            for job_id in transcript_job_ids:
                try:
                    job = self.blob_storage.find_transcription_job(job_id)
                    if job and job.user_id == user_id and job.transcript_text:
                        transcripts.append({
                            'source': f"Previous transcript: {job.original_filename}",
                            'content': job.transcript_text,
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
    
    def _ocr_image(self, image_path: str) -> str:
        """Run OCR on an image file and return extracted text"""
        extracted_text = ""
        try:
            with open(image_path, 'rb') as image_stream:
                ocr_result = self.cv_client.read_in_stream(image_stream, raw=True)  # type: ignore[union-attr]
                operation_id = ocr_result.headers["Operation-Location"].split("/")[-1]

                timeout = 30
                start_time = time.time()
                read_result = None
                while True:
                    if time.time() - start_time > timeout:
                        print(f"[WARN] OCR timeout for: {image_path}")
                        break
                    read_result = self.cv_client.get_read_result(operation_id)  # type: ignore[union-attr]
                    if read_result.status not in ['notStarted', 'running']:
                        break
                    time.sleep(1)

                if read_result is not None and read_result.status == OperationStatusCodes.succeeded:
                    for text_result in read_result.analyze_result.read_results:
                        for line in text_result.lines:
                            extracted_text += line.text + "\n"
                    print(f"[OK] OCR extracted {len(extracted_text)} chars from image")
        except Exception as ocr_error:
            print(f"[WARN] OCR failed for {image_path}: {ocr_error}")
        return extracted_text

    def _describe_image(self, image_path: str):
        """Return (description, confidence) from Computer Vision describe API"""
        description = ""
        confidence = 0.0
        try:
            with open(image_path, 'rb') as image_stream:
                description_result = self.cv_client.describe_image_in_stream(  # type: ignore[union-attr]
                    image_stream, max_candidates=3, language='en'
                )
                if description_result.captions:
                    description = description_result.captions[0].text
                    confidence = description_result.captions[0].confidence
                    print(f"[OK] Image description: {description[:50]}...")
        except Exception as desc_error:
            print(f"[WARN] Description failed for {image_path}: {desc_error}")
        return description, confidence

    def _analyze_image_content(self, image_path: str) -> Optional[Dict]:
        """Analyze image content with Computer Vision - FIXED for better OCR"""
        if LOCAL_TESTING_MODE:
            print(f"🧪 [LOCAL MODE] Using mock OCR for: {os.path.basename(image_path)}")
            mock_text = get_mock_ocr().extract_text_from_image(image_path)
            return {
                'path': image_path,
                'filename': os.path.basename(image_path),
                'text': mock_text,
                'confidence': 0.95,
                'status': 'success (mock)'
            }
        
        if not self.cv_client:
            print(f"[WARN] Computer Vision not available, skipping image: {image_path}")
            return None
        
        if not os.path.exists(image_path):
            print(f"[WARN] Image file not found: {image_path}")
            return None
        
        try:
            extracted_text = self._ocr_image(image_path)
            description, confidence = self._describe_image(image_path)
            
            if extracted_text.strip() or description:
                return {
                    'extracted_text': extracted_text.strip(),
                    'description': description,
                    'confidence': confidence,
                    'has_content': True
                }
            print(f"[WARN] No content extracted from image: {image_path}")
            return None
                
        except Exception as e:
            print(f"[ERROR] Image analysis failed for {image_path}: {e}")
            return None
        
    def _attach_document_files(self, documents: List[Dict], user_content: List) -> None:
        """Add PDF documents as base64-encoded attachments to user_content in-place"""
        for doc in documents:
            if not ('path' in doc and os.path.exists(doc['path'])):
                continue
            try:
                if doc.get('extension') == 'pdf':
                    with open(doc['path'], 'rb') as f:
                        pdf_data = base64.b64encode(f.read()).decode('utf-8')
                    user_content.append({
                        "type": "document",
                        "source": {"type": "base64", "media_type": "application/pdf", "data": pdf_data}
                    })
                    print(f"   [DOC] Uploaded PDF directly: {doc['filename']}")
            except Exception as e:
                print(f"   [WARN] Could not upload file, using extracted text: {e}")

    def _check_request_attempt(self, response, attempt: int, max_retries: int) -> bool:
        """Inspect HTTP status; return True to accept, False to retry, raise on final failure"""
        if response.status_code == 200:
            return True
        if response.status_code == 429:
            time.sleep(2 ** attempt)
            return False
        error_msg = f"Azure OpenAI API error: {response.status_code} - {response.text}"
        print(error_msg)
        if attempt == max_retries - 1:
            raise APIRequestError(error_msg)
        time.sleep(1)
        return False

    def _make_openai_request(self, url: str, headers: Dict, data: Dict) -> object:
        """POST to Azure OpenAI with retry logic; returns the Response object"""
        max_retries = 3
        response = None
        for attempt in range(max_retries):
            try:
                response = requests.post(url, headers=headers, json=data, timeout=config.AI_PROCESSING_TIMEOUT)
                print(f"📡 API Response Status: {response.status_code}")
                if self._check_request_attempt(response, attempt, max_retries):
                    break
            except requests.exceptions.Timeout:
                if attempt == max_retries - 1:
                    raise APIRequestError("Azure OpenAI request timed out")
                print(f"⏳ Request timeout, retrying... (attempt {attempt + 1})")
                time.sleep(2)
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    raise APIRequestError(f"Azure OpenAI request failed: {str(e)}")
                print(f"[ERROR] Request error, retrying... (attempt {attempt + 1}): {e}")
                time.sleep(2)
        if response is None:
            raise APIRequestError("No response received from Azure OpenAI")
        return response

    def _extract_ai_response(self, result: Dict, output_language: str) -> str:
        """Parse OpenAI JSON result dict; return the AI text or raise on errors"""
        if not ('choices' in result and len(result['choices']) > 0):
            raise APIRequestError(f"No response from Azure OpenAI: {result}")
        choice = result['choices'][0]
        if not ('message' in choice and 'content' in choice['message']):
            raise APIRequestError(f"Unexpected response format: {result}")
        ai_response = choice['message']['content']
        if 'usage' in result:
            usage = result['usage']
            print("[STATS] Token usage:")
            print(f"   - Prompt: {usage.get('prompt_tokens', 0):,}")
            print(f"   - Completion: {usage.get('completion_tokens', 0):,}")
            print(f"   - Total: {usage.get('total_tokens', 0):,}")
        finish_reason = choice.get('finish_reason', '')
        if finish_reason == 'content_filter':
            raise ContentFilterError("Content filtered by Azure OpenAI")
        elif finish_reason == 'length':
            print("[WARN] Response truncated due to length")
            ai_response += "\n\n[Response was truncated]"
        print(f"[OK] AI summary generated in {output_language}")
        return ai_response

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
            if LOCAL_TESTING_MODE:
                print("🧪 [LOCAL MODE] Using mock AI summary service")
                all_content = "".join(t.get('content', '') + "\n\n" for t in transcripts)
                all_content += "".join(d.get('content', '') + "\n\n" for d in documents)
                return get_mock_ai().summarize(all_content, summary_type, user_prompt)
            
            print("[AI] Generating AI summary with Azure OpenAI...")
            system_prompt = self._create_system_prompt(summary_type, output_language, settings or {})
            messages: List[Dict] = [{"role": "system", "content": system_prompt}]
            
            text_content = self._prepare_text_content_simple(
                transcripts, documents, image_insights, user_prompt, summary_type
            )
            user_content: List = [{"type": "text", "text": text_content}]
            if documents:
                self._attach_document_files(documents, user_content)
            messages.append({"role": "user", "content": user_content if len(user_content) > 1 else text_content})
            
            final_tokens = self.token_manager.count_tokens(text_content)
            print(f"[STATS] Estimated input tokens: {final_tokens:,} / {self.token_manager.max_content_tokens:,}")
            if final_tokens > self.token_manager.max_content_tokens:
                print("[WARN] Content still too long, applying emergency truncation")
                text_content = self.token_manager.truncate_text(text_content, self.token_manager.max_content_tokens)
                messages[-1] = {"role": "user", "content": text_content}
            
            url = f"{self.azure_openai_endpoint}/openai/deployments/{self.azure_openai_deployment}/chat/completions?api-version={self.azure_openai_api_version}"
            headers = {"Content-Type": "application/json", "api-key": self.azure_openai_key}
            data = {
                "messages": messages,
                "max_tokens": self.token_manager.max_completion_tokens,
                "temperature": 0.1,
                "top_p": 0.95,
                "frequency_penalty": 0,
                "presence_penalty": 0
            }
            print(f"🚀 Making API request to: {self.azure_openai_deployment}")
            
            raw_response = self._make_openai_request(url, headers, data)
            try:
                result = raw_response.json()  # type: ignore[union-attr]
            except json.JSONDecodeError as e:
                raise APIRequestError(f"Invalid JSON response: {str(e)}")
            
            return self._extract_ai_response(result, output_language)
            
        except (AISummaryError,):
            raise
        except Exception as e:
            error_msg = f"Azure OpenAI generation failed: {str(e)}"
            print(error_msg)
            raise SummaryProcessingError(error_msg) from e
    
    def _create_system_prompt(self, summary_type: str, output_language: str, settings: Dict) -> str:
        """Create optimized system prompt with detailed Thai templates for meetings and events"""
        
        # Determine output language
        lang = "ไทย" if output_language == "Auto-Detect" else output_language
        
        # CORE SYSTEM PROMPT
        core_rules = f"""คุณคือผู้เชี่ยวชาญด้านการจัดทำสรุปการประชุมและเอกสารราชการ/ธุรกิจ ที่มีประสบการณ์สูง

【ขั้นตอนการทำงาน — ปฏิบัติตามลำดับนี้เสมอ】
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

เงื่อนไขสำคัญ:
- จัดการข้อมูลบุคคลและการมีส่วนร่วม: ประมวลผลรายชื่อผู้เข้าร่วมประชุม บทบาทหน้าที่ ข้อเสนอแนะ หรือการตัดสินใจสำคัญของแต่ละบุคคลจากเนื้อหาจริง แต่เวลาเขียนลงในรายงาน ต้องแทนที่ชื่อจริงด้วยรูปแบบ {ยศ/ตำแหน่ง ชื่อ-นามสกุล} เสมอ ห้ามตกหล่นบุคคลที่มีส่วนร่วมหรือถูกอ้างถึง
- จัดกลุ่มและสรุปประเด็นวาระ: หัวข้อสามารถเพิ่มหรือลดได้ตามเนื้อหาจริง (มีหัวข้อหลักและหัวข้อย่อยได้อิสระ) ขอให้ดึงมาให้ครบถ้วนทุกเนื้อหา โดยบังคับให้แบ่งตามประเภทหมวดหมู่ให้ชัดเจน หากมีการตัดสินใจ (Decision) หรือข้อตกลงร่วมกัน ให้ระบุให้ชัดเจนในแต่ละหัวข้อ
- รวบรวม Next Steps: ให้ระบุชัดเจนว่าใครทำอะไรบ้าง แม้เนื้อหาจะกระจัดกระจายอยู่ใน transcript โดยจัดกลุ่มตามตัวบุคคลผู้รับผิดชอบและแยกย่อยเป็นงานหลัก-งานย่อย
- อย่าละทิ้งรายละเอียดสำคัญ เช่น ชื่อโปรเจกต์ ชื่อระบบ เครื่องมือที่ใช้ หรือตัวเลขที่ถูกกล่าวถึงในที่ประชุม

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

3. {ชื่อหัวข้อ}
   3.1 ...
   3.2 ...
   (เพิ่มได้ตามจริง)

...

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

เงื่อนไขสำคัญ:
- จัดการข้อมูลบุคคลและการมีส่วนร่วม: ประมวลผลรายชื่อผู้เข้าร่วมประชุม บทบาทหน้าที่ ข้อเสนอแนะ หรือการตัดสินใจสำคัญของแต่ละบุคคลจากเนื้อหาจริง แต่เวลาเขียนลงในรายงาน ต้องแทนที่ชื่อจริงด้วยรูปแบบ {ยศ/ตำแหน่ง ชื่อ-นามสกุล} เสมอ ห้ามตกหล่นบุคคลที่มีส่วนร่วมหรือถูกอ้างถึง
- จัดกลุ่มและสรุปประเด็นวาระ: หัวข้อสามารถเพิ่มหรือลดได้ตามเนื้อหาจริง (มีหัวข้อหลักและหัวข้อย่อยได้อิสระ) ขอให้ดึงมาให้ครบถ้วนทุกเนื้อหา โดยบังคับให้แบ่งตามประเภทหมวดหมู่ให้ชัดเจน หากมีการตัดสินใจ (Decision) หรือข้อตกลงร่วมกัน ให้ระบุให้ชัดเจนในแต่ละหัวข้อ
- รวบรวม Next Steps: ให้ระบุชัดเจนว่าใครทำอะไรบ้าง แม้เนื้อหาจะกระจัดกระจายอยู่ใน transcript โดยจัดกลุ่มตามตัวบุคคลผู้รับผิดชอบและแยกย่อยเป็นงานหลัก-งานย่อย
- อย่าละทิ้งรายละเอียดสำคัญ เช่น ชื่อโปรเจกต์ ชื่อระบบ เครื่องมือที่ใช้ หรือตัวเลขที่ถูกกล่าวถึงในที่ประชุม

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
✓ ใช้ภาษาสุภาพ เป็นทางการ สละสลวย เหมาะสมกับเอกสารราชการ/ธุรกิจ
✓ ไม่มีหัวข้อว่าง — ทุกหัวข้อต้องมีเนื้อหา (ถ้าไม่มีข้อมูลให้ระบุ "-")
✓ ครอบคลุมทุกประเด็นสำคัญจากต้นฉบับ ไม่ตกหล่นเนื้อหา
✓ ไม่ตกหล่นบุคคล โปรเจกต์ เครื่องมือ ตัวเลข หรือวันที่ที่ถูกกล่าวถึง
✓ Action Items / Next Steps ระบุผู้รับผิดชอบชัดเจน
✓ ตัวเลข สถิติ จำนวนเงิน วันที่ ตรงตามต้นฉบับ ไม่ปัดเศษ
✓ โครงสร้างผลลัพธ์ตรงตามรูปแบบที่กำหนด"""

        return core_rules + format_instructions + additional + quality_rules
    
    def _append_transcript_sections(self, context_parts: List, transcripts: List) -> None:
        """Append transcript content blocks to context_parts"""
        if not transcripts:
            return
        context_parts.extend(["## 🎙️ TRANSCRIPT CONTENT (PRIMARY)", "*This is the main content to summarize*", ""])
        for i, transcript in enumerate(transcripts, 1):
            source = transcript.get('source', 'Unknown')
            content = transcript.get('content', '')
            if content.strip():
                context_parts.extend([f"### Transcript {i}: {source}", "", content, "", "---", ""])

    def _append_document_sections(self, context_parts: List, documents: List) -> None:
        """Append document content blocks to context_parts"""
        if not documents:
            return
        context_parts.extend(["## [DOC] SUPPLEMENTARY DOCUMENTS", "*Additional context - use only if relevant*", ""])
        for i, doc in enumerate(documents, 1):
            filename = doc.get('filename', doc.get('source', 'Unknown'))
            content = doc.get('content', '')
            if not content.strip():
                continue
            if len(content) > 10000:
                content = content[:10000] + "\n[... content truncated ...]"
            context_parts.extend([f"### Document {i}: {filename}", "", content, "", "---", ""])

    def _append_image_sections(self, context_parts: List, image_insights: List) -> None:
        """Append image analysis blocks to context_parts"""
        valid_images = [img for img in image_insights if img.get('analysis', {}).get('has_content')]
        if not valid_images:
            return
        context_parts.extend(["## [VISION] VISUAL CONTENT", "*Text and descriptions extracted from images*", ""])
        for i, img in enumerate(valid_images, 1):
            analysis = img.get('analysis', {})
            source = img.get('source', 'Unknown')
            extracted_text = analysis.get('extracted_text', '').strip()
            description = analysis.get('description', '').strip()
            if not (extracted_text or description):
                continue
            context_parts.append(f"### Image {i}: {source}")
            if extracted_text:
                context_parts.extend(["**Text found in image:**", extracted_text])
            if description:
                context_parts.append(f"**Image description:** {description}")
            context_parts.append("")

    def _prepare_text_content_simple(self, transcripts, documents, image_insights, user_prompt, summary_type):
        """Prepare content for AI - FIXED to prevent hallucination"""
        context_parts = [
            "# Content for Analysis",
            f"**Task**: {summary_type}",
            f"**User Instructions**: {user_prompt}",
            "",
            "---",
            ""
        ]
        
        self._append_transcript_sections(context_parts, transcripts)
        self._append_document_sections(context_parts, documents)
        self._append_image_sections(context_parts, image_insights)
        
        context_parts.extend([
            "",
            "---",
            "",
            "**IMPORTANT: Create summary based ONLY on the content above.**",
            "**Do NOT add information that is not present in the source content.**"
        ])
        
        return "\n".join(context_parts)
    
    def get_summary_status(self, job_id: str) -> Optional["SummaryJob"]:
        """Get summary job status from blob storage"""
        if self.blob_storage:
            return self.blob_storage.find_summary_job(job_id)  # type: ignore[return-value]
        return None
    
    def get_user_summary_history(self, user_id: str, limit: int = 20) -> List["SummaryJob"]:
        """Get user summary history from blob storage"""
        if self.blob_storage:
            return self.blob_storage.get_user_summary_history(user_id, limit)  # type: ignore[return-value]
        return []

# Global AI summary manager instance
ai_summary_manager = AISummaryManager()

