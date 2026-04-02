import os
import time
import uuid
import json
import requests
import subprocess
import threading
import hashlib
import re
from datetime import datetime, timedelta
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, asdict
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv
from azure.storage.blob import BlobServiceClient, BlobClient
import tempfile


class TranscriptionError(Exception):
    """Base exception for transcription operations."""


class AudioConversionError(TranscriptionError):
    """Raised when audio format conversion fails."""


class StorageError(TranscriptionError):
    """Raised when blob storage operations fail."""


class SpeechServiceError(TranscriptionError):
    """Raised when Azure Speech Service is unavailable or fails."""

# Load Environment
load_dotenv()

# Import secure configuration (loads secrets from Key Vault)
import config

# Import error logger
try:
    from error_logger import log_error
except ImportError:
    # Fallback if error_logger not available
    def log_error(source, error_type, message, _details=""):
        print(f"[ERROR] [{source}] {error_type}: {message}")

# Check for LOCAL_TESTING_MODE
LOCAL_TESTING_MODE = config.LOCAL_TESTING_MODE

SOURCE_GPT4O_TRANSCRIBE = "GPT-4o Transcribe"

if LOCAL_TESTING_MODE:
    print("=" * 80)
    print("[TEST] LOCAL TESTING MODE ENABLED")
    print("=" * 80)
    print("[OK] All Azure services will be mocked")
    print("[OK] No Azure API calls will be made")
    print("[OK] Data will be stored locally in ./local_storage")
    print("=" * 80)
    from local_mock import (
        get_mock_storage, get_mock_transcription, 
        get_mock_ai, get_mock_ocr, is_local_testing_mode
    )

def _require_setting(setting_name: str, value: Optional[str]) -> str:
    if not value or value.strip() == "" or "your" in value.lower():
        raise ValueError(f"Configuration value {setting_name} is missing or invalid. Check your .env file.")
    return value

# Environment variables
# Azure Speech Service - get from config which handles Key Vault
from config import (
    AZURE_SPEECH_KEY, 
    AZURE_SPEECH_KEY_BACKUP,
    AZURE_SPEECH_KEY_ENDPOINT,
    AZURE_SPEECH_KEY_ENDPOINT_BACKUP,
    AZURE_REGION,
    AZURE_REGION_BACKUP,
    AZURE_BLOB_CONNECTION as CONFIG_AZURE_BLOB_CONNECTION,
    AZURE_STORAGE_ACCOUNT_NAME as CONFIG_AZURE_STORAGE_ACCOUNT_NAME,
    API_VERSION as CONFIG_API_VERSION,
    AZURE_CONTAINER as CONFIG_TRANSCRIPTS_CONTAINER,
    CHAT_RESPONSES_CONTAINER as CONFIG_CHAT_RESPONSES_CONTAINER,
    USER_PASSWORD_CONTAINER as CONFIG_USER_PASSWORD_CONTAINER,
    META_DATA_CONTAINER as CONFIG_META_DATA_CONTAINER,
    TRANSCRIPTS_SAS_TOKEN as CONFIG_TRANSCRIPTS_SAS_TOKEN,
    CHAT_RESPONSES_SAS_TOKEN as CONFIG_CHAT_RESPONSES_SAS_TOKEN,
    USER_PASSWORD_SAS_TOKEN as CONFIG_USER_PASSWORD_SAS_TOKEN,
    META_DATA_SAS_TOKEN as CONFIG_META_DATA_SAS_TOKEN,
    ALLOWED_LANGS as CONFIG_ALLOWED_LANGS,
    AZURE_OPENAI_ENDPOINT as CONFIG_AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_KEY as CONFIG_AZURE_OPENAI_KEY,
    AZURE_OPENAI_DEPLOYMENT as CONFIG_AZURE_OPENAI_DEPLOYMENT,
    AZURE_OPENAI_API_VERSION as CONFIG_AZURE_OPENAI_API_VERSION,
    GPT4O_TRANSCRIBE_ENDPOINT as CONFIG_GPT4O_TRANSCRIBE_ENDPOINT,
    GPT4O_TRANSCRIBE_API_KEY as CONFIG_GPT4O_TRANSCRIBE_API_KEY,
    GPT4O_TRANSCRIBE_API_VERSION as CONFIG_GPT4O_TRANSCRIBE_API_VERSION,
)

AZURE_BLOB_CONNECTION = _require_setting("AZURE_BLOB_CONNECTION", CONFIG_AZURE_BLOB_CONNECTION)
AZURE_STORAGE_ACCOUNT_NAME = _require_setting("AZURE_STORAGE_ACCOUNT_NAME", CONFIG_AZURE_STORAGE_ACCOUNT_NAME)

API_VERSION = _require_setting("API_VERSION", CONFIG_API_VERSION)

# Containers
TRANSCRIPTS_CONTAINER = _require_setting("AZURE_CONTAINER", CONFIG_TRANSCRIPTS_CONTAINER)
CHAT_RESPONSES_CONTAINER = _require_setting("CHAT_RESPONSES_CONTAINER", CONFIG_CHAT_RESPONSES_CONTAINER)
USER_PASSWORD_CONTAINER = _require_setting("USER_PASSWORD_CONTAINER", CONFIG_USER_PASSWORD_CONTAINER)
META_DATA_CONTAINER = _require_setting("META_DATA_CONTAINER", CONFIG_META_DATA_CONTAINER)

# Container-specific SAS Tokens
TRANSCRIPTS_SAS_TOKEN = CONFIG_TRANSCRIPTS_SAS_TOKEN
CHAT_RESPONSES_SAS_TOKEN = CONFIG_CHAT_RESPONSES_SAS_TOKEN
USER_PASSWORD_SAS_TOKEN = CONFIG_USER_PASSWORD_SAS_TOKEN
META_DATA_SAS_TOKEN = CONFIG_META_DATA_SAS_TOKEN

ALLOWED_LANGS = json.loads(CONFIG_ALLOWED_LANGS)
AUDIO_FORMATS = ["wav", "mp3", "ogg", "opus", "flac", "wma", "aac", "alaw", "mulaw", "amr", "webm", "speex"]

# Azure OpenAI Configuration for LLM Correction
AZURE_OPENAI_ENDPOINT = CONFIG_AZURE_OPENAI_ENDPOINT.rstrip('/')
AZURE_OPENAI_KEY = CONFIG_AZURE_OPENAI_KEY
AZURE_OPENAI_DEPLOYMENT = CONFIG_AZURE_OPENAI_DEPLOYMENT
AZURE_OPENAI_API_VERSION = CONFIG_AZURE_OPENAI_API_VERSION

# GPT-4o Transcribe Diarize Configuration
GPT4O_TRANSCRIBE_ENDPOINT = CONFIG_GPT4O_TRANSCRIBE_ENDPOINT
GPT4O_TRANSCRIBE_API_KEY = CONFIG_GPT4O_TRANSCRIBE_API_KEY
GPT4O_TRANSCRIBE_API_VERSION = CONFIG_GPT4O_TRANSCRIBE_API_VERSION


