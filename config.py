"""
Secure Configuration Management with Azure Key Vault
Loads secrets from Key Vault with fallback to environment variables
"""
import os
from typing import Optional
from dotenv import load_dotenv
from azure_keyvault_client import get_secret_secure

# Load environment variables from .env file
load_dotenv()

# =============================================================================
# TESTING MODE
# =============================================================================

LOCAL_TESTING_MODE = os.getenv("LOCAL_TESTING_MODE", "False").lower() in ("true", "1", "yes")

# =============================================================================
# AZURE SPEECH SERVICES
# =============================================================================

# Try to get both primary and backup keys (both optional)
AZURE_SPEECH_KEY = get_secret_secure(
    "azure-speech-key",
    fallback_env_var="AZURE_SPEECH_KEY",
    required=False
)

AZURE_SPEECH_KEY_BACKUP = get_secret_secure(
    "azure-speech-key-backup",
    fallback_env_var="AZURE_SPEECH_KEY_BACKUP",
    required=False
)

AZURE_SPEECH_KEY_ENDPOINT = os.getenv("AZURE_SPEECH_KEY_ENDPOINT", "https://westus.api.cognitive.microsoft.com/")
AZURE_SPEECH_KEY_ENDPOINT_BACKUP = os.getenv("AZURE_SPEECH_KEY_ENDPOINT_BACKUP", "https://eastus.api.cognitive.microsoft.com/")
AZURE_REGION = os.getenv("AZURE_REGION", "westus")
AZURE_REGION_BACKUP = os.getenv("AZURE_REGION_BACKUP", "eastus")

# Log which keys are available (show first 8 and last 4 chars for verification)
def mask_key(key: str) -> str:
    if not key or len(key) < 12:
        return "***INVALID***"
    return f"{key[:8]}...{key[-4:]}"

print("\n" + "="*70)
print("🔑 AZURE SPEECH SERVICE KEY STATUS")
print("="*70)
if AZURE_SPEECH_KEY:
    print(f"✅ PRIMARY Key: {mask_key(AZURE_SPEECH_KEY)}")
    print(f"   Endpoint: {AZURE_SPEECH_KEY_ENDPOINT}")
    print(f"   Region: {AZURE_REGION}")
else:
    print("❌ PRIMARY Key: NOT FOUND")

print()
if AZURE_SPEECH_KEY_BACKUP:
    print(f"✅ BACKUP Key: {mask_key(AZURE_SPEECH_KEY_BACKUP)}")
    print(f"   Endpoint: {AZURE_SPEECH_KEY_ENDPOINT_BACKUP}")
    print(f"   Region: {AZURE_REGION_BACKUP}")
else:
    print("❌ BACKUP Key: NOT FOUND")
print("="*70 + "\n")

# Validate: at least one speech key must be available
if not AZURE_SPEECH_KEY and not AZURE_SPEECH_KEY_BACKUP:
    raise ValueError(
        "❌ CRITICAL: No Azure Speech Service keys found!\n"
        "   Please set either AZURE_SPEECH_KEY or AZURE_SPEECH_KEY_BACKUP\n"
        "   in Key Vault or environment variables."
    )

# =============================================================================
# AZURE OPENAI
# =============================================================================

AZURE_OPENAI_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "")
AZURE_OPENAI_KEY = get_secret_secure(
    "azure-openai-key",
    fallback_env_var="AZURE_OPENAI_KEY",
    required=True
)
AZURE_OPENAI_DEPLOYMENT = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5.4-nano")
AZURE_OPENAI_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")

# =============================================================================
# TOKEN CONFIGURATION (1M input, 32k output - GPT 5.4 nano - NO CHUNKING)
# =============================================================================

AZURE_OPENAI_MAX_TOKENS = int(os.getenv("AZURE_OPENAI_MAX_TOKENS", "1000000"))
AZURE_OPENAI_COMPLETION_TOKENS = int(os.getenv("AZURE_OPENAI_COMPLETION_TOKENS", "32000"))

# Chunking Configuration (DISABLED)
ENABLE_CHUNKING = os.getenv("ENABLE_CHUNKING", "False").lower() in ("true", "1", "yes")
CHUNK_STRATEGY = os.getenv("CHUNK_STRATEGY", "none")
AZURE_OPENAI_CHUNK_SIZE = int(os.getenv("AZURE_OPENAI_CHUNK_SIZE", "0"))
AZURE_OPENAI_OVERLAP_TOKENS = int(os.getenv("AZURE_OPENAI_OVERLAP_TOKENS", "0"))

# Content Token Budgets (for optimization only)
AI_TOKEN_LIMIT_TRANSCRIPTS = int(os.getenv("AI_TOKEN_LIMIT_TRANSCRIPTS", "800000"))
AI_TOKEN_LIMIT_DOCUMENTS = int(os.getenv("AI_TOKEN_LIMIT_DOCUMENTS", "150000"))
AI_TOKEN_LIMIT_IMAGES = int(os.getenv("AI_TOKEN_LIMIT_IMAGES", "50000"))

# AI Processing Settings
AI_MAX_PROCESSING_TIME_MINUTES = int(os.getenv("AI_MAX_PROCESSING_TIME_MINUTES", "30"))
AI_RETRY_ATTEMPTS = int(os.getenv("AI_RETRY_ATTEMPTS", "3"))
AI_BATCH_SIZE = int(os.getenv("AI_BATCH_SIZE", "5"))
AI_PROCESSING_TIMEOUT = int(os.getenv("AI_PROCESSING_TIMEOUT", "1800"))  # 30 minutes

# =============================================================================
# AZURE BLOB STORAGE
# =============================================================================

