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
        self.audio_converter = AudioConverter()
        self.executor = ThreadPoolExecutor(max_workers=5)
        self._job_status_cache = {}
        
        # Audio enhancement (DSP / noisereduce / Resemble Enhance)
        from audio_enhancer import AudioEnhancer
        self.audio_enhancer = AudioEnhancer()
        
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
    
    def _log_worker_status(self, pending_jobs, iteration_count):
        """Log background worker status every 6th iteration."""
        if not pending_jobs or iteration_count % 6 != 0:
            return
        active_jobs = sum(1 for j in pending_jobs if j.status == 'processing')
        queued_jobs = sum(1 for j in pending_jobs if j.status == 'pending')
        if active_jobs > 0 or queued_jobs > 0:
            print(f"Background worker: {active_jobs} processing, {queued_jobs} queued")
    
    def _dispatch_pending_jobs(self, pending_jobs):
        """Submit pending jobs to Azure or check processing jobs."""
        for job in pending_jobs:
            if job.status == 'pending' and job.audio_url:
                self.executor.submit(self._submit_to_azure, job.job_id, job.user_id)
            elif job.status == 'processing' and job.azure_trans_id:
                self.executor.submit(self._check_transcription_status, job.job_id, job.user_id)
    
    def _background_worker(self):
        """Background worker to process pending jobs"""
        iteration_count = 0
        while self.running:
            try:
                pending_jobs = self.blob_storage.get_pending_transcription_jobs()
                self._log_worker_status(pending_jobs, iteration_count)
                self._dispatch_pending_jobs(pending_jobs)
                
                time.sleep(10)
                iteration_count += 1
                
            except Exception as e:
                print(f"❌ Background worker error: {e}")
                time.sleep(30)
    
    def submit_transcription(self, file_bytes: bytes, original_filename: str, 
                           user_id: str, language: str, settings: Dict) -> str:
        """Submit new transcription job to Azure Speech Service"""
        job_id = str(uuid.uuid4())
        
        if not isinstance(settings, dict):
            settings = {}
        
        # Azure STT path
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
            
            # Audio enhancement based on quality setting
            audio_processing = settings.get('audio_processing', 'standard')
            if audio_processing != 'minimal':
                print(f"🔊 Applying audio enhancement: {audio_processing}")
                enhanced_bytes, enhance_err = self.audio_enhancer.enhance(
                    processed_bytes, audio_processing, original_filename
                )
                if enhance_err:
                    print(f"[WARN] Audio enhancement warning: {enhance_err}")
                else:
                    processed_bytes = enhanced_bytes
                    print(f"[OK] Audio enhanced: {len(processed_bytes) / 1024 / 1024:.2f} MB")
                settings['audio_enhanced'] = True
                settings['enhancement_method'] = audio_processing
            else:
                settings['audio_enhanced'] = False
                settings['enhancement_method'] = 'none'
            
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

    def _run_mock_transcription(self, job, job_id, user_id):
        """Run local mock transcription for testing mode."""
        audio_bytes = self._download_audio_from_blob(job.audio_url)
        if not audio_bytes:
            raise StorageError("Failed to download audio from storage")
        
        ext = job.settings.get('audio_format', 'wav') if job.settings else 'wav'
        with tempfile.NamedTemporaryFile(suffix=f'.{ext}', delete=False) as tmp:
            tmp.write(audio_bytes)
            temp_path = tmp.name
        
        try:
            transcript, success = get_mock_transcription().transcribe_audio(temp_path, job.language)
            if not (success and transcript):
                raise TranscriptionError("Mock transcription failed")
            
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
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
        
        self.blob_storage.save_transcription_job(job)
    
    def _resolve_speech_key(self):
        """Determine which Azure Speech Service key/endpoint to use. Returns (key, endpoint)."""
        if AZURE_SPEECH_KEY:
            print("[INFO] Using PRIMARY Azure Speech Service")
            return AZURE_SPEECH_KEY, AZURE_SPEECH_KEY_ENDPOINT.rstrip('/')
        if AZURE_SPEECH_KEY_BACKUP:
            print("[INFO] Using BACKUP Azure Speech Service (no primary available)")
            return AZURE_SPEECH_KEY_BACKUP, AZURE_SPEECH_KEY_ENDPOINT_BACKUP.rstrip('/')
        raise SpeechServiceError("No Azure Speech Service key available!")
    
    def _build_stt_request(self, job, job_id):
        """Build Azure STT API request payload and properties."""
        settings = job.settings if job.settings else {}
        properties = {
            "wordLevelTimestampsEnabled": settings.get('timestamps', False),
            "profanityFilterMode": settings.get('profanity', 'Masked').capitalize(),
            "punctuationMode": settings.get('punctuation', 'Automatic').capitalize(),
        }
        
        if settings.get('diarization_enabled', False):
            speakers = settings.get('speakers', 2)
            properties["diarizationEnabled"] = True
            properties["diarization"] = {"speakers": {"minCount": 1, "maxCount": speakers}}
        
        return {
            "contentUrls": [job.audio_url],
            "locale": job.language,
            "displayName": f"Transcription_{job_id[:8]}",
            "properties": properties
        }
    
    def _submit_to_azure(self, job_id: str, user_id: str):
        """Submit transcription job to Azure Speech Service (always default)"""
        try:
            job = self.blob_storage.get_transcription_job(job_id, user_id)
            if not job or job.status != 'pending':
                return
            
            if LOCAL_TESTING_MODE:
                print(f"[TEST] [LOCAL MODE] Using mock transcription for job {job_id[:8]}...")
                self._run_mock_transcription(job, job_id, user_id)
                return
            
            print(f"[{user_id[:8]}...] Submitting to Azure STT: {job.original_filename}")
            
            speech_key, speech_endpoint = self._resolve_speech_key()
            url = f"{speech_endpoint}/speechtotext/{API_VERSION}/transcriptions"
            headers = {
                "Ocp-Apim-Subscription-Key": speech_key,
                "Content-Type": "application/json"
            }
            
            data = self._build_stt_request(job, job_id)
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
    
    def _process_succeeded_transcription(self, job, job_id, user_id):
        """Handle a succeeded Azure STT transcription — fetch, save, and mark completed."""
        content_url = self._get_transcription_result_url(job.azure_trans_id)
        if not content_url:
            return
        
        settings = job.settings if job.settings else {}
        transcript = self._fetch_transcript(
            content_url,
            settings.get('diarization_enabled', False),
            settings.get('timestamps', False),
            settings.get('profanity', 'masked')
        )
        
        settings['azure_stt_transcript_length'] = len(transcript) if transcript else 0
        settings['transcription_method'] = 'azure-stt'
        
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
    
    @staticmethod
    def _extract_error_message(data):
        """Extract error message from Azure STT response data."""
        if "properties" in data and "error" in data["properties"]:
            return data["properties"]["error"].get("message", "")
        if "error" in data:
            return data["error"].get("message", "")
        return ""
    
    def _check_transcription_status(self, job_id: str, user_id: str):
        """Check Azure STT transcription status and process results when ready."""
        try:
            job = self.blob_storage.get_transcription_job(job_id, user_id)
            if not job or job.status != 'processing' or not job.azure_trans_id:
                return
            
            url = f"{AZURE_SPEECH_KEY_ENDPOINT}/speechtotext/{API_VERSION}/transcriptions/{job.azure_trans_id}"
            headers = {"Ocp-Apim-Subscription-Key": AZURE_SPEECH_KEY}
            
            r = requests.get(url, headers=headers)
            data = r.json()
            
            if data.get("status") == "Succeeded":
                self._process_succeeded_transcription(job, job_id, user_id)
                        
            elif data.get("status") in ("Failed", "FailedWithPartialResults"):
                job.status = "failed"
                job.error_message = f"Azure transcription failed: {self._extract_error_message(data)}"
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
    
    def _get_timestamp_str(self, phrase):
        """Extract and format a timestamp string from a recognized phrase."""
        offset = phrase.get('offset')
        if not offset:
            return None
        if isinstance(offset, str) and offset.startswith('PT'):
            return self._parse_iso_duration(offset)
        offset_ticks = phrase.get('offsetInTicks', 0)
        return self._format_timestamp(offset_ticks / 10000000.0)
    
    def _format_recognized_phrase(self, phrase, get_text, timestamps_enabled, diarization_enabled):
        """Format a single recognized phrase into a text line, or return None."""
        nbest = phrase.get('nBest', [])
        if not nbest:
            return None
        
        text = get_text(nbest[0])
        if not text:
            return None
        
        parts = []
        if timestamps_enabled:
            ts = self._get_timestamp_str(phrase)
            if ts:
                parts.append(f"[{ts}]")
        
        if diarization_enabled:
            speaker = phrase.get('speaker')
            if speaker is not None:
                parts.append(f"[Speaker {speaker}]")
        
        parts.append(text)
        return " ".join(parts)
    
    def _format_transcript(self, data: Dict, diarization_enabled: bool = False, 
                      timestamps_enabled: bool = False, profanity_mode: str = 'masked') -> str:
        """Format Azure transcript"""
        try:
            def get_text_by_profanity_mode(nbest_item):
                if profanity_mode == 'raw':
                    return nbest_item.get('lexical', nbest_item.get('display', ''))
                if profanity_mode == 'removed':
                    return nbest_item.get('itn', nbest_item.get('display', ''))
                return nbest_item.get('display', nbest_item.get('maskedITN', ''))
            
            recognized_phrases = data.get('recognizedPhrases', [])
            
            if not recognized_phrases:
                combined_phrases = data.get('combinedRecognizedPhrases', [])
                if combined_phrases:
                    lines = [get_text_by_profanity_mode(p) for p in combined_phrases if get_text_by_profanity_mode(p)]
                    return "\n\n".join(lines) if lines else "No transcript available"
            
            lines = []
            for phrase in recognized_phrases:
                line = self._format_recognized_phrase(phrase, get_text_by_profanity_mode, timestamps_enabled, diarization_enabled)
                if line:
                    lines.append(line)
            
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
def _test_speech_key(key, endpoint_raw, label):
    """Test a single Azure Speech Service key and return True if valid."""
    if not key:
        print(f"\n⏭️  {label} Service: Not configured (skipped)")
        return False
    try:
        endpoint = endpoint_raw.rstrip('/')
        url = f"{endpoint}/speechtotext/{API_VERSION}/transcriptions"
        headers = {"Ocp-Apim-Subscription-Key": key}
        
        print(f"\n📡 Testing {label} Service:")
        print(f"   Endpoint: {endpoint}")
        print(f"   URL: {url}")
        print(f"   Key: {key[:8]}...{key[-4:]}")
        
        response = requests.get(url, headers=headers, timeout=10)
        print(f"   Response Status: {response.status_code}")
        
        if response.status_code == 200:
            print(f"   ✅ {label} Service is VALID and WORKING!")
            return True
        if response.status_code == 401:
            print(f"   ❌ {label} Service AUTHENTICATION FAILED (401)")
            print(f"   Error: {response.text}")
        else:
            print(f"   ⚠️  {label} Service returned: {response.status_code}")
            print(f"   Response: {response.text[:200]}")
    except Exception as e:
        print(f"   ❌ {label} Service test FAILED: {str(e)}")
    return False


def validate_speech_service():
    """Test Azure Speech Service key validity on startup"""
    import requests
    
    print("\n" + "="*70)
    print("🔍 TESTING AZURE SPEECH SERVICE CONNECTIVITY")
    print("="*70)
    
    primary_ok = _test_speech_key(AZURE_SPEECH_KEY, AZURE_SPEECH_KEY_ENDPOINT, "PRIMARY")
    backup_ok = _test_speech_key(AZURE_SPEECH_KEY_BACKUP, AZURE_SPEECH_KEY_ENDPOINT_BACKUP, "BACKUP")
    valid_key_found = primary_ok or backup_ok
    
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