class GPT4oTranscribeDiarize:
    """Use GPT-4o-transcribe-diarize for enhanced audio transcription
    
    Sends audio files directly to GPT-4o-transcribe-diarize endpoint for:
    - High accuracy transcription (~90%+ vs ~60% from standard STT)
    - Speaker diarization (automatic speaker identification)
    - Better handling of Thai, mixed languages, and technical terms
    """
    
    def __init__(self):
        self.endpoint = GPT4O_TRANSCRIBE_ENDPOINT
        self.api_key = GPT4O_TRANSCRIBE_API_KEY or AZURE_OPENAI_KEY
        self.api_version = GPT4O_TRANSCRIBE_API_VERSION
        self.available = bool(self.endpoint and self.api_key)
        
        if self.available:
            print("[GPT-4o] GPT-4o-transcribe-diarize initialized")
            print(f"   Endpoint: {self.endpoint[:50]}...")
        else:
            print("[WARNING] GPT-4o-transcribe-diarize not available (missing config)")
    
    def transcribe_audio(self, audio_file_path: str, language: str = "th") -> Tuple[str, bool]:
        """Transcribe audio file using GPT-4o-transcribe-diarize
        
        Args:
            audio_file_path: Path to the audio file (WAV, MP3, etc.)
            language: Language code (e.g., 'th', 'en', 'zh')
        
        Returns:
            Tuple of (transcript_text, success_flag)
        """
        if not self.available:
            print("[WARN] GPT-4o-transcribe-diarize unavailable")
            return "", False
        
        if not os.path.exists(audio_file_path):
            print(f"[WARN] Audio file not found: {audio_file_path}")
            return "", False
        
        try:
            file_size = os.path.getsize(audio_file_path)
            print(f"[AI] Sending audio to GPT-4o-transcribe-diarize ({file_size / 1024 / 1024:.2f} MB)...")
            print("[WARN] WARNING: Using GPT-4o-transcribe-diarize with chunking_strategy=auto and diarization_enabled=true")
            
            # Build request URL with API version
            url = f"{self.endpoint}?api-version={self.api_version}"
            
            # Prepare headers - Use Authorization Bearer header
            headers = {
                "Authorization": f"Bearer {self.api_key}"
            }
            
            # Get language code
            lang_map = {
                "th-TH": "th", "th": "th",
                "en-US": "en", "en-GB": "en", "en": "en",
                "zh-CN": "zh", "zh": "zh",
                "ja-JP": "ja", "ja": "ja"
            }
            lang_code = lang_map.get(language, language.split("-")[0] if "-" in language else language)
            
            # Prepare multipart form data
            with open(audio_file_path, 'rb') as audio_file:
                # Determine content type based on file extension
                ext = os.path.splitext(audio_file_path)[1].lower()
                content_types = {
                    '.wav': 'audio/wav',
                    '.mp3': 'audio/mpeg',
                    '.m4a': 'audio/mp4',
                    '.ogg': 'audio/ogg',
                    '.flac': 'audio/flac',
                    '.webm': 'audio/webm'
                }
                content_type = content_types.get(ext, 'audio/wav')
                
                files = {
                    'file': (os.path.basename(audio_file_path), audio_file, content_type)
                }
                
                # Request data matches curl command:
                # -F "model=gpt-4o-transcribe-diarize" 
                # -F "chunking_strategy=auto" 
                # -F "diarization_enabled=true" 
                # -F "response_format=diarized_json"
                data = {
                    'model': 'gpt-4o-transcribe-diarize',
                    'chunking_strategy': 'auto',
                    'diarization_enabled': 'true',
                    'response_format': 'diarized_json',
                    'language': lang_code
                }
                
                print("[FIX] API Request: model=gpt-4o-transcribe-diarize, chunking=auto, diarization=true")
                
                # Make request with longer timeout for large files
                timeout = max(300, file_size // (100 * 1024))  # At least 5 min, scale with file size
                
                response = requests.post(
                    url, 
                    headers=headers, 
                    files=files, 
                    data=data,
                    timeout=timeout
                )
            
            if response.status_code == 200:
                result = response.json()
                
                # Extract transcript from JSON response with speaker diarization
                transcript = self._format_diarized_transcript(result)
                
                if transcript:
                    print(f"[OK] GPT-4o-transcribe-diarize complete: {len(transcript)} chars")
                    return transcript.strip(), True
                else:
                    print("[WARN] GPT-4o-transcribe-diarize returned empty transcript")
                    log_error(
                        source=SOURCE_GPT4O_TRANSCRIBE,
                        error_type="Empty Response",
                        message="Transcription returned empty result",
                        details=f"File: {audio_file_path}"
                    )
                    return "", False
                    
            else:
                error_msg = response.text[:500] if response.text else "Unknown error"
                print(f"[WARN] GPT-4o-transcribe-diarize failed: {response.status_code} - {error_msg}")
                log_error(
                    source=SOURCE_GPT4O_TRANSCRIBE,
                    error_type=f"HTTP {response.status_code}",
                    message="GPT-4o transcription failed",
                    details=f"URL: {url}\nResponse: {error_msg}"
                )
                return "", False
                
        except requests.exceptions.Timeout:
            print("[WARN] GPT-4o-transcribe-diarize timed out")
            log_error(
                source=SOURCE_GPT4O_TRANSCRIBE,
                error_type="Timeout",
                message="Request timed out",
                details=f"File: {audio_file_path}, Size: {file_size/1024/1024:.1f}MB"
            )
            return "", False
        except Exception as e:
            print(f"[WARN] GPT-4o-transcribe-diarize error: {e}")
            log_error(
                source=SOURCE_GPT4O_TRANSCRIBE,
                error_type="Exception",
                message=str(e),
                details=f"File: {audio_file_path}"
            )
            return "", False
    
    def _format_diarized_transcript(self, result: dict) -> str:
        """Format JSON response with speaker labels AND timestamps (Azure STT compatible format)"""
        
        # Check for plain text
        if 'text' in result and not result.get('segments'):
            return result['text']
        
        segments = result.get('segments', [])
        if not segments:
            return result.get('text', '')
        
        formatted_parts = []
        current_speaker = None
        current_text = []
        segment_start_time = None
        
        for segment in segments:
            text = segment.get('text', '').strip()
            if not text:
                continue
            
            speaker = segment.get('speaker', None)
            start_time = segment.get('start', 0)
            
            # If speaker changed, output previous speaker's text with timestamp
            if speaker != current_speaker and current_text:
                timestamp_str = self._format_timestamp(segment_start_time) if segment_start_time is not None else ""
                if current_speaker is not None:
                    speaker_label = f"[{timestamp_str}] [Speaker {current_speaker}]"
                else:
                    speaker_label = f"[{timestamp_str}]"
                formatted_parts.append(f"{speaker_label} {' '.join(current_text)}")
                current_text = []
            
            # Track start time of current speaker segment
            if segment_start_time is None or speaker != current_speaker:
                segment_start_time = start_time
            
            current_speaker = speaker
            current_text.append(text)
        
        # Add remaining text
        if current_text:
            timestamp_str = self._format_timestamp(segment_start_time) if segment_start_time is not None else ""
            if current_speaker is not None:
                speaker_label = f"[{timestamp_str}] [Speaker {current_speaker}]"
            else:
                speaker_label = f"[{timestamp_str}]"
            formatted_parts.append(f"{speaker_label} {' '.join(current_text)}")
        
        return "\n\n".join(formatted_parts) if formatted_parts else result.get('text', '')
    
    def _format_timestamp(self, seconds: float) -> str:
        """Convert seconds to MM:SS format"""
        if seconds is None:
            return "00:00"
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f"{minutes:02d}:{secs:02d}"
    
    def transcribe_audio_bytes(self, audio_bytes: bytes, filename: str, language: str = "th") -> Tuple[str, bool]:
        """Transcribe audio from bytes using GPT-4o-transcribe-diarize
        
        Args:
            audio_bytes: Audio file content as bytes
            filename: Original filename (for extension detection)
            language: Language code
        
        Returns:
            Tuple of (transcript_text, success_flag)
        """
        if not self.available:
            return "", False
        
        # Create temp file
        temp_path = None
        try:
            ext = os.path.splitext(filename)[1] or '.wav'
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                temp_path = f.name
                f.write(audio_bytes)
            
            return self.transcribe_audio(temp_path, language)
            
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass


# Legacy class for backward compatibility (now wraps GPT-4o-transcribe-diarize)
class LLMTranscriptCorrector:
    """Legacy wrapper - now uses GPT-4o-transcribe-diarize for audio-based correction"""
    
    def __init__(self):
        self.gpt4o_transcribe = GPT4oTranscribeDiarize()
        self.available = self.gpt4o_transcribe.available
        
        # Fallback to text-based correction if GPT-4o-transcribe not available
        self.endpoint = AZURE_OPENAI_ENDPOINT
        self.api_key = AZURE_OPENAI_KEY
        self.deployment = AZURE_OPENAI_DEPLOYMENT
        self.api_version = AZURE_OPENAI_API_VERSION
        self.text_correction_available = bool(self.endpoint and self.api_key)
        
        if self.available:
            print("[AI] LLM Transcript Corrector ready (GPT-4o-transcribe-diarize)")
        elif self.text_correction_available:
            print("[AI] LLM Transcript Corrector ready (text-based fallback)")
        else:
            print("[WARN] LLM Transcript Corrector not available")
    
    def correct_transcript(self, transcript_text: str, language: str = "th-TH", 
                          context_hint: str = "") -> Tuple[str, bool]:
        """Text-based correction fallback (when audio file not available)"""
        if not self.text_correction_available:
            return transcript_text, False
        
        if not transcript_text or len(transcript_text.strip()) < 50:
            return transcript_text, False
        
        try:
            lang_names = {
                "th-TH": "ภาษาไทย", "en-US": "English", "en-GB": "English",
                "zh-CN": "中文", "ja-JP": "日本語"
            }
            lang_name = lang_names.get(language, language)
            
            system_prompt = f"""คุณคือผู้เชี่ยวชาญด้านการตรวจแก้คำถอดเสียง ({lang_name})

【หน้าที่】แก้ไขข้อผิดพลาดในบทถอดเสียงจากระบบ Speech-to-Text

【สิ่งที่ต้องแก้ไข】
1. คำพ้องเสียง - เลือกคำที่ถูกต้องตามบริบท
2. ชื่อเฉพาะ คำทับศัพท์ - แก้การสะกด
3. ประโยคขาด/เกิน - เติม/ตัดให้สมบูรณ์
4. เครื่องหมายวรรคตอน

【ห้าม】เพิ่มเนื้อหาใหม่ / แปลภาษา / สรุปความ

【ผลลัพธ์】ส่งคืนเฉพาะบทถอดเสียงที่แก้ไขแล้ว"""

            user_message = f"แก้ไขบทถอดเสียงนี้:\n\n{transcript_text}"
            if context_hint:
                user_message += f"\n\n【บริบท】{context_hint}"
            
            url = f"{self.endpoint}/openai/deployments/{self.deployment}/chat/completions?api-version={self.api_version}"
            
            headers = {"Content-Type": "application/json", "api-key": self.api_key}
            
            data = {
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                "max_tokens": min(16000, max(4000, len(transcript_text) // 2)),
                "temperature": 0.1
            }
            
            response = requests.post(url, headers=headers, json=data, timeout=120)
            
            if response.status_code == 200:
                result = response.json()
                if 'choices' in result and len(result['choices']) > 0:
                    corrected = result['choices'][0]['message']['content']
                    if corrected and len(corrected.strip()) > len(transcript_text) * 0.5:
                        print("[OK] Text-based LLM correction complete")
                        return corrected.strip(), True
            
            return transcript_text, False
                
        except Exception as e:
            print(f"[WARN] LLM text correction error: {e}")
            return transcript_text, False


# Global instances
gpt4o_transcribe = GPT4oTranscribeDiarize()
llm_corrector = LLMTranscriptCorrector()

def _account_url() -> str:
    return f"https://{AZURE_STORAGE_ACCOUNT_NAME}.blob.core.windows.net"

def _container_sas_for(container_name: str) -> str:
    """Get SAS token for specific container"""
    if container_name == TRANSCRIPTS_CONTAINER and TRANSCRIPTS_SAS_TOKEN:
        return TRANSCRIPTS_SAS_TOKEN.lstrip("?")
    if container_name == CHAT_RESPONSES_CONTAINER and CHAT_RESPONSES_SAS_TOKEN:
        return CHAT_RESPONSES_SAS_TOKEN.lstrip("?")
    if container_name == USER_PASSWORD_CONTAINER and USER_PASSWORD_SAS_TOKEN:
        return USER_PASSWORD_SAS_TOKEN.lstrip("?")
    if container_name == META_DATA_CONTAINER and META_DATA_SAS_TOKEN:
        return META_DATA_SAS_TOKEN.lstrip("?")
    return ""

def _get_blob_client(blob_service: BlobServiceClient, container: str, blob: str) -> BlobClient:
    """Get blob client with appropriate SAS token"""
    sas = _container_sas_for(container)
    if sas:
        return BlobClient(account_url=_account_url(), container_name=container, blob_name=blob, credential=sas)
    return blob_service.get_blob_client(container=container, blob=blob)


@dataclass
class User:
    user_id: str
    email: str
    username: str
    password_hash: str
    created_at: str
    last_login: Optional[str] = None
    is_active: bool = True
    gdpr_consent: bool = False
    data_retention_agreed: bool = False
    marketing_consent: bool = False

@dataclass
class TranscriptionJob:
    job_id: str
    user_id: str
    original_filename: str
    audio_url: str
    language: str
    status: str
    created_at: str
    completed_at: Optional[str] = None
    transcript_text: Optional[str] = None
    transcript_url: Optional[str] = None
    error_message: Optional[str] = None
    azure_trans_id: Optional[str] = None
    settings: Optional[Dict] = None

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


class AuthManager:
    """Handle user authentication and PDPA compliance"""
    
    @staticmethod
    def hash_password(password: str) -> str:
        """Hash password using SHA-256 with salt from Key Vault"""
        salt = config.PASSWORD_SALT
        return hashlib.sha256((password + salt).encode()).hexdigest()
    
    @staticmethod
    def hash_reset_token(token: str) -> str:
        """Hash reset token for storage"""
        salt = config.PASSWORD_SALT
        return hashlib.sha256((token + salt + "reset").encode()).hexdigest()
    
    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        """Verify password against hash"""
        return AuthManager.hash_password(password) == password_hash
    
    @staticmethod
    def validate_email(email: str) -> bool:
        """Validate email format"""
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return re.match(pattern, email) is not None
    
    @staticmethod
    def validate_username(username: str) -> bool:
        """Validate username format"""
        pattern = r'^\w{3,30}$'
        return re.match(pattern, username) is not None
    
    @staticmethod
    def validate_password(password: str) -> Tuple[bool, str]:
        """Validate password strength"""
        if len(password) < 8:
            return False, "Password must be at least 8 characters long"
        if not re.search(r'[A-Z]', password):
            return False, "Password must contain at least one uppercase letter"
        if not re.search(r'[a-z]', password):
            return False, "Password must contain at least one lowercase letter"
        if not re.search(r'\d', password):
            return False, "Password must contain at least one number"
        return True, "Password is valid"


# backend.py - Updated BlobStorageManager class

class BlobStorageManager:
    """Manage all data in blob storage - no local database or temp files"""
    
    def __init__(self):
        self.blob_service = BlobServiceClient.from_connection_string(AZURE_BLOB_CONNECTION)
        self.storage_account_name = AZURE_STORAGE_ACCOUNT_NAME
        
        # Container configurations
        self.containers = {
            'transcripts': TRANSCRIPTS_CONTAINER,
            'responses': CHAT_RESPONSES_CONTAINER,
            'users': USER_PASSWORD_CONTAINER,
            'metadata': META_DATA_CONTAINER
        }
        
        self.sas_tokens = {
            TRANSCRIPTS_CONTAINER: TRANSCRIPTS_SAS_TOKEN,
            CHAT_RESPONSES_CONTAINER: CHAT_RESPONSES_SAS_TOKEN,
            USER_PASSWORD_CONTAINER: USER_PASSWORD_SAS_TOKEN,
            META_DATA_CONTAINER: META_DATA_SAS_TOKEN
        }
        
        self._lock = threading.Lock()
        self._ensure_containers_exist()
        
        # Start cleanup worker
        self.running = True
        self.cleanup_thread = threading.Thread(target=self._cleanup_old_data_loop, daemon=True)
        self.cleanup_thread.start()
        
        print("[OK] Blob Storage Manager initialized (Blob-Only)")
    
    def _cleanup_old_data_loop(self):
        """Background loop to cleanup old data (30+ days)"""
        while self.running:
            try:
                time.sleep(86400)  # Run once per day
                print("[CLEAN] Starting automated cleanup of data older than 30 days...")
                self.cleanup_old_data(days=30)
            except Exception as e:
                print(f"[WARN] Cleanup loop error: {e}")
                time.sleep(3600)
    
    def cleanup_old_data(self, days: int = 30):
        """Delete blobs older than specified days from all containers"""
        cutoff_date = datetime.now() - timedelta(days=days)
        deleted_count = 0
        
        for container_name in [TRANSCRIPTS_CONTAINER, CHAT_RESPONSES_CONTAINER, META_DATA_CONTAINER]:
            try:
                container_client = self.blob_service.get_container_client(container_name)
                blobs = container_client.list_blobs()
                
                for blob in blobs:
                    try:
                        if blob.last_modified.replace(tzinfo=None) < cutoff_date:
                            blob_client = self._get_blob_client(container_name, blob.name)
                            blob_client.delete_blob()
                            deleted_count += 1
                    except Exception as e:
                        print(f"[WARN] Error deleting {blob.name}: {e}")
                
            except Exception as e:
                print(f"[WARN] Error cleaning {container_name}: {e}")
        
        print(f"[OK] Cleaned up {deleted_count} items older than {days} days")
        return deleted_count
    
    def _ensure_containers_exist(self):
        """Ensure all required containers exist"""
        for container_name in self.containers.values():
            try:
                container_client = self.blob_service.get_container_client(container_name)
                if not container_client.exists():
                    container_client.create_container()
                    print(f"âœ… Created container: {container_name}")
            except Exception as e:
                print(f"âš ï¸ Container check warning for {container_name}: {e}")
    
    def _get_blob_client(self, container: str, blob_name: str) -> BlobClient:
        """Get blob client with SAS token"""
        sas = self.sas_tokens.get(container, "")
        if sas:
            return BlobClient(
                account_url=f"https://{self.storage_account_name}.blob.core.windows.net",
                container_name=container,
                blob_name=blob_name,
                credential=sas
            )
        return self.blob_service.get_blob_client(container=container, blob=blob_name)
    
    def _get_blob_url(self, container: str, blob_name: str) -> str:
        """Generate blob URL with SAS token"""
        base_url = f"https://{self.storage_account_name}.blob.core.windows.net/{container}/{blob_name}"
        sas = self.sas_tokens.get(container, "")
        return f"{base_url}?{sas}" if sas else base_url
    
    # ==================== USER MANAGEMENT ====================
    
    def save_user(self, user: User) -> bool:
        """Save user credentials to blob storage"""
        try:
            blob_name = f"{user.user_id}.json"
            blob_client = self._get_blob_client(USER_PASSWORD_CONTAINER, blob_name)
            
            user_data = asdict(user)
            user_json = json.dumps(user_data, ensure_ascii=False, indent=2, default=str)
            
            blob_client.upload_blob(user_json.encode('utf-8'), overwrite=True)
            print(f"âœ… User saved to blob: {user.username}")
            return True
        except Exception as e:
            print(f"âŒ Error saving user: {e}")
            return False
    
    def get_user(self, user_id: str) -> Optional[User]:
        """Get user from blob storage"""
        try:
            blob_name = f"{user_id}.json"
            blob_client = self._get_blob_client(USER_PASSWORD_CONTAINER, blob_name)
            
            if not blob_client.exists():
                return None
            
            blob_data = blob_client.download_blob().readall()
            user_dict = json.loads(blob_data.decode('utf-8'))
            return User(**user_dict)
        except Exception as e:
            print(f"âŒ Error loading user: {e}")
            return None
    
    def find_user_by_email(self, email: str) -> Optional[User]:
        """Find user by email in blob storage"""
        try:
            container_client = self.blob_service.get_container_client(USER_PASSWORD_CONTAINER)
            blobs = container_client.list_blobs()
            
            for blob in blobs:
                try:
                    blob_client = self._get_blob_client(USER_PASSWORD_CONTAINER, blob.name)
                    blob_data = blob_client.download_blob().readall()
                    user_dict = json.loads(blob_data.decode('utf-8'))
                    
                    if user_dict.get('email', '').lower() == email.lower():
                        return User(**user_dict)
                except Exception:
                    continue
            return None
        except Exception as e:
            print(f"âŒ Error finding user by email: {e}")
            return None
    
    def find_user_by_username(self, username: str) -> Optional[User]:
        """Find user by username in blob storage"""
        try:
            container_client = self.blob_service.get_container_client(USER_PASSWORD_CONTAINER)
            blobs = container_client.list_blobs()
            
            for blob in blobs:
                try:
                    blob_client = self._get_blob_client(USER_PASSWORD_CONTAINER, blob.name)
                    blob_data = blob_client.download_blob().readall()
                    user_dict = json.loads(blob_data.decode('utf-8'))
                    
                    if user_dict.get('username', '').lower() == username.lower():
                        return User(**user_dict)
                except Exception:
                    continue
            return None
        except Exception as e:
            print(f"âŒ Error finding user by username: {e}")
            return None
    
    def delete_user(self, user_id: str) -> bool:
        """Delete user credentials from blob storage"""
        try:
            blob_name = f"{user_id}.json"
            blob_client = self._get_blob_client(USER_PASSWORD_CONTAINER, blob_name)
            
            if blob_client.exists():
                blob_client.delete_blob()
                print(f"ðŸ—'ï¸ User deleted from blob: {user_id[:8]}...")
                return True
            return False
        except Exception as e:
            print(f"âŒ Error deleting user: {e}")
            return False
    
    # ==================== TRANSCRIPTION JOB MANAGEMENT ====================
    
    def save_transcription_job(self, job: TranscriptionJob) -> bool:
        """Save transcription job metadata to blob storage"""
        try:
            blob_name = f"transcriptions/{job.user_id}/{job.job_id}.json"
            blob_client = self._get_blob_client(META_DATA_CONTAINER, blob_name)
            
            job_data = asdict(job)
            job_json = json.dumps(job_data, ensure_ascii=False, indent=2, default=str)
            
            blob_client.upload_blob(job_json.encode('utf-8'), overwrite=True)
            return True
        except Exception as e:
            print(f"âŒ Error saving transcription job: {e}")
            return False
    
    def get_transcription_job(self, job_id: str, user_id: str) -> Optional[TranscriptionJob]:
        """Get transcription job from blob storage"""
        try:
            blob_name = f"transcriptions/{user_id}/{job_id}.json"
            blob_client = self._get_blob_client(META_DATA_CONTAINER, blob_name)
            
            if not blob_client.exists():
                return None
            
            blob_data = blob_client.download_blob().readall()
            job_dict = json.loads(blob_data.decode('utf-8'))
            return TranscriptionJob(**job_dict)
        except Exception as e:
            print(f"âŒ Error loading transcription job: {e}")
            return None
    
    def find_transcription_job(self, job_id: str) -> Optional[TranscriptionJob]:
        """Find transcription job by ID across all users"""
        try:
            prefix = "transcriptions/"
            container_client = self.blob_service.get_container_client(META_DATA_CONTAINER)
            blobs = container_client.list_blobs(name_starts_with=prefix)
            
            for blob in blobs:
                if job_id in blob.name:
                    try:
                        blob_client = self._get_blob_client(META_DATA_CONTAINER, blob.name)
                        blob_data = blob_client.download_blob().readall()
                        job_dict = json.loads(blob_data.decode('utf-8'))
                        return TranscriptionJob(**job_dict)
                    except Exception:
                        continue
            return None
        except Exception as e:
            print(f"âŒ Error finding transcription job: {e}")
            return None
    
    def get_user_transcription_history(self, user_id: str, limit: int = 50) -> List[TranscriptionJob]:
        """Get user's transcription history from blob storage"""
        try:
            jobs = []
            prefix = f"transcriptions/{user_id}/"
            container_client = self.blob_service.get_container_client(META_DATA_CONTAINER)
            blobs = list(container_client.list_blobs(name_starts_with=prefix))
            
            # Sort by last modified (newest first)
            blobs.sort(key=lambda b: b.last_modified, reverse=True)
            
            for blob in blobs[:limit]:
                try:
                    blob_client = self._get_blob_client(META_DATA_CONTAINER, blob.name)
                    blob_data = blob_client.download_blob().readall()
                    job_dict = json.loads(blob_data.decode('utf-8'))
                    jobs.append(TranscriptionJob(**job_dict))
                except Exception:
                    continue
            
            return jobs
        except Exception as e:
            print(f"âŒ Error loading transcription history: {e}")
            return []
    
    def get_pending_transcription_jobs(self) -> List[TranscriptionJob]:
        """Get all pending transcription jobs"""
        try:
            jobs = []
            prefix = "transcriptions/"
            container_client = self.blob_service.get_container_client(META_DATA_CONTAINER)
            blobs = container_client.list_blobs(name_starts_with=prefix)
            
            for blob in blobs:
                try:
                    blob_client = self._get_blob_client(META_DATA_CONTAINER, blob.name)
                    blob_data = blob_client.download_blob().readall()
                    job_dict = json.loads(blob_data.decode('utf-8'))
                    job = TranscriptionJob(**job_dict)
                    
                    if job.status in ['pending', 'processing']:
                        jobs.append(job)
                except Exception:
                    continue
            
            return jobs
        except Exception as e:
            print(f"âŒ Error getting pending jobs: {e}")
            return []
    
    def upload_audio(self, audio_data: bytes, user_id: str, job_id: str, audio_format: str) -> str:
        """Upload audio file to transcripts container"""
        try:
            blob_name = f"users/{user_id}/audio/{job_id}.{audio_format}"
            blob_client = self._get_blob_client(TRANSCRIPTS_CONTAINER, blob_name)
            
            blob_client.upload_blob(audio_data, overwrite=True)
            
            return self._get_blob_url(TRANSCRIPTS_CONTAINER, blob_name)
        except Exception as e:
            print(f"âŒ Error uploading audio: {e}")
            raise
    
    def upload_transcript_result(self, transcript_text: str, user_id: str, job_id: str, filename: str) -> str:
        """Upload transcript result to transcripts container"""
        try:
            clean_filename = re.sub(r'[^\w\s.-]', '', filename)
            clean_filename = re.sub(r'[-\s]+', '_', clean_filename)
            
            blob_name = f"users/{user_id}/transcripts/{job_id}_{clean_filename}.txt"
            blob_client = self._get_blob_client(TRANSCRIPTS_CONTAINER, blob_name)
            
            blob_client.upload_blob(transcript_text.encode('utf-8'), overwrite=True)
            
            return self._get_blob_url(TRANSCRIPTS_CONTAINER, blob_name)
        except Exception as e:
            print(f"âŒ Error uploading transcript: {e}")
            return ""
    
    # ==================== AI SUMMARY JOB MANAGEMENT ====================
    
    def save_summary_job(self, job: SummaryJob) -> bool:
        """Save AI summary job metadata to blob storage"""
        try:
            blob_name = f"summaries/{job.user_id}/{job.job_id}.json"
            blob_client = self._get_blob_client(META_DATA_CONTAINER, blob_name)
            
            job_data = asdict(job)
            job_json = json.dumps(job_data, ensure_ascii=False, indent=2, default=str)
            
            blob_client.upload_blob(job_json.encode('utf-8'), overwrite=True)
            return True
        except Exception as e:
            print(f"âŒ Error saving summary job: {e}")
            return False
    
    def get_summary_job(self, job_id: str, user_id: str) -> Optional[SummaryJob]:
        """Get AI summary job from blob storage"""
        try:
            blob_name = f"summaries/{user_id}/{job_id}.json"
            blob_client = self._get_blob_client(META_DATA_CONTAINER, blob_name)
            
            if not blob_client.exists():
                return None
            
            blob_data = blob_client.download_blob().readall()
            job_dict = json.loads(blob_data.decode('utf-8'))
            
            # Convert JSON strings back to objects
            if isinstance(job_dict.get('original_files'), str):
                job_dict['original_files'] = json.loads(job_dict['original_files'])
            if isinstance(job_dict.get('settings'), str):
                job_dict['settings'] = json.loads(job_dict['settings'])
            
            return SummaryJob(**job_dict)
        except Exception as e:
            print(f"âŒ Error loading summary job: {e}")
            return None
    
    def find_summary_job(self, job_id: str) -> Optional[SummaryJob]:
        """Find AI summary job by ID across all users"""
        try:
            prefix = "summaries/"
            container_client = self.blob_service.get_container_client(META_DATA_CONTAINER)
            blobs = container_client.list_blobs(name_starts_with=prefix)
            
            for blob in blobs:
                if job_id in blob.name:
                    try:
                        blob_client = self._get_blob_client(META_DATA_CONTAINER, blob.name)
                        blob_data = blob_client.download_blob().readall()
                        job_dict = json.loads(blob_data.decode('utf-8'))
                        
                        # Convert JSON strings
                        if isinstance(job_dict.get('original_files'), str):
                            job_dict['original_files'] = json.loads(job_dict['original_files'])
                        if isinstance(job_dict.get('settings'), str):
                            job_dict['settings'] = json.loads(job_dict['settings'])
                        
                        return SummaryJob(**job_dict)
                    except Exception:
                        continue
            return None
        except Exception as e:
            print(f"âŒ Error finding summary job: {e}")
            return None
    
    def get_user_summary_history(self, user_id: str, limit: int = 50) -> List[SummaryJob]:
        """Get user's AI summary history from blob storage"""
        try:
            jobs = []
            prefix = f"summaries/{user_id}/"
            container_client = self.blob_service.get_container_client(META_DATA_CONTAINER)
            blobs = list(container_client.list_blobs(name_starts_with=prefix))
            
            # Sort by last modified (newest first)
            blobs.sort(key=lambda b: b.last_modified, reverse=True)
            
            for blob in blobs[:limit]:
                try:
                    blob_client = self._get_blob_client(META_DATA_CONTAINER, blob.name)
                    blob_data = blob_client.download_blob().readall()
                    job_dict = json.loads(blob_data.decode('utf-8'))
                    
                    # Convert JSON strings
                    if isinstance(job_dict.get('original_files'), str):
                        job_dict['original_files'] = json.loads(job_dict['original_files'])
                    if isinstance(job_dict.get('settings'), str):
                        job_dict['settings'] = json.loads(job_dict['settings'])
                    
                    jobs.append(SummaryJob(**job_dict))
                except Exception:
                    continue
            
            return jobs
        except Exception as e:
            print(f"âŒ Error loading summary history: {e}")
            return []
    
    def get_pending_summary_jobs(self) -> List[SummaryJob]:
        """Get all pending AI summary jobs"""
        try:
            jobs = []
            prefix = "summaries/"
            container_client = self.blob_service.get_container_client(META_DATA_CONTAINER)
            blobs = container_client.list_blobs(name_starts_with=prefix)
            
            for blob in blobs:
                try:
                    blob_client = self._get_blob_client(META_DATA_CONTAINER, blob.name)
                    blob_data = blob_client.download_blob().readall()
                    job_dict = json.loads(blob_data.decode('utf-8'))
                    
                    # Convert JSON strings
                    if isinstance(job_dict.get('original_files'), str):
                        job_dict['original_files'] = json.loads(job_dict['original_files'])
                    if isinstance(job_dict.get('settings'), str):
                        job_dict['settings'] = json.loads(job_dict['settings'])
                    
                    job = SummaryJob(**job_dict)
                    
                    if job.status in ['pending', 'processing']:
                        jobs.append(job)
                except Exception:
                    continue
            
            return jobs
        except Exception as e:
            print(f"âŒ Error getting pending summary jobs: {e}")
            return []
    
    def upload_summary_result(self, summary_text: str, user_id: str, job_id: str) -> str:
        """Upload AI summary result to responses container"""
        try:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            blob_name = f"users/{user_id}/summaries/summary_{job_id}_{timestamp}.txt"
            blob_client = self._get_blob_client(CHAT_RESPONSES_CONTAINER, blob_name)
            
            blob_client.upload_blob(summary_text.encode('utf-8'), overwrite=True)
            
            return self._get_blob_url(CHAT_RESPONSES_CONTAINER, blob_name)
        except Exception as e:
            print(f"âŒ Error uploading summary: {e}")
            return ""
    
    # ==================== PASSWORD RESET ====================
    
    def create_password_reset_token(self, user_id: str, email: str) -> Optional[str]:
        """Create password reset token in blob storage"""
        try:
            reset_token = str(uuid.uuid4())
            expiry_time = datetime.now().timestamp() + 3600
            
            reset_data = {
                'user_id': user_id,
                'email': email,
                'reset_token': reset_token,
                'created_at': datetime.now().isoformat(),
                'expires_at': datetime.fromtimestamp(expiry_time).isoformat(),
                'expiry_timestamp': expiry_time,
                'used': False
            }
            
            # Store in META_DATA_CONTAINER
            blob_name = f"password_resets/{reset_token}.json"
            blob_client = self._get_blob_client(META_DATA_CONTAINER, blob_name)
            blob_client.upload_blob(
                json.dumps(reset_data, ensure_ascii=False, indent=2).encode('utf-8'),
                overwrite=True
            )
            
            print(f"🔑 Reset token created in blob storage: {user_id[:8]}...")
            return reset_token
        except Exception as e:
            print(f"[ERROR] Error creating reset token: {e}")
            return None
    
    def validate_reset_token(self, reset_token: str) -> Optional[Dict]:
        """Validate password reset token"""
        try:
            blob_name = f"password_resets/{reset_token}.json"
            blob_client = self._get_blob_client(META_DATA_CONTAINER, blob_name)
            
            if not blob_client.exists():
                return None
            
            blob_data = blob_client.download_blob().readall()
            reset_data = json.loads(blob_data.decode('utf-8'))
            
            if reset_data.get('used', False):
                return None
            
            if time.time() > reset_data.get('expiry_timestamp', 0):
                return None
            
            return reset_data
        except Exception as e:
            print(f"Error validating reset token: {e}")
            return None
    
    def mark_reset_token_used(self, reset_token: str) -> bool:
        """Mark reset token as used"""
        try:
            blob_name = f"password_resets/{reset_token}.json"
            blob_client = self._get_blob_client(META_DATA_CONTAINER, blob_name)
            
            if not blob_client.exists():
                return False
            
            blob_data = blob_client.download_blob().readall()
            reset_data = json.loads(blob_data.decode('utf-8'))
            reset_data['used'] = True
            reset_data['used_at'] = datetime.now().isoformat()
            
            blob_client.upload_blob(
                json.dumps(reset_data, ensure_ascii=False, indent=2).encode('utf-8'),
                overwrite=True
            )
            
            return True
        except Exception as e:
            print(f"âŒ Error marking token used: {e}")
            return False
    
    # ==================== STATISTICS ====================
    
    def get_user_stats(self, user_id: str) -> Dict:
        """Get user transcription statistics"""
        try:
            stats = {
                'total_jobs': 0,
                'by_status': {},
                'recent_jobs': 0
            }
            
            prefix = f"transcriptions/{user_id}/"
            container_client = self.blob_service.get_container_client(META_DATA_CONTAINER)
            blobs = list(container_client.list_blobs(name_starts_with=prefix))
            
            stats['total_jobs'] = len(blobs)
            
            week_ago = datetime.now() - timedelta(days=7)
            
            for blob in blobs:
                try:
                    blob_client = self._get_blob_client(META_DATA_CONTAINER, blob.name)
                    blob_data = blob_client.download_blob().readall()
                    job_dict = json.loads(blob_data.decode('utf-8'))
                    
                    status = job_dict.get('status', 'unknown')
                    stats['by_status'][status] = stats['by_status'].get(status, 0) + 1
                    
                    # Check if recent
                    created_at = job_dict.get('created_at', '')
                    if created_at:
                        job_date = datetime.fromisoformat(created_at)
                        if job_date >= week_ago:
                            stats['recent_jobs'] += 1
                except Exception:
                    continue
            
            return stats
        except Exception as e:
            print(f"âŒ Error getting user stats: {e}")
            return {'total_jobs': 0, 'by_status': {}, 'recent_jobs': 0}
    
    def get_user_summary_stats(self, user_id: str) -> Dict:
        """Get user AI summary statistics"""
        try:
            stats = {
                'total_jobs': 0,
                'by_status': {},
                'recent_jobs': 0
            }
            
            prefix = f"summaries/{user_id}/"
            container_client = self.blob_service.get_container_client(META_DATA_CONTAINER)
            blobs = list(container_client.list_blobs(name_starts_with=prefix))
            
            stats['total_jobs'] = len(blobs)
            
            week_ago = datetime.now() - timedelta(days=7)
            
            for blob in blobs:
                try:
                    blob_client = self._get_blob_client(META_DATA_CONTAINER, blob.name)
                    blob_data = blob_client.download_blob().readall()
                    job_dict = json.loads(blob_data.decode('utf-8'))
                    
                    status = job_dict.get('status', 'unknown')
                    stats['by_status'][status] = stats['by_status'].get(status, 0) + 1
                    
                    # Check if recent
                    created_at = job_dict.get('created_at', '')
                    if created_at:
                        job_date = datetime.fromisoformat(created_at)
                        if job_date >= week_ago:
                            stats['recent_jobs'] += 1
                except Exception:
                    continue
            
            return stats
        except Exception as e:
            print(f"âŒ Error getting summary stats: {e}")
            return {'total_jobs': 0, 'by_status': {}, 'recent_jobs': 0}
    
    def export_user_data(self, user_id: str) -> Dict:
        """Export all user data"""
        try:
            export_data = {
                'export_date': datetime.now().isoformat(),
                'export_type': 'comprehensive_blob_storage',
                'user_info': {},
                'transcriptions': [],
                'ai_summaries': [],
                'transcription_statistics': self.get_user_stats(user_id),
                'ai_summary_statistics': self.get_user_summary_stats(user_id)
            }
            
            # Get user info
            user = self.get_user(user_id)
            if user:
                export_data['user_info'] = asdict(user)
            
            # Get transcription jobs
            transcription_jobs = self.get_user_transcription_history(user_id, limit=1000)
            export_data['transcriptions'] = [asdict(job) for job in transcription_jobs]
            
            # Get summary jobs
            summary_jobs = self.get_user_summary_history(user_id, limit=1000)
            export_data['ai_summaries'] = [asdict(job) for job in summary_jobs]
            
            return export_data
        except Exception as e:
            print(f"âŒ Error exporting user data: {e}")
            return {}
    
    def delete_user_all_data(self, user_id: str) -> bool:
        """Delete all user data across all containers"""
        try:
            deleted_count = 0
            
            # Delete from metadata container
            for prefix in [f"transcriptions/{user_id}/", f"summaries/{user_id}/"]:
                try:
                    container_client = self.blob_service.get_container_client(META_DATA_CONTAINER)
                    blobs = container_client.list_blobs(name_starts_with=prefix)
                    for blob in blobs:
                        blob_client = self._get_blob_client(META_DATA_CONTAINER, blob.name)
                        blob_client.delete_blob()
                        deleted_count += 1
                except Exception as e:
                    print(f"âš ï¸ Error cleaning metadata {prefix}: {e}")
            
            # Delete from transcripts container
            try:
                prefix = f"users/{user_id}/"
                container_client = self.blob_service.get_container_client(TRANSCRIPTS_CONTAINER)
                blobs = container_client.list_blobs(name_starts_with=prefix)
                for blob in blobs:
                    blob_client = self._get_blob_client(TRANSCRIPTS_CONTAINER, blob.name)
                    blob_client.delete_blob()
                    deleted_count += 1
            except Exception as e:
                print(f"âš ï¸ Error cleaning transcripts: {e}")
            
            # Delete from responses container
            try:
                prefix = f"users/{user_id}/"
                container_client = self.blob_service.get_container_client(CHAT_RESPONSES_CONTAINER)
                blobs = container_client.list_blobs(name_starts_with=prefix)
                for blob in blobs:
                    blob_client = self._get_blob_client(CHAT_RESPONSES_CONTAINER, blob.name)
                    blob_client.delete_blob()
                    deleted_count += 1
            except Exception as e:
                print(f"âš ï¸ Error cleaning responses: {e}")
            
            # Delete user credentials
            self.delete_user(user_id)
            
            print(f"ðŸ—'ï¸ Deleted {deleted_count} items for user {user_id[:8]}...")
            return True
        except Exception as e:
            print(f"âŒ Error deleting user data: {e}")
            return False


def allowed_file(filename):
    """Check if file extension is supported"""
    if not filename or filename in ["upload.unknown", ""]:
        return True
    
    if '.' not in filename:
        return True
    
    ext = filename.rsplit('.', 1)[1].lower()
    supported_extensions = set(AUDIO_FORMATS) | {
        'mp4', 'mov', 'avi', 'mkv', 'webm', 'm4a', '3gp', 'f4v', 
        'wmv', 'asf', 'rm', 'rmvb', 'flv', 'mpg', 'mpeg', 'mts', 'vob',
        'pdf', 'docx', 'doc', 'pptx', 'ppt', 'xlsx', 'xls', 'csv', 'txt', 'json',
        'jpg', 'jpeg', 'png', 'bmp', 'gif', 'tiff', 'webp'
    }
    
    return ext in supported_extensions

class AudioConverter:
    """Convert audio/video files to WAV format for Azure Speech Service"""
    
    def __init__(self):
        self.target_format = 'wav'
        self.target_sample_rate = 16000  # 16kHz is optimal for speech recognition
        self.target_channels = 1  # Mono
        
    def convert_to_wav(self, input_file: bytes, original_filename: str) -> Tuple[bytes, str]:
        """
        Convert any audio/video file to WAV format using FFmpeg
        
        Args:
            input_file: Input file bytes
            original_filename: Original filename for extension detection
            
        Returns:
            Tuple of (wav_bytes, error_message)
        """
        temp_input = None
        temp_output = None
        
        try:
            # Get input file extension
            input_ext = os.path.splitext(original_filename)[1].lower()
            if not input_ext:
                input_ext = '.tmp'
            
            # Create temporary input file
            with tempfile.NamedTemporaryFile(suffix=input_ext, delete=False) as f:
                temp_input = f.name
                f.write(input_file)
            
            # Create temporary output file
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
                temp_output = f.name
            
            # Build FFmpeg command
            # -i: input file
            # -vn: no video (extract audio only from video files)
            # -acodec pcm_s16le: 16-bit PCM encoding
            # -ar 16000: 16kHz sample rate
            # -ac 1: mono channel
            # -y: overwrite output file
            ffmpeg_cmd = [
                'ffmpeg',
                '-i', temp_input,
                '-vn',  # No video
                '-acodec', 'pcm_s16le',  # 16-bit PCM
                '-ar', str(self.target_sample_rate),  # Sample rate
                '-ac', str(self.target_channels),  # Channels
                '-y',  # Overwrite
                temp_output
            ]
            
            print(f"🔄 Converting {original_filename} to WAV format...")
            
            # Run FFmpeg
            result = subprocess.run(
                ffmpeg_cmd,
                capture_output=True,
                text=True,
                timeout=1800  # 30 minute timeout
            )
            
            if result.returncode != 0:
                error_msg = f"FFmpeg conversion failed: {result.stderr}"
                print(f"[ERROR] {error_msg}")
                return None, error_msg
            
            # Read converted WAV file
            if not os.path.exists(temp_output):
                return None, "Conversion succeeded but output file not found"
            
            with open(temp_output, 'rb') as f:
                wav_bytes = f.read()
            
            file_size = len(wav_bytes)
            print(f"[OK] Converted to WAV: {file_size / 1024 / 1024:.2f} MB")
            
            return wav_bytes, None
            
        except subprocess.TimeoutExpired:
            return None, "Audio conversion timed out (file too large or complex)"
        except Exception as e:
            return None, f"Audio conversion error: {str(e)}"
        finally:
            # Clean up temporary files
            try:
                if temp_input and os.path.exists(temp_input):
                    os.remove(temp_input)
                if temp_output and os.path.exists(temp_output):
                    os.remove(temp_output)
            except Exception:
                pass
    
    def get_audio_info(self, file_bytes: bytes, filename: str) -> Optional[Dict]:
        """Get audio file information using FFprobe"""
        temp_file = None
        try:
            # Create temporary file
            ext = os.path.splitext(filename)[1] or '.tmp'
            with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as f:
                temp_file = f.name
                f.write(file_bytes)
            
            # Run FFprobe
            ffprobe_cmd = [
                'ffprobe',
                '-v', 'error',
                '-show_entries', 'format=duration,size,bit_rate:stream=codec_name,sample_rate,channels',
                '-of', 'json',
                temp_file
            ]
            
            result = subprocess.run(
                ffprobe_cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode == 0:
                import json
                info = json.loads(result.stdout)
                return info
            
            return None
            
        except Exception as e:
            print(f"Error getting audio info: {e}")
            return None
        finally:
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except Exception:
                    pass

class TranscriptionManager:
    """Manage transcription jobs - Blob Storage Only"""
    
    def __init__(self):
        self.blob_storage = BlobStorageManager()
        self.audio_converter = AudioConverter()  # ADD THIS LINE
        self.executor = ThreadPoolExecutor(max_workers=5)
        self._job_status_cache = {}
        
        # Auto-create default admin user if no users exist
        self._ensure_default_admin()
        
        # Start background worker
        self.running = True
        self.worker_thread = threading.Thread(target=self._background_worker, daemon=True)
        self.worker_thread.start()
        
        print("[OK] Transcription Manager initialized (Blob Storage + Audio Conversion)")
    
    def _ensure_default_admin(self):
        """Create default admin user if no users exist"""
        try:
            # Check if any users exist
            container_client = self.blob_storage.blob_service.get_container_client(USER_PASSWORD_CONTAINER)
            blobs = list(container_client.list_blobs(name_starts_with="user_"))
            
            if not blobs or len(blobs) == 0:
                # Create default admin user
                print("[ADMIN] No users found, creating default admin user...")
                user_id = str(uuid.uuid4())
                password_hash = AuthManager.hash_password("admin123")  # Default password
                
                admin_user = User(
                    user_id=user_id,
                    email="admin@localhost",
                    username="admin",
                    password_hash=password_hash,
                    created_at=datetime.now().isoformat(),
                    gdpr_consent=True,
                    data_retention_agreed=True,
                    marketing_consent=False,
                    is_active=True,
                    last_login=None
                )
                
                self.blob_storage.save_user(admin_user)
                print("[OK] Default admin user created!")
                print("   Username: admin")
                print("   Password: admin123")
                print("   Please change this password after first login!")
        except Exception as e:
            print(f"[WARN] Could not create default admin: {e}")
    
    def _background_worker(self):
        """Background worker to process pending jobs"""
        iteration_count = 0
        while self.running:
            try:
                pending_jobs = self.blob_storage.get_pending_transcription_jobs()
                
                if pending_jobs and iteration_count % 6 == 0:
                    active_jobs = len([j for j in pending_jobs if j.status == 'processing'])
                    queued_jobs = len([j for j in pending_jobs if j.status == 'pending'])
                    if active_jobs > 0 or queued_jobs > 0:
                        print(f"Background worker: {active_jobs} processing, {queued_jobs} queued")
                
                for job in pending_jobs:
                    # IMPORTANT: Only process Azure STT jobs
                    # GPT-4o (LLM) jobs have their own background thread and use 'processing_gpt4o' status
                    # Never submit LLM-flagged jobs to Azure STT
                    is_llm_job = job.settings and job.settings.get('llm_correction', False)
                    
                    if job.status == 'pending' and job.audio_url and not is_llm_job:
                        self.executor.submit(self._submit_to_azure, job.job_id, job.user_id)
                    elif job.status == 'processing' and job.azure_trans_id:
                        self.executor.submit(self._check_transcription_status, job.job_id, job.user_id)
                
                time.sleep(10)
                iteration_count += 1
                
            except Exception as e:
                print(f"âŒ Background worker error: {e}")
                time.sleep(30)
    
    def submit_transcription(self, file_bytes: bytes, original_filename: str, 
                           user_id: str, language: str, settings: Dict) -> str:
        """Submit new transcription job - Direct to GPT-4o if LLM enabled for speed"""
        job_id = str(uuid.uuid4())
        
        if not isinstance(settings, dict):
            settings = {}
        
        # Check if LLM transcription is requested (direct GPT-4o path)
        llm_correction = settings.get('llm_correction', False)
        
        if llm_correction and gpt4o_transcribe.available:
            print("[AI] LLM Transcription enabled - Using DIRECT GPT-4o path (fast!)")
            return self._submit_gpt4o_direct(file_bytes, original_filename, user_id, language, settings, job_id)
        
        # Traditional Azure STT path
        print(f"[MIC] Creating transcription job: {original_filename}")
        
        try:
            # Check file extension
            file_ext = os.path.splitext(original_filename)[1].lower().lstrip('.')
            
            # Determine if conversion is needed
            needs_conversion = file_ext != 'wav'
            
            if needs_conversion:
                print(f"🔄 Converting {file_ext.upper()} to WAV format...")
                
                # Convert to WAV
                wav_bytes, error = self.audio_converter.convert_to_wav(
                    file_bytes, 
                    original_filename
                )
                
                if error:
                    raise AudioConversionError(f"Audio conversion failed: {error}")
                
                if not wav_bytes:
                    raise AudioConversionError("Audio conversion produced empty file")
                
                # Use converted WAV
                processed_bytes = wav_bytes
                audio_format = 'wav'
                print(f"[OK] Conversion complete: {len(wav_bytes) / 1024 / 1024:.2f} MB")
                
                # Store conversion info in settings
                settings['original_format'] = file_ext
                settings['converted_to_wav'] = True
            else:
                # Already WAV, use as-is
                processed_bytes = file_bytes
                audio_format = 'wav'
                settings['converted_to_wav'] = False
                print(f"[OK] Using original WAV file: {len(file_bytes) / 1024 / 1024:.2f} MB")
            
            # Upload to blob storage
            audio_url = self.blob_storage.upload_audio(
                processed_bytes, 
                user_id, 
                job_id, 
                audio_format
            )
            
            # Update settings with audio format
            settings['audio_format'] = audio_format
            
            # Create job
            job = TranscriptionJob(
                job_id=job_id,
                user_id=user_id,
                original_filename=original_filename,
                audio_url=audio_url,
                language=language,
                status="pending",
                created_at=datetime.now().isoformat(),
                settings=settings
            )
            
            self.blob_storage.save_transcription_job(job)
            
            print(f"[MIC] [{user_id[:8]}...] Transcription job created: {job_id[:8]}...")
            return job_id
            
        except Exception as e:
            print(f"[ERROR] Error submitting transcription: {e}")
            raise

    def _submit_gpt4o_direct(self, file_bytes: bytes, original_filename: str,
                            user_id: str, language: str, settings: Dict, job_id: str) -> str:
        """Submit directly to GPT-4o-transcribe-diarize - FAST! No Azure STT delay
        Enforces 50MB file size limit for GPT-4o API."""
        try:
            # Enforce 50MB file size limit for GPT-4o API
            max_size_bytes = 50 * 1024 * 1024  # 50MB
            if len(file_bytes) > max_size_bytes:
                raise AudioConversionError(
                    f"File too large for LLM Transcription: {len(file_bytes) / 1024 / 1024:.1f}MB. "
                    f"Maximum is 50MB. Please use standard transcription for larger files."
                )
            
            print(f"[RUN] [{user_id[:8]}...] Direct GPT-4o submission: {original_filename}")
            
            # Convert to WAV if needed
            file_ext = os.path.splitext(original_filename)[1].lower().lstrip('.')
            
            if file_ext != 'wav':
                print(f"🔄 Converting {file_ext.upper()} to WAV...")
                wav_bytes, error = self.audio_converter.convert_to_wav(file_bytes, original_filename)
                if error or not wav_bytes:
                    raise AudioConversionError(f"Conversion failed: {error}")
                processed_bytes = wav_bytes
                audio_format = 'wav'
                settings['converted_to_wav'] = True
            else:
                processed_bytes = file_bytes
                audio_format = 'wav'
                settings['converted_to_wav'] = False
            
            # Upload audio to blob
            audio_url = self.blob_storage.upload_audio(processed_bytes, user_id, job_id, audio_format)
            settings['audio_format'] = audio_format
            settings['transcription_method'] = 'gpt-4o-transcribe-diarize-direct'
            
            # Create job with processing_gpt4o status
            job = TranscriptionJob(
                job_id=job_id,
                user_id=user_id,
                original_filename=original_filename,
                audio_url=audio_url,
                language=language,
                status="processing_gpt4o",
                created_at=datetime.now().isoformat(),
                settings=settings
            )
            
            self.blob_storage.save_transcription_job(job)
            
            # Process with GPT-4o in background thread
            threading.Thread(
                target=self._process_gpt4o_transcription,
                args=(job_id, user_id, processed_bytes, original_filename, language),
                daemon=True
            ).start()
            
            print(f"[OK] [{user_id[:8]}...] GPT-4o processing started: {job_id[:8]}...")
            return job_id
            
        except Exception as e:
            print(f"[ERROR] Error in direct GPT-4o submission: {e}")
            raise
    
    def _process_gpt4o_transcription(self, job_id: str, user_id: str, 
                                     audio_bytes: bytes, filename: str, language: str):
        """Process transcription with GPT-4o in background"""
        try:
            job = self.blob_storage.get_transcription_job(job_id, user_id)
            if not job:
                return
            
            print(f"[AI] [{user_id[:8]}...] Starting GPT-4o transcription...")
            
            # Save to temp file
            ext = job.settings.get('audio_format', 'wav')
            with tempfile.NamedTemporaryFile(suffix=f'.{ext}', delete=False) as tmp:
                tmp.write(audio_bytes)
                temp_path = tmp.name
            
            try:
                # Call GPT-4o
                transcript, success = gpt4o_transcribe.transcribe_audio(
                    audio_file_path=temp_path,
                    language=language
                )
                
                if success and transcript:
                    # Save transcript
                    transcript_url = self.blob_storage.upload_transcript_result(
                        transcript, user_id, job_id,
                        os.path.splitext(filename)[0] + "_transcript"
                    )
                    
                    job.status = "completed"
                    job.transcript_text = transcript
                    job.transcript_url = transcript_url
                    job.completed_at = datetime.now().isoformat()
                    job.settings['gpt4o_transcript_length'] = len(transcript)
                    job.settings['llm_correction_applied'] = True
                    
                    print(f"[OK] [{user_id[:8]}...] GPT-4o completed: {len(transcript)} chars")
                else:
                    job.status = "failed"
                    job.error_message = "GPT-4o transcription failed"
                    print(f"[ERROR] [{user_id[:8]}...] GPT-4o failed")
                
            finally:
                # Clean up temp file
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass
            
            self.blob_storage.save_transcription_job(job)
            
        except Exception as e:
            print(f"[ERROR] Error in GPT-4o processing: {e}")
            if job:
                job.status = "failed"
                job.error_message = f"Error: {str(e)}"
                self.blob_storage.save_transcription_job(job)


    
    def _submit_to_azure(self, job_id: str, user_id: str):
        """Submit transcription job to Azure Speech Service (always default)"""
        try:
            job = self.blob_storage.get_transcription_job(job_id, user_id)
            if not job or job.status != 'pending':
                return
            
            # Use local mock if in testing mode
            if LOCAL_TESTING_MODE:
                print(f"[TEST] [LOCAL MODE] Using mock transcription for job {job_id[:8]}...")
                
                # Download audio to get content for mock
                audio_bytes = self._download_audio_from_blob(job.audio_url)
                if not audio_bytes:
                    raise StorageError("Failed to download audio from storage")
                
                # Save to temp file for mock service
                ext = job.settings.get('audio_format', 'wav') if job.settings else 'wav'
                with tempfile.NamedTemporaryFile(suffix=f'.{ext}', delete=False) as tmp:
                    tmp.write(audio_bytes)
                    temp_path = tmp.name
                
                try:
                    # Use mock transcription
                    transcript, success = get_mock_transcription().transcribe_audio(
                        temp_path, 
                        job.language
                    )
                    
                    if success and transcript:
                        # Save transcript
                        transcript_url = self.blob_storage.upload_transcript_result(
                            transcript, user_id, job_id,
                            os.path.splitext(job.original_filename)[0] + "_transcript"
                        )
                        
                        job.status = "completed"
                        job.transcript_text = transcript
                        job.transcript_url = transcript_url
                        job.completed_at = datetime.now().isoformat()
                        if not job.settings:
                            job.settings = {}
                        job.settings['mock_transcript_length'] = len(transcript)
                        job.settings['transcription_method'] = 'local-mock'
                        
                        print(f"[OK] [LOCAL MODE] Mock transcription completed: {len(transcript)} chars")
                    else:
                        raise TranscriptionError("Mock transcription failed")
                finally:
                    if os.path.exists(temp_path):
                        try:
                            os.remove(temp_path)
                        except OSError:
                            pass
                
                self.blob_storage.save_transcription_job(job)
                return
            
            print(f"[{user_id[:8]}...] Submitting to Azure STT: {job.original_filename}")
            
            # Determine which speech key to use (prefer primary if available)
            if AZURE_SPEECH_KEY:
                speech_key = AZURE_SPEECH_KEY
                speech_endpoint = AZURE_SPEECH_KEY_ENDPOINT.rstrip('/')
                print("[INFO] Using PRIMARY Azure Speech Service")
            elif AZURE_SPEECH_KEY_BACKUP:
                speech_key = AZURE_SPEECH_KEY_BACKUP
                speech_endpoint = AZURE_SPEECH_KEY_ENDPOINT_BACKUP.rstrip('/')
                print("[INFO] Using BACKUP Azure Speech Service (no primary available)")
            else:
                raise SpeechServiceError("No Azure Speech Service key available!")
            
            url = f"{speech_endpoint}/speechtotext/{API_VERSION}/transcriptions"
            headers = {
                "Ocp-Apim-Subscription-Key": speech_key,
                "Content-Type": "application/json"
            }
            
            settings = job.settings if job.settings else {}
            
            timestamps_enabled = settings.get('timestamps', False)
            diarization_enabled = settings.get('diarization_enabled', False)
            
            properties = {
                "wordLevelTimestampsEnabled": timestamps_enabled,
                "profanityFilterMode": settings.get('profanity', 'Masked').capitalize(),
                "punctuationMode": settings.get('punctuation', 'Automatic').capitalize(),
            }
            
            if diarization_enabled:
                speakers = settings.get('speakers', 2)
                properties["diarizationEnabled"] = True
                properties["diarization"] = {
                    "speakers": {
                        "minCount": 1,
                        "maxCount": speakers
                    }
                }
            
            data = {
                "contentUrls": [job.audio_url],
                "locale": job.language,
                "displayName": f"Transcription_{job_id[:8]}",
                "properties": properties
            }
            
            response = requests.post(url, headers=headers, json=data, timeout=30)
            
            # If primary fails with 401 and backup is available, try backup
            if response.status_code == 401 and AZURE_SPEECH_KEY_BACKUP and AZURE_SPEECH_KEY and speech_key == AZURE_SPEECH_KEY:
                print("[WARN] Primary speech service returned 401, trying backup service...")
                speech_key = AZURE_SPEECH_KEY_BACKUP
                speech_endpoint = AZURE_SPEECH_KEY_ENDPOINT_BACKUP.rstrip('/')
                url = f"{speech_endpoint}/speechtotext/{API_VERSION}/transcriptions"
                headers["Ocp-Apim-Subscription-Key"] = speech_key
                response = requests.post(url, headers=headers, json=data, timeout=30)
                
                if response.status_code == 201:
                    print("[OK] Backup speech service succeeded!")
            
            if response.status_code == 201:
                result = response.json()
                azure_trans_id = result.get('self', '').split('/')[-1]
                
                job.status = "processing"
                job.azure_trans_id = azure_trans_id
                self.blob_storage.save_transcription_job(job)
                
                print(f"[OK] [{user_id[:8]}...] Azure STT started: {azure_trans_id[:8]}...")
            else:
                error_msg = f"Azure API error: {response.status_code} - {response.text}"
                # Log to error logger for UI display
                log_error(
                    source="Azure STT",
                    error_type=f"HTTP {response.status_code}",
                    message="Failed to submit transcription job",
                    details=f"URL: {url}\nKey Used: {speech_key[:15]}...\nFull Response: {response.text}"
                )
                print(f"[ERROR] Azure STT API Error {response.status_code}:")
                print(f"        URL: {url}")
                print(f"        Key: {speech_key[:15]}...")
                print(f"        Response: {response.text}")
                raise SpeechServiceError(error_msg)
                
        except Exception as e:
            print(f"[ERROR] Azure submission failed: {e}")
            # Log to error logger
            log_error(
                source="TranscriptionManager",
                error_type="Submission Failed",
                message=f"Azure STT submission failed for job {job_id[:8]}",
                details=str(e)
            )
            job = self.blob_storage.get_transcription_job(job_id, user_id)
            if job:
                job.status = "failed"
                job.error_message = f"Azure submission failed: {str(e)}"
                job.completed_at = datetime.now().isoformat()
                self.blob_storage.save_transcription_job(job)
    
    def _download_audio_from_blob(self, audio_url: str) -> Optional[bytes]:
        """Download audio file from blob storage URL"""
        try:
            response = requests.get(audio_url, timeout=120)
            if response.status_code == 200:
                return response.content
            else:
                print(f"[WARN] Failed to download audio: {response.status_code}")
                return None
        except Exception as e:
            print(f"[WARN] Error downloading audio: {e}")
            return None
    
    def _check_transcription_status(self, job_id: str, user_id: str):
        """Check Azure STT status - ONLY for Azure STT jobs (no GPT-4o dual execution).
        
        GPT-4o/LLM transcription has its own dedicated path via _process_gpt4o_transcription.
        This method NEVER calls GPT-4o to prevent dual execution.
        """
        try:
            job = self.blob_storage.get_transcription_job(job_id, user_id)
            if not job or job.status != 'processing' or not job.azure_trans_id:
                return
            
            # Safety guard: never process LLM jobs here
            if job.settings and job.settings.get('llm_correction', False):
                print(f"[{user_id[:8]}...] Skipping LLM job in Azure STT checker: {job_id[:8]}")
                return
            
            url = f"{AZURE_SPEECH_KEY_ENDPOINT}/speechtotext/{API_VERSION}/transcriptions/{job.azure_trans_id}"
            headers = {"Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY}
            
            r = requests.get(url, headers=headers)
            data = r.json()
            
            if data.get("status") == "Succeeded":
                content_url = self._get_transcription_result_url(job.azure_trans_id)
                if content_url:
                    settings = job.settings if job.settings else {}
                    diarization_enabled = settings.get('diarization_enabled', False)
                    timestamps_enabled = settings.get('timestamps', False)
                    profanity_mode = settings.get('profanity', 'masked')
                    
                    # Get Azure STT transcript (pure Azure result - no GPT-4o mixing)
                    transcript = self._fetch_transcript(
                        content_url, 
                        diarization_enabled, 
                        timestamps_enabled,
                        profanity_mode
                    )
                    
                    settings['azure_stt_transcript_length'] = len(transcript) if transcript else 0
                    settings['transcription_method'] = 'azure-stt'
                    
                    # Upload transcript to blob storage
                    transcript_url = self.blob_storage.upload_transcript_result(
                        transcript, user_id, job_id, 
                        os.path.splitext(job.original_filename)[0] + "_transcript"
                    )
                    
                    job.status = "completed"
                    job.transcript_text = transcript
                    job.transcript_url = transcript_url
                    job.completed_at = datetime.now().isoformat()
                    job.settings = settings
                    self.blob_storage.save_transcription_job(job)
                    
                    print(f"[{user_id[:8]}...] Azure STT completed: {job.original_filename}")
                        
            elif data.get("status") in ("Failed", "FailedWithPartialResults"):
                error_message = ""
                if "properties" in data and "error" in data["properties"]:
                    error_message = data["properties"]["error"].get("message", "")
                elif "error" in data:
                    error_message = data["error"].get("message", "")
                
                job.status = "failed"
                job.error_message = f"Azure transcription failed: {error_message}"
                job.completed_at = datetime.now().isoformat()
                self.blob_storage.save_transcription_job(job)
                
        except Exception as e:
            print(f"âŒ Status check failed: {e}")
            job = self.blob_storage.get_transcription_job(job_id, user_id)
            if job:
                job.status = "failed"
                job.error_message = f"Status check failed: {str(e)}"
                job.completed_at = datetime.now().isoformat()
                self.blob_storage.save_transcription_job(job)
    
    def _get_transcription_result_url(self, azure_trans_id: str) -> Optional[str]:
        """Get transcription result URL from Azure"""
        try:
            url = f"{AZURE_SPEECH_KEY_ENDPOINT}/speechtotext/{API_VERSION}/transcriptions/{azure_trans_id}/files"
            headers = {"Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY}
            
            response = requests.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                files = response.json().get('values', [])
                for file in files:
                    if file.get('kind') == 'Transcription':
                        return file.get('links', {}).get('contentUrl')
            return None
        except Exception as e:
            print(f"âŒ Error getting transcription result URL: {e}")
            return None
    
    def _fetch_transcript(self, content_url: str, diarization_enabled: bool = False, 
                        timestamps_enabled: bool = False, profanity_mode: str = 'masked') -> str:
        """Fetch and format transcript"""
        try:
            response = requests.get(content_url, timeout=60)
            if response.status_code == 200:
                data = response.json()
                return self._format_transcript(data, diarization_enabled, timestamps_enabled, profanity_mode)
            return ""
        except Exception as e:
            print(f"âŒ Error fetching transcript: {e}")
            return ""
    
    def _format_transcript(self, data: Dict, diarization_enabled: bool = False, 
                      timestamps_enabled: bool = False, profanity_mode: str = 'masked') -> str:
        """Format Azure transcript"""
        try:
            lines = []
            
            def get_text_by_profanity_mode(nbest_item):
                if profanity_mode == 'raw':
                    return nbest_item.get('lexical', nbest_item.get('display', ''))
                elif profanity_mode == 'removed':
                    return nbest_item.get('itn', nbest_item.get('display', ''))
                else:
                    return nbest_item.get('display', nbest_item.get('maskedITN', ''))
            
            recognized_phrases = data.get('recognizedPhrases', [])
            
            if not recognized_phrases:
                combined_phrases = data.get('combinedRecognizedPhrases', [])
                if combined_phrases:
                    for phrase in combined_phrases:
                        text = get_text_by_profanity_mode(phrase)
                        if text:
                            lines.append(text)
                    return "\n\n".join(lines) if lines else "No transcript available"
            
            for phrase in recognized_phrases:
                nbest = phrase.get('nBest', [])
                if not nbest:
                    continue
                    
                best = nbest[0]
                text = get_text_by_profanity_mode(best)
                
                if not text:
                    continue
                
                line_parts = []
                
                if timestamps_enabled:
                    offset = phrase.get('offset')
                    if offset:
                        if isinstance(offset, str) and offset.startswith('PT'):
                            timestamp_str = self._parse_iso_duration(offset)
                        else:
                            offset_ticks = phrase.get('offsetInTicks', 0)
                            timestamp_seconds = offset_ticks / 10000000.0
                            timestamp_str = self._format_timestamp(timestamp_seconds)
                        
                        line_parts.append(f"[{timestamp_str}]")
                
                if diarization_enabled:
                    speaker = phrase.get('speaker')
                    if speaker is not None:
                        line_parts.append(f"[Speaker {speaker}]")
                
                line_parts.append(text)
                lines.append(" ".join(line_parts))
            
            return "\n\n".join(lines) if lines else "No transcript available"
            
        except Exception as e:
            print(f"âŒ Error formatting transcript: {e}")
            return "Error formatting transcript"
    
    def _parse_iso_duration(self, duration_str: str) -> str:
        """Parse ISO 8601 duration format"""
        try:
            duration_str = duration_str.replace('PT', '')
            
            hours = 0
            minutes = 0
            seconds = 0.0
            
            if 'H' in duration_str:
                h_parts = duration_str.split('H')
                hours = float(h_parts[0])
                duration_str = h_parts[1]
            
            if 'M' in duration_str:
                m_parts = duration_str.split('M')
                minutes = float(m_parts[0])
                duration_str = m_parts[1]
            
            if 'S' in duration_str:
                seconds = float(duration_str.replace('S', ''))
            
            total_seconds = hours * 3600 + minutes * 60 + seconds
            return self._format_timestamp(total_seconds)
            
        except Exception as e:
            print(f"âš ï¸ Error parsing duration '{duration_str}': {e}")
            return "00:00"
    
    def _format_timestamp(self, seconds: float) -> str:
        """Format seconds into HH:MM:SS or MM:SS timestamp"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        
        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes:02d}:{secs:02d}"
    
    def get_job_status(self, job_id: str) -> Optional[TranscriptionJob]:
        """Get job status from blob storage"""
        return self.blob_storage.find_transcription_job(job_id)
    
    def get_user_history(self, user_id: str, limit: int = 50) -> List[TranscriptionJob]:
        """Get user history from blob storage"""
        return self.blob_storage.get_user_transcription_history(user_id, limit)
    
    def get_user_stats(self, user_id: str) -> Dict:
        """Get user stats from blob storage"""
        return self.blob_storage.get_user_stats(user_id)
    
    def get_user_summary_stats(self, user_id: str) -> Dict:
        """Get user summary stats from blob storage"""
        return self.blob_storage.get_user_summary_stats(user_id)
    
    def register_user(self, email: str, username: str, password: str, 
                     gdpr_consent: bool, data_retention_consent: bool, 
                     marketing_consent: bool) -> Tuple[bool, str, Optional[str]]:
        """Register new user - save to blob storage only"""
        try:
            # ... validation code ...
            
            # Check if user exists in blob storage
            existing = self.blob_storage.find_user_by_email(email)
            if existing:
                return False, "Email already registered", None
            
            existing = self.blob_storage.find_user_by_username(username)
            if existing:
                return False, "Username already taken", None
            
            # Create user
            user_id = str(uuid.uuid4())
            password_hash = AuthManager.hash_password(password)
            
            user = User(
                user_id=user_id,
                email=email,
                username=username,
                password_hash=password_hash,
                created_at=datetime.now().isoformat(),
                gdpr_consent=gdpr_consent,
                data_retention_agreed=data_retention_consent,
                marketing_consent=marketing_consent
            )
            
            # Save to blob storage only (no database)
            if self.blob_storage.save_user(user):
                print(f"👤 New user registered in blob storage: {username}")
                return True, "Account created successfully", user_id
            else:
                return False, "Failed to save user to blob storage", None
                
        except Exception as e:
            print(f"[ERROR] Error registering user: {str(e)}")
            return False, f"Registration failed: {str(e)}", None
    
    def login_user(self, login: str, password: str) -> Tuple[bool, str, Optional[User]]:
        """Login user - authenticate from blob storage only"""
        try:
            # Try email first in blob storage
            user = self.blob_storage.find_user_by_email(login)
            if not user:
                # Try username in blob storage
                user = self.blob_storage.find_user_by_username(login)
            
            if not user:
                return False, "Invalid credentials", None
            
            if not user.is_active:
                return False, "Account is inactive", None
            
            if not AuthManager.verify_password(password, user.password_hash):
                return False, "Invalid credentials", None
            
            # Update last login in blob storage
            user.last_login = datetime.now().isoformat()
            self.blob_storage.save_user(user)
            
            print(f"🔓 User logged in from blob storage: {user.username}")
            return True, "Login successful", user
            
        except Exception as e:
            print(f"[ERROR] Login error: {str(e)}")
            return False, f"Login failed: {str(e)}", None

    
    def update_user_consent(self, user_id: str, marketing_consent: bool) -> bool:
        """Update user marketing consent"""
        try:
            user = self.blob_storage.get_user(user_id)
            if not user:
                return False
            
            user.marketing_consent = marketing_consent
            return self.blob_storage.save_user(user)
        except Exception as e:
            print(f"âŒ Error updating consent: {str(e)}")
            return False
    
    def export_user_data(self, user_id: str) -> Dict:
        """Export user data from blob storage"""
        return self.blob_storage.export_user_data(user_id)
    
    def delete_user_account(self, user_id: str) -> bool:
        """Delete user account and all data from blob storage"""
        return self.blob_storage.delete_user_all_data(user_id)
    
    def save_summary_job(self, job: SummaryJob):
        """Save AI summary job to blob storage"""
        self.blob_storage.save_summary_job(job)
    
    def get_summary_job(self, job_id: str) -> Optional[SummaryJob]:
        """Get AI summary job from blob storage"""
        return self.blob_storage.find_summary_job(job_id)
    
    def get_user_summary_history(self, user_id: str, limit: int = 50) -> List[SummaryJob]:
        """Get user AI summary history from blob storage"""
        return self.blob_storage.get_user_summary_history(user_id, limit)
    
    def delete_user_summary_data(self, user_id: str) -> bool:
        """Delete user summary data from blob storage"""
        try:
            prefix = f"summaries/{user_id}/"
            container_client = self.blob_storage.blob_service.get_container_client(META_DATA_CONTAINER)
            blobs = container_client.list_blobs(name_starts_with=prefix)
            
            for blob in blobs:
                try:
                    blob_client = self.blob_storage._get_blob_client(META_DATA_CONTAINER, blob.name)
                    blob_client.delete_blob()
                except Exception:
                    continue
            
            print(f"ðŸ—'ï¸ User AI summary data deleted: {user_id[:8]}...")
            return True
        except Exception as e:
            print(f"âŒ Error deleting user summary data: {e}")
            return False

    def get_storage_stats(self) -> Dict:
        """Get cloud storage statistics"""
        try:
            stats = {'users_count': 0, 'metadata_count': 0, 'password_resets_count': 0, 'total_size_mb': 0.0}
            for container_name, key in [
                (USER_PASSWORD_CONTAINER, 'users_count'),
                (META_DATA_CONTAINER, 'metadata_count'),
            ]:
                try:
                    container_client = self.blob_storage.blob_service.get_container_client(container_name)
                    blobs = list(container_client.list_blobs())
                    if key == 'metadata_count':
                        stats[key] = len([b for b in blobs if not b.name.startswith('password_resets/')])
                        stats['password_resets_count'] = len([b for b in blobs if b.name.startswith('password_resets/')])
                    else:
                        stats[key] = len(blobs)
                    stats['total_size_mb'] += sum(b.size for b in blobs) / (1024 * 1024)
                except Exception:
                    pass
            return stats
        except Exception:
            return {'users_count': 0, 'metadata_count': 0, 'password_resets_count': 0, 'total_size_mb': 0.0}

def check_ffmpeg_available() -> bool:
    """Check if FFmpeg is available on the system"""
    try:
        result = subprocess.run(
            ['ffmpeg', '-version'],
            capture_output=True,
            timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False
        
if not check_ffmpeg_available():
    print("[WARN] WARNING: FFmpeg not found! Audio conversion will fail.")
    print("   Please install FFmpeg: apt-get install ffmpeg")
else:
    print("[OK] FFmpeg detected and ready for audio conversion")

# Validate Azure Speech Service connectivity
def validate_speech_service():
    """Test Azure Speech Service key validity on startup"""
    import requests
    
    API_VERSION = "v3.2"
    valid_key_found = False
    
    print("\n" + "="*70)
    print("🔍 TESTING AZURE SPEECH SERVICE CONNECTIVITY")
    print("="*70)
    
    # Test primary if available
    if AZURE_SPEECH_KEY:
        try:
            endpoint = AZURE_SPEECH_KEY_ENDPOINT.rstrip('/')
            url = f"{endpoint}/speechtotext/{API_VERSION}/transcriptions"
            headers = {"Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY}
            
            print("\n📡 Testing PRIMARY Service:")
            print(f"   Endpoint: {endpoint}")
            print(f"   URL: {url}")
            print(f"   Key: {AZURE_SPEECH_KEY[:8]}...{AZURE_SPEECH_KEY[-4:]}")
            
            response = requests.get(url, headers=headers, timeout=10)
            
            print(f"   Response Status: {response.status_code}")
            
            if response.status_code == 200:
                print("   ✅ PRIMARY Service is VALID and WORKING!")
                valid_key_found = True
            elif response.status_code == 401:
                print("   ❌ PRIMARY Service AUTHENTICATION FAILED (401)")
                print(f"   Error: {response.text}")
            else:
                print(f"   ⚠️  PRIMARY Service returned: {response.status_code}")
                print(f"   Response: {response.text[:200]}")
        except Exception as e:
            print(f"   ❌ PRIMARY Service test FAILED: {str(e)}")
    else:
        print("\n⏭️  PRIMARY Service: Not configured (skipped)")
    
    # Test backup if available
    if AZURE_SPEECH_KEY_BACKUP:
        try:
            endpoint = AZURE_SPEECH_KEY_ENDPOINT_BACKUP.rstrip('/')
            url = f"{endpoint}/speechtotext/{API_VERSION}/transcriptions"
            headers = {"Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY_BACKUP}
            
            print("\n📡 Testing BACKUP Service:")
            print(f"   Endpoint: {endpoint}")
            print(f"   URL: {url}")
            print(f"   Key: {AZURE_SPEECH_KEY_BACKUP[:8]}...{AZURE_SPEECH_KEY_BACKUP[-4:]}")
            
            response = requests.get(url, headers=headers, timeout=10)
            
            print(f"   Response Status: {response.status_code}")
            
            if response.status_code == 200:
                print("   ✅ BACKUP Service is VALID and WORKING!")
                valid_key_found = True
            elif response.status_code == 401:
                print("   ❌ BACKUP Service AUTHENTICATION FAILED (401)")
                print(f"   Error: {response.text}")
            else:
                print(f"   ⚠️  BACKUP Service returned: {response.status_code}")
                print(f"   Response: {response.text[:200]}")
        except Exception as e:
            print(f"   ❌ BACKUP Service test FAILED: {str(e)}")
    else:
        print("\n⏭️  BACKUP Service: Not configured (skipped)")
    
    print("\n" + "="*70)
    if valid_key_found:
        print("✅ AT LEAST ONE SPEECH SERVICE IS WORKING")
    else:
        print("❌ NO VALID SPEECH SERVICE FOUND - 401 ERRORS WILL OCCUR!")
        print("   ACTION REQUIRED: Check your Azure Speech Service keys in Key Vault")
    print("="*70 + "\n")
    
    return valid_key_found

print("\n🔍 Validating Azure Speech Service...")
validate_speech_service()
print("")
    
# Global transcription manager instance
transcription_manager = TranscriptionManager()