AZURE_BLOB_CONNECTION = get_secret_secure(
    "azure-blob-connection",
    fallback_env_var="AZURE_BLOB_CONNECTION",
    required=True
)
AZURE_STORAGE_ACCOUNT_NAME = os.getenv("AZURE_STORAGE_ACCOUNT_NAME", "aisummarymeetingstorage")

# SAS Tokens for each container
TRANSCRIPTS_SAS_TOKEN = get_secret_secure(
    "transcripts-sas-token",
    fallback_env_var="TRANSCRIPTS_SAS_TOKEN",
    required=False
)
CHAT_RESPONSES_SAS_TOKEN = get_secret_secure(
    "chat-responses-sas-token",
    fallback_env_var="CHAT_RESPONSES_SAS_TOKEN",
    required=False
)
USER_PASSWORD_SAS_TOKEN = get_secret_secure(
    "user-password-sas-token",
    fallback_env_var="USER_PASSWORD_SAS_TOKEN",
    required=False
)
META_DATA_SAS_TOKEN = get_secret_secure(
    "meta-data-sas-token",
    fallback_env_var="META_DATA_SAS_TOKEN",
    required=False
)

# Blob Storage Containers
AZURE_CONTAINER = os.getenv("AZURE_CONTAINER", "transcripts")
CHAT_RESPONSES_CONTAINER = os.getenv("CHAT_RESPONSES_CONTAINER", "response-chats")
USER_AUTH_CONTAINER = os.getenv("USER_PASSWORD_CONTAINER", "user-password")
META_DATA_CONTAINER = os.getenv("META_DATA_CONTAINER", "meta-storage")

# =============================================================================
# COMPUTER VISION
# =============================================================================

COMPUTER_VISION_ENDPOINT = os.getenv("COMPUTER_VISION_ENDPOINT", "")
COMPUTER_VISION_KEY = get_secret_secure(
    "computer-vision-key",
    fallback_env_var="COMPUTER_VISION_KEY",
    required=True
)
COMPUTER_VISION_REGION = os.getenv("COMPUTER_VISION_REGION", "southeastasia")
API_VERSION = os.getenv("API_VERSION", "v3.2")

# =============================================================================
# FILE UPLOAD SETTINGS
# =============================================================================

UPLOAD_MAX_SIZE_MB = int(os.getenv("UPLOAD_MAX_SIZE_MB", "500"))
MAX_FILE_SIZE_BYTES = int(os.getenv("MAX_FILE_SIZE_BYTES", "524288000"))

# Supported file formats
ALLOWED_LANGS = os.getenv("ALLOWED_LANGS", '{"en-US": "English (US)", "en-GB": "English (UK)", "es-ES": "Spanish", "fr-FR": "French", "de-DE": "German", "it-IT": "Italian", "pt-BR": "Portuguese (Brazil)", "zh-CN": "Chinese (Simplified)", "ja-JP": "Japanese", "ko-KR": "Korean", "ru-RU": "Russian", "ar-SA": "Arabic", "hi-IN": "Hindi", "th-TH": "Thai", "vi-VN": "Vietnamese"}')
AUDIO_FORMATS = os.getenv("AUDIO_FORMATS", "wav,mp3,ogg,opus,flac,wma,aac,m4a,amr,webm,speex")
VIDEO_FORMATS = os.getenv("VIDEO_FORMATS", "mp4,mov,avi,mkv,webm,flv,3gp,wmv")
DOCUMENT_FORMATS = os.getenv("DOCUMENT_FORMATS", "pdf,docx,doc,pptx,ppt,xlsx,xls,txt,csv,json")
IMAGE_FORMATS = os.getenv("IMAGE_FORMATS", "jpg,jpeg,png,bmp,gif,tiff,webp")

# Audio Preprocessing
DEFAULT_AUDIO_PROCESSING = os.getenv("DEFAULT_AUDIO_PROCESSING", "standard")
NOISE_REDUCTION_STRENGTH = float(os.getenv("NOISE_REDUCTION_STRENGTH", "0.8"))
TARGET_LOUDNESS_DBFS = int(os.getenv("TARGET_LOUDNESS_DBFS", "-20"))
ENABLE_SPEAKER_SEPARATION = os.getenv("ENABLE_SPEAKER_SEPARATION", "True").lower() in ("true", "1", "yes")
# Files larger than this (MB) skip Python-level numpy processing to prevent OOM
# FFmpeg enhancement still runs for these files
MAX_AUDIO_PREPROCESS_MB = int(os.getenv("MAX_AUDIO_PREPROCESS_MB", "500"))
MAX_UPLOAD_FILE_MB = int(os.getenv("MAX_UPLOAD_FILE_MB", "500"))

# =============================================================================
# UI CONFIGURATION
# =============================================================================

LOGO_PATH = os.getenv("LOGO_PATH", "static/logo_betime-white.png")
APP_TITLE = os.getenv("APP_TITLE", "🎙️🤖 ระบบ AI สำหรับการประชุม")
SHOW_TOP_ICONS = os.getenv("SHOW_TOP_ICONS", "False").lower() in ("true", "1", "yes")

# =============================================================================
# OTHER SETTINGS
# =============================================================================

DEBUG = os.getenv("DEBUG", "False").lower() in ("true", "1", "yes")
MAX_CONCURRENT_JOBS = int(os.getenv("MAX_CONCURRENT_JOBS", "5"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "database/ai_conference_service.db")

# =============================================================================
# SECURITY
# =============================================================================

PASSWORD_SALT = get_secret_secure(
    "password-salt",
    fallback_env_var="PASSWORD_SALT",
    required=True
)

# =============================================================================
# LOGGING
# =============================================================================

print("✅ Secure configuration loaded successfully")
if LOCAL_TESTING_MODE:
    print("🧪 LOCAL TESTING MODE ENABLED - Using mock services")
