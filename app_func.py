import zipfile
import gradio as gr
import json
import os
from datetime import datetime, timedelta, timezone
from backend import (ALLOWED_LANGS, AUDIO_FORMATS, transcription_manager, User)
from ai_summary import ai_summary_manager
from session_manager import session_manager
import html
import mimetypes

# Bangkok timezone (UTC+7) — used for all user-facing timestamps
BANGKOK_TZ = timezone(timedelta(hours=7))

# Shared UI messages (extracted to avoid duplicate literals)
MSG_LOGIN_STATS = "👤 Please log in to view your statistics..."
MSG_LOGIN_REQUIRED = "👤 Please log in to view statistics"
MSG_AUTO_REFRESH = "🔄 Auto-refresh active"
METHOD_LLM = "🤖 LLM"

# Shared regex patterns
RE_CLEAN_SPECIAL = r'[^\w\s-]'
RE_CLEAN_SPACES = r'[-\s]+'

# Helper for auto-refresh HTML indicator
def _refresh_html(text=""):
    """Return HTML string for refresh indicator, or empty string to hide."""
    if not text:
        return ""
    return f'<div class="refresh-badge">{html.escape(text)}</div>'

HIDE_REFRESH = gr.update(value="")

# Helpers for download info display
def _download_info_html(filepath):
    """Return HTML showing file name and size for the download area."""
    if not filepath or not os.path.exists(filepath):
        return ""
    filename = os.path.basename(filepath)
    size_bytes = os.path.getsize(filepath)
    if size_bytes < 1024:
        size_str = f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        size_str = f"{size_bytes / 1024:.1f} KB"
    else:
        size_str = f"{size_bytes / (1024 * 1024):.2f} MB"
    return (
        f'<div class="download-file-info">'
        f'<span class="download-filename">📄 {html.escape(filename)}</span>'
        f'<span class="download-filesize">{size_str}</span>'
        f'</div>'
    )

HIDE_DOWNLOAD = (gr.update(value=""), gr.update(visible=False))

def to_bangkok(dt_str: str) -> datetime:
    """Convert an ISO timestamp string to Bangkok time (UTC+7).
    Handles both naive (assumed UTC from Docker) and aware datetimes."""
    try:
        dt = datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            # Naive datetime from Docker container = UTC
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(BANGKOK_TZ)
    except Exception:
        return datetime.now(BANGKOK_TZ)

def now_bangkok() -> datetime:
    """Get current time in Bangkok timezone."""
    return datetime.now(BANGKOK_TZ)

def format_status(status):
    """Convert status to user-friendly format"""
    status_map = {
        'pending': '⏳ Queued',
        'processing': '🔄 Processing',
        'processing_gpt4o': '🤖 LLM Processing',
        'completed': '✅ Done',
        'failed': '❌ Failed'
    }
    return status_map.get(status, status)

def format_processing_time(created_at, completed_at=None):
    """Calculate and format processing time"""
    try:
        start_time = to_bangkok(created_at)
        if completed_at:
            end_time = to_bangkok(completed_at)
            duration = end_time - start_time
        else:
            duration = now_bangkok() - start_time
        
        total_seconds = int(duration.total_seconds())
        if total_seconds < 60:
            return f"{total_seconds}s"
        elif total_seconds < 3600:
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            return f"{minutes}m {seconds}s"
        else:
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            return f"{hours}h {minutes}m"
    except Exception:
        return "Unknown"

def _ensure_user(user, session_token=None):
    """Ensure user is authenticated. Tries session token as fallback.
    
    This prevents 'Please log in' errors when current_user state is lost
    due to race conditions (page refresh, WebSocket reconnect, etc.)
    but the session ticket is still valid.
    """
    if user:
        return user
    if session_token:
        try:
            restored = session_manager.validate_session(session_token)
            if restored:
                print(f"🔄 User auto-restored from session: {restored.username}")
                return restored
        except Exception as e:
            print(f"⚠️ Session restore fallback failed: {e}")
    return None


def get_user_stats_display(user: User):
    """Get comprehensive user statistics for display"""
    if not user:
        return MSG_LOGIN_REQUIRED
    
    try:
        # Get transcript stats
        transcript_stats = transcription_manager.get_user_stats(user.user_id)
        
        # Get AI summary stats
        summary_stats = transcription_manager.get_user_summary_stats(user.user_id)
        
        total_transcripts = transcript_stats.get('total_jobs', 0)
        total_summaries = summary_stats.get('total_jobs', 0)
        
        stats_text = f"👤 {user.username} | 🎙️ Transcripts: {total_transcripts} | 🤖 AI Summaries: {total_summaries}"
        
        # Add processing status
        processing_transcripts = transcript_stats.get('by_status', {}).get('processing', 0)
        processing_summaries = summary_stats.get('by_status', {}).get('processing', 0)
        
        if processing_transcripts > 0:
            stats_text += f" | 🔄 Transcribing: {processing_transcripts}"
        if processing_summaries > 0:
            stats_text += f" | 🔄 Summarizing: {processing_summaries}"
            
        return stats_text
        
    except Exception as e:
        return f"👤 {user.username} | Stats error: {str(e)}"

# Authentication Functions
def register_user(email, username, password, confirm_password, gdpr_consent, data_retention_consent, marketing_consent):
    """Register new user account"""
    try:
        print(f"📝 Registration attempt for: {username} ({email})")
        
        # Validate inputs
        if not email or not username or not password:
            return "❌ All fields are required", gr.update(visible=False)
        
        if password != confirm_password:
            return "❌ Passwords do not match", gr.update(visible=False)
        
        if not gdpr_consent:
            return "❌ GDPR consent is required to create an account", gr.update(visible=False)
        
        if not data_retention_consent:
            return "❌ Data retention agreement is required", gr.update(visible=False)
        
        # Attempt registration
        success, message, _ = transcription_manager.register_user(
            email, username, password, gdpr_consent, data_retention_consent, marketing_consent
        )
        
        print(f"📝 Registration result: success={success}, message={message}")
        
        if success:
            print(f"✅ User registered successfully: {username}")
            return f"✅ {message}! Please log in with your credentials.", gr.update(visible=True)
        else:
            print(f"❌ Registration failed: {message}")
            return f"❌ {message}", gr.update(visible=False)
            
    except Exception as e:
        print(f"❌ Registration error: {str(e)}")
        return f"❌ Registration error: {str(e)}", gr.update(visible=False)

def login_user(login, password):
    """Login user"""
    try:
        print(f"🔐 Login attempt for: {login}")
        
        if not login or not password:
            return "❌ Please enter both username/email and password", None, gr.update(visible=True), gr.update(visible=False), MSG_LOGIN_STATS
        
        success, message, user = transcription_manager.login_user(login, password)
        print(f"🔐 Login result: success={success}, message={message}")
        
        if success and user:
            print(f"✅ User logged in successfully: {user.username}")
            stats_display = get_user_stats_display(user)
            return f"✅ Welcome back, {user.username}!", user, gr.update(visible=False), gr.update(visible=True), stats_display
        else:
            print(f"❌ Login failed: {message}")
            return f"❌ {message}", None, gr.update(visible=True), gr.update(visible=False), MSG_LOGIN_STATS
            
    except Exception as e:
        print(f"❌ Login error: {str(e)}")
        return f"❌ Login error: {str(e)}", None, gr.update(visible=True), gr.update(visible=False), MSG_LOGIN_STATS

def logout_user():
    """Logout user"""
    print("👋 User logged out")
    return None, "👋 You have been logged out. Please log in to continue.", gr.update(visible=True), gr.update(visible=False), MSG_LOGIN_STATS

# Transcription Functions
def submit_transcription(file, language, audio_format, diarization_enabled, speakers, 
                        profanity, punctuation, timestamps, lexical, audio_processing, 
                        llm_correction, user, session_token=None):
    """Submit transcription job with optional LLM correction for improved accuracy"""
    user = _ensure_user(user, session_token)
    if not user:
        return (
            "❌ Please log in to submit transcriptions",
            "",
            gr.update(value=""), gr.update(visible=False),
            "",
            {},
            gr.update(value=""),
            gr.update()
        )
    
    if file is None:
        return (
            "Please upload an audio or video file first.",
            "",
            gr.update(value=""), gr.update(visible=False),
            "",
            {},
            gr.update(value=""),
            gr.update()
        )
    
    try:
        # Get file data
        try:
            if isinstance(file, str):
                if os.path.exists(file):
                    with open(file, 'rb') as f:
                        file_bytes = f.read()
                    original_filename = os.path.basename(file)
                else:
                    return (
                        "File not found. Please try uploading again.",
                        "",
                        gr.update(value=""), gr.update(visible=False),
                        "",
                        {},
                        gr.update(value=""),
                        gr.update()
                    )
            else:
                file_path = str(file)
                if os.path.exists(file_path):
                    with open(file_path, 'rb') as f:
                        file_bytes = f.read()
                    original_filename = os.path.basename(file_path)
                else:
                    return (
                        "Unable to process file. Please try again.",
                        "",
                        gr.update(value=""), gr.update(visible=False),
                        "",
                        {},
                        gr.update(value=""),
                        gr.update()
                    )
        except Exception as e:
            return (
                f"Error reading file: {str(e)}",
                "",
                gr.update(value=""), gr.update(visible=False),
                "",
                {},
                gr.update(value=""),
                gr.update()
            )
        
        # Validate file
        file_extension = original_filename.split('.')[-1].lower() if '.' in original_filename else ""
        supported_extensions = set(AUDIO_FORMATS) | {
            'mp4', 'mov', 'avi', 'mkv', 'webm', 'm4a', '3gp', 'f4v', 
            'wmv', 'asf', 'rm', 'rmvb', 'flv', 'mpg', 'mpeg', 'mts', 'vob'
        }
        
        if file_extension not in supported_extensions and file_extension != "":
            return (
                f"Unsupported file format: .{file_extension}",
                "",
                gr.update(value=""), gr.update(visible=False),
                "",
                {},
                gr.update(value=""),
                gr.update()
            )
        
        # Basic file size check
        if len(file_bytes) > 500 * 1024 * 1024:  # 500MB limit
            return (
                "File too large. Please upload files smaller than 500MB.",
                "",
                gr.update(value=""), gr.update(visible=False),
                "",
                {},
                gr.update(value=""),
                gr.update()
            )
        
        # LLM Transcription: enforce 25MB limit and audio-only
        if bool(llm_correction):
            audio_extensions = {'wav', 'mp3', 'ogg', 'opus', 'flac', 'wma', 'aac', 'm4a', 'amr', 'speex', 'webm'}
            if file_extension not in audio_extensions:
                return (
                    f"❌ LLM Transcription only supports audio files.\nUnsupported: .{file_extension}\nSupported: WAV, MP3, OGG, M4A, FLAC, AAC, etc.\nPlease uncheck '🤖 LLM Transcription' to use standard transcription for video files.",
                    "",
                    gr.update(value=""), gr.update(visible=False),
                    "",
                    {},
                    gr.update(value=""),
                    gr.update()
                )
            max_llm_size = 25 * 1024 * 1024  # 25MB
            if len(file_bytes) > max_llm_size:
                file_size_mb = len(file_bytes) / 1024 / 1024
                return (
                    f"❌ File too large for LLM Transcription: {file_size_mb:.1f}MB\nMaximum size: 25MB\nPlease uncheck '🤖 LLM Transcription' to use standard transcription for larger files.",
                    "",
                    gr.update(value=""), gr.update(visible=False),
                    "",
                    {},
                    gr.update(value=""),
                    gr.update()
                )
        
        # Prepare settings with LLM correction option
        settings = {
            'audio_format': audio_format,
            'diarization_enabled': bool(diarization_enabled),
            'speakers': int(speakers),
            'profanity': profanity,
            'punctuation': punctuation,
            'timestamps': bool(timestamps),
            'lexical': bool(lexical),
            'audio_processing': audio_processing,
            'llm_correction': bool(llm_correction)  # NEW: LLM correction flag
        }

        
        print(f"🎙️ Submitting transcription with settings: {settings}")
        print(f"   - Diarization: {diarization_enabled}")
        print(f"   - LLM Correction: {llm_correction}")
        
        # Submit job
        job_id = transcription_manager.submit_transcription(
            file_bytes, original_filename, user.user_id, language, settings
        )
        
        # Update job state
        job_state = {
            'current_job_id': job_id,
            'start_time': datetime.now().isoformat(),
            'auto_refresh_active': True,
            'last_status': 'pending',
            'llm_correction_requested': bool(llm_correction)
        }
        
        # Get updated user stats
        stats_display = get_user_stats_display(user)
        
        # Build status message
        correction_note = "\n🤖 LLM correction enabled (improved accuracy)" if llm_correction else ""
        
        return (
            f"🚀 Transcription started for: {original_filename}{correction_note}\n📡 Auto-refreshing every 10 seconds...",
            "",
            gr.update(value=""), gr.update(visible=False),
            f"Job ID: {job_id}",
            job_state,
            gr.update(value=_refresh_html(MSG_AUTO_REFRESH)),
            stats_display
        )
        
    except Exception as e:
        print(f"❌ Error submitting transcription: {str(e)}")
        return (
            f"Error: {str(e)}",
            "",
            gr.update(value=""), gr.update(visible=False),
            "",
            {},
            gr.update(value=""),
            gr.update()
        )

def check_current_job_status(job_state, user, session_token=None):
    """Check status of current job with improved transcript handling"""
    user = _ensure_user(user, session_token)
    if not user:
        return (
            "❌ Please log in to check status", 
            "",
            gr.update(value=""), gr.update(visible=False),
            "",
            gr.update(value=""),
            gr.update()
        )
    
    if not job_state or 'current_job_id' not in job_state:
        return (
            "No active job", 
            "",
            gr.update(value=""), gr.update(visible=False),
            "",
            gr.update(value=""),
            gr.update()
        )
    
    job_id = job_state['current_job_id']
    
    try:
        job = transcription_manager.get_job_status(job_id)
        if not job or job.user_id != user.user_id:
            return (
                "Job not found or access denied", 
                "", 
                gr.update(value=""), gr.update(visible=False), 
                "",
                gr.update(value=""),
                gr.update()
            )
        
        # Calculate processing time
        processing_time = format_processing_time(job.created_at, job.completed_at)
        
        # Enhanced status change logging
        last_status = job_state.get('last_status', '')
        if job.status != last_status:
            print(f"🔄 [{user.username}] Job status change: {last_status} → {job.status} ({job.original_filename})")
            job_state['last_status'] = job.status
        
        # Get updated user stats
        stats_display = get_user_stats_display(user)
        
        # Handle completed status with better transcript detection
        if job.status == 'completed' and job.transcript_text and job.transcript_text.strip():
            # Job is complete and transcript is available
            
            # Create downloadable file
            try:
                transcript_file = create_transcript_file(job.transcript_text, job_id)
                print(f"✅ [{user.username}] Transcription ready: {len(job.transcript_text)} characters")
            except Exception as e:
                print(f"⚠️ [{user.username}] Error creating transcript file: {str(e)}")
                transcript_file = None
            
            return (
                f"✅ Transcription completed in {processing_time}",
                job.transcript_text,
                gr.update(value=_download_info_html(transcript_file) if transcript_file else ""),
                gr.update(visible=True, value=transcript_file) if transcript_file else gr.update(visible=False),
                f"Processed: {job.original_filename}",
                gr.update(value=""),  # Hide auto-refresh status
                stats_display
            )
        
        elif job.status == 'failed':
            # Job failed
            error_msg = job.error_message[:100] + "..." if job.error_message and len(job.error_message) > 100 else job.error_message or "Unknown error"
            return (
                f"❌ Transcription failed after {processing_time}",
                "",
                gr.update(value=""), gr.update(visible=False),
                f"Error: {error_msg}",
                gr.update(value=""),  # Hide auto-refresh status
                stats_display
            )
        
        elif job.status == 'processing':
            # Still processing with Azure STT
            return (
                f"🔄 Processing... ({processing_time} elapsed)\n📡 Auto-refreshing every 10 seconds...",
                "",
                gr.update(value=""), gr.update(visible=False),
                f"Converting and analyzing: {job.original_filename}",
                gr.update(value=_refresh_html(MSG_AUTO_REFRESH)),
                stats_display
            )
        
        elif job.status == 'processing_gpt4o':
            # GPT-4o LLM transcription in progress
            return (
                f"🤖 LLM Transcription in progress... ({processing_time} elapsed)\n📡 Auto-refreshing every 10 seconds...",
                "",
                gr.update(value=""), gr.update(visible=False),
                f"GPT-4o processing: {job.original_filename}",
                gr.update(value=_refresh_html("🤖 LLM Auto-refresh active")),
                stats_display
            )
        
        elif job.status == 'completed' and (not job.transcript_text or not job.transcript_text.strip()):
            # Job marked as completed but transcript not yet available - keep refreshing
            return (
                f"🔄 Finalizing transcript... ({processing_time} elapsed)\n📡 Auto-refreshing every 10 seconds...",
                "",
                gr.update(value=""), gr.update(visible=False),
                f"Retrieving results: {job.original_filename}",
                gr.update(value=_refresh_html(MSG_AUTO_REFRESH)),
                stats_display
            )
        
        else:  # pending
            # Still pending
            return (
                f"⏳ Queued for processing... ({processing_time} waiting)\n📡 Auto-refreshing every 10 seconds...",
                "",
                gr.update(value=""), gr.update(visible=False),
                f"Waiting: {job.original_filename}",
                gr.update(value=_refresh_html(MSG_AUTO_REFRESH)),
                stats_display
            )
        
    except Exception as e:
        print(f"❌ Error checking job status: {str(e)}")
        return (
            f"Error checking status: {str(e)}", 
            "",
            gr.update(value=""), gr.update(visible=False),
            "",
            gr.update(value=""),
            gr.update()
        )


# AI Summary Functions (continued in next artifact due to length)
def get_available_transcripts(user):
    """Get list of available transcripts for AI summarization"""
    if not user:
        return gr.update(choices=[], value=[])
    
    try:
        # Get completed transcripts
        completed_jobs = transcription_manager.get_user_history(user.user_id, limit=50)
        completed_transcripts = [
            job for job in completed_jobs 
            if job.status == 'completed' and job.transcript_text
        ]
        
        # Create choices list
        choices = []
        for job in completed_transcripts[:20]:  # Limit to recent 20
            label = f"{job.original_filename} ({job.created_at[:16]})"
            choices.append((label, job.job_id))
        
        return gr.update(choices=choices, value=[])
        
    except Exception as e:
        print(f"❌ Error getting available transcripts: {str(e)}")
        return gr.update(choices=[], value=[])

def _guess_mime(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    # Audio
    audio_map = {
        "mp3": "audio/mpeg",
        "wav": "audio/wav",
        "ogg": "audio/ogg",
        "opus": "audio/opus",
        "m4a": "audio/mp4",
        "aac": "audio/aac",
        "flac": "audio/flac",
        "wma": "audio/x-ms-wma",
        "amr": "audio/amr",
        "speex": "audio/speex",
    }
    # Video
    video_map = {
        "mp4": "video/mp4",
        "webm": "video/webm",
        "mov": "video/quicktime",
        "avi": "video/x-msvideo",
        "mkv": "video/x-matroska",
        "flv": "video/x-flv",
        "wmv": "video/x-ms-wmv",
        "3gp": "video/3gpp",
    }
    if ext in audio_map: return audio_map[ext]
    if ext in video_map: return video_map[ext]
    # Fallback via mimetypes
    return mimetypes.types_map.get("." + ext, "application/octet-stream")

def show_media_preview(file_path):
    """Render an audio/video preview player below the upload area."""
    if not file_path or not os.path.exists(file_path):
        return "<p style='text-align:center;color:#01579B;'>ยังไม่มีไฟล์ที่อัปโหลด</p>"

    safe_path = html.escape(file_path)  # prevent HTML injection via path
    ext = os.path.splitext(file_path)[1].lower()
    mime = _guess_mime(ext)

    audio_exts = {".mp3", ".wav", ".ogg", ".opus", ".m4a", ".aac", ".flac", ".wma", ".amr", ".speex"}
    video_exts = {".mp4", ".webm", ".mov", ".avi", ".mkv", ".flv", ".wmv", ".3gp"}

    if ext in audio_exts:
        # ✅ IMPORTANT: use file={path}, not file/{path}
        return f"""
        <audio controls style='width:100%;border-radius:8px;margin-top:10px;'>
            <source src="file={safe_path}" type="{mime}">
            เบราว์เซอร์ของคุณไม่รองรับการเล่นเสียง
        </audio>
        """
    elif ext in video_exts:
        return f"""
        <video controls style='width:100%;border-radius:8px;margin-top:10px;max-height:360px;'>
            <source src="file={safe_path}" type="{mime}">
            เบราว์เซอร์ของคุณไม่รองรับการเล่นวิดีโอ
        </video>
        """
    else:
        return "<p style='text-align:center;color:#01579B;'>ไม่สามารถแสดงตัวอย่างไฟล์นี้ได้</p>"

def check_ai_summary_status(summary_job_state, user, session_token=None):
    """Check status of AI summary job"""
    user = _ensure_user(user, session_token)
    if not user:
        return (
            "❌ Please log in to check AI summary status", 
            "",
            gr.update(value=""), gr.update(visible=False),
            "",
            gr.update(value=""),
            gr.update()
        )
    
    if not summary_job_state:
        return (
            "No active AI summary job", 
            "",
            gr.update(value=""), gr.update(visible=False),
            "",
            gr.update(value=""),
            gr.update()
        )
    
    try:
        # Handle special case: waiting for transcription to complete
        if summary_job_state.get('waiting_for_transcription'):
            transcription_job_id = summary_job_state.get('transcription_job_id')
            if not transcription_job_id:
                return (
                    "❌ Error: Missing transcription job ID", 
                    "",
                    gr.update(value=""), gr.update(visible=False),
                    "",
                    gr.update(value=""),
                    gr.update()
                )
            
            # Check transcription status
            transcription_job = transcription_manager.get_job_status(transcription_job_id)
            if not transcription_job:
                return (
                    "❌ Transcription job not found", 
                    "",
                    gr.update(value=""), gr.update(visible=False),
                    "",
                    gr.update(value=""),
                    gr.update()
                )
            
            processing_time = format_processing_time(summary_job_state['start_time'])
            
            if transcription_job.status == 'pending':
                return (
                    f"⏳ Transcription queued... ({processing_time} elapsed)\n📡 Auto-refreshing every 10 seconds...",
                    "",
                    gr.update(value=""), gr.update(visible=False),
                    f"Transcription: {transcription_job.original_filename}",
                    gr.update(value=_refresh_html("🔄 Waiting for transcription")),
                    get_user_stats_display(user)
                )
            
            elif transcription_job.status == 'processing':
                transcription_time = format_processing_time(transcription_job.created_at)
                return (
                    f"🎙️ Transcribing... ({transcription_time} transcribing, {processing_time} total)\n📡 Auto-refreshing every 10 seconds...",
                    "",
                    gr.update(value=""), gr.update(visible=False),
                    f"Transcribing: {transcription_job.original_filename}",
                    gr.update(value=_refresh_html("🔄 Transcription in progress")),
                    get_user_stats_display(user)
                )
            
            elif transcription_job.status == 'failed':
                return (
                    f"❌ Transcription failed - Cannot proceed\nError: {transcription_job.error_message or 'Unknown error'}",
                    "",
                    gr.update(value=""), gr.update(visible=False),
                    f"Failed: {transcription_job.original_filename}",
                    gr.update(value=""),
                    get_user_stats_display(user)
                )
            
            elif transcription_job.status == 'completed':
                if not transcription_job.transcript_text or not transcription_job.transcript_text.strip():
                    return (
                        f"🔄 Transcription completed, retrieving text... ({processing_time} elapsed)\n📡 Auto-refreshing every 10 seconds...",
                        "",
                        gr.update(value=""), gr.update(value=""), gr.update(visible=False),
                        f"Getting transcript: {transcription_job.original_filename}",
                        gr.update(value=_refresh_html("🔄 Getting transcript")),
                        get_user_stats_display(user)
                    )
                
                # TRANSCRIPTION IS COMPLETE! NOW TRIGGER AI SUMMARY IMMEDIATELY
                print("✅ Transcription completed, triggering AI summary immediately...")
                
                try:
                    # Prepare transcript IDs including the newly completed one
                    transcript_ids = summary_job_state.get('existing_transcripts', [])
                    transcript_ids.append(transcription_job_id)
                    
                    # Prepare settings
                    settings = {
                        'content_mode': "New Audio/Video Files",
                        'format': summary_job_state.get('summary_format', 'บทสรุปผู้บริหาร'),
                        'output_language': summary_job_state.get('output_language', 'Thai'),
                        'focus_areas': summary_job_state.get('focus_areas', ''),
                        'include_timestamps': summary_job_state.get('include_timestamps', True),
                        'include_action_items': summary_job_state.get('include_action_items', True),
                        'language': "th-TH"
                    }
                    
                    # Submit AI summary job NOW with completed transcript
                    job_id = ai_summary_manager.submit_summary_job_enhanced(
                        user_id=summary_job_state['user_id'],
                        content_mode="New Audio/Video Files",
                        summary_type=summary_job_state.get('summary_format', 'บทสรุปผู้บริหาร'),
                        user_prompt=summary_job_state.get('ai_instructions', ''),
                        existing_transcript_ids=transcript_ids,
                        audio_video_files=[],
                        document_files=summary_job_state.get('document_image_files', []),
                        settings=settings
                    )
                    
                    print(f"🤖 AI Summary job created immediately: {job_id}")
                    
                    # Update job state to track AI summary instead of transcription
                    summary_job_state.update({
                        'waiting_for_transcription': False,
                        'current_summary_job_id': job_id,
                        'transcription_completed_at': datetime.now().isoformat(),
                        'last_status': 'ai_started'
                    })
                    
                    return (
                        f"✅ Transcription done! 🤖 AI Summary started immediately\n📊 Using transcript: {len(transcription_job.transcript_text):,} characters\n📡 Auto-refreshing every 10 seconds...",
                        "",
                        gr.update(value=""), gr.update(value=""), gr.update(visible=False),
                        f"AI Processing: {transcription_job.original_filename}",
                        gr.update(value=_refresh_html("🔄 AI Summary active")),
                        get_user_stats_display(user)
                    )
                    
                except Exception as e:
                    print(f"❌ Error triggering AI summary: {str(e)}")
                    return (
                        f"❌ Transcription completed but AI summary failed to start: {str(e)}",
                        "",
                        gr.update(value=""), gr.update(value=""), gr.update(visible=False),
                        "AI Summary creation failed",
                        gr.update(value=""),
                        get_user_stats_display(user)
                    )
        
        # Normal AI summary job monitoring
        if 'current_summary_job_id' not in summary_job_state:
            return (
                "No active AI summary job", 
                "",
                gr.update(value=""), gr.update(visible=False),
                "",
                gr.update(value=""),
                gr.update()
            )
        
        job_id = summary_job_state['current_summary_job_id']
        job = ai_summary_manager.get_summary_status(job_id)
        
        if not job or job.user_id != user.user_id:
            return (
                "AI summary job not found or access denied", 
                "",
                gr.update(value=""), gr.update(visible=False),
                "",
                gr.update(value=""),
                gr.update()
            )
        
        # Calculate processing time
        processing_time = format_processing_time(job.created_at, job.completed_at)
        
        # Enhanced status change logging
        last_status = summary_job_state.get('last_status', '')
        if job.status != last_status:
            print(f"🔄 [{user.username}] AI Summary status: {last_status} → {job.status}")
            summary_job_state['last_status'] = job.status
        
        # Get updated user stats
        stats_display = get_user_stats_display(user)
        
        # Handle completed status
        if job.status == 'completed' and job.summary_text and job.summary_text.strip():
            # Job is complete
            
            # Create downloadable file
            try:
                summary_file = create_summary_file(job.summary_text, job_id)
                print(f"✅ [{user.username}] AI Summary ready: {len(job.summary_text)} characters")
            except Exception as e:
                print(f"⚠️ [{user.username}] Error creating summary file: {str(e)}")
                summary_file = None
            
            total_time = format_processing_time(summary_job_state['start_time'])
            return (
                f"✅ AI Summary completed! Total time: {total_time}\n📊 Generated: {len(job.summary_text):,} characters",
                job.summary_text,
                gr.update(value=_download_info_html(summary_file) if summary_file else ""),
                gr.update(visible=True, value=summary_file) if summary_file else gr.update(visible=False),
                f"Completed: {', '.join(job.original_files)}",
                gr.update(value=""),  # Hide auto-refresh
                stats_display
            )
        
        elif job.status == 'failed':
            # Job failed
            error_msg = job.error_message[:100] + "..." if job.error_message else "Unknown error"
            total_time = format_processing_time(summary_job_state['start_time'])
            return (
                f"❌ AI Summary failed after {total_time}",
                "",
                gr.update(value=""), gr.update(visible=False),
                f"Error: {error_msg}",
                gr.update(value=""),  # Hide auto-refresh
                stats_display
            )
        
        elif job.status == 'processing':
            # Still processing
            return (
                f"🤖 AI analyzing and generating summary... ({processing_time} AI processing)\n📊 Creating comprehensive analysis\n📡 Auto-refreshing every 10 seconds...",
                "",
                gr.update(value=""), gr.update(visible=False),
                f"AI Processing: {', '.join(job.original_files[:2])}{'...' if len(job.original_files) > 2 else ''}",
                gr.update(value=_refresh_html("🔄 AI generating summary")),
                stats_display
            )
        
        else:  # pending
            # Still pending
            return (
                f"⏳ AI Summary queued... ({processing_time} waiting)\n📡 Auto-refreshing every 10 seconds...",
                "",
                gr.update(value=""), gr.update(visible=False),
                f"Queued: {', '.join(job.original_files[:2])}{'...' if len(job.original_files) > 2 else ''}",
                gr.update(value=_refresh_html("🔄 AI queued")),
                stats_display
            )
        
    except Exception as e:
        print(f"❌ Error checking AI summary status: {str(e)}")
        return (
            f"Error checking AI summary status: {str(e)}", 
            "",
            gr.update(value=""), gr.update(visible=False),
            "",
            gr.update(value=""),
            gr.update()
        )

def should_auto_refresh(job_state, user):
    """Check if auto-refresh should be active"""
    if not user or not job_state or not job_state.get('auto_refresh_active', False):
        return False
    
    if 'current_job_id' not in job_state:
        return False
    
    job_id = job_state['current_job_id']
    
    try:
        job = transcription_manager.get_job_status(job_id)
        
        if not job or job.user_id != user.user_id:
            return False
        
        if job.status == 'failed':
            return False
        elif job.status == 'completed':
            if job.transcript_text and job.transcript_text.strip():
                return False
            else:
                return True
        elif job.status in ('pending', 'processing', 'processing_gpt4o'):
            return True
        else:
            return False
            
    except Exception as e:
        print(f"❌ Error in should_auto_refresh: {str(e)}")
        return True

def should_auto_refresh_summary(summary_job_state, user):
    """Check if AI summary auto-refresh should be active"""
    if not user or not summary_job_state or not summary_job_state.get('auto_refresh_active', False):
        return False
    
    if 'current_summary_job_id' not in summary_job_state:
        return False
    
    job_id = summary_job_state['current_summary_job_id']
    
    try:
        job = ai_summary_manager.get_summary_status(job_id)
        
        if not job or job.user_id != user.user_id:
            return False
        
        if job.status in ['failed', 'completed']:
            return False
        else:
            return True
            
    except Exception as e:
        print(f"❌ Error in should_auto_refresh_summary: {str(e)}")
        return True

def auto_refresh_status(job_state, user, session_token=None):
    """Auto-refresh function for transcription — checks status and hides indicator when done"""
    user = _ensure_user(user, session_token)
    if not user:
        return (
            gr.update(),
            gr.update(),
            gr.update(), gr.update(),
            gr.update(),
            gr.update(value=""),
            gr.update()
        )
    
    if job_state and job_state.get('current_job_id'):
        return check_current_job_status(job_state, user, session_token)
    else:
        return (
            gr.update(),
            gr.update(),
            gr.update(), gr.update(),
            gr.update(),
            gr.update(value=""),
            gr.update()
        )

def auto_refresh_ai_summary(summary_job_state, user, session_token=None):
    """Auto-refresh function for AI summary — checks status and hides indicator when done"""
    user = _ensure_user(user, session_token)
    if not user:
        return (
            gr.update(),
            gr.update(),
            gr.update(), gr.update(),
            gr.update(),
            gr.update(value=""),
            gr.update()
        )
    
    if summary_job_state and (summary_job_state.get('current_summary_job_id') or summary_job_state.get('waiting_for_transcription')):
        return check_ai_summary_status(summary_job_state, user, session_token)
    else:
        return (
            gr.update(),
            gr.update(),
            gr.update(), gr.update(), gr.update(),
            gr.update(),
            gr.update(value=""),
            gr.update()
        )

# History Functions
def get_transcription_history_table(user, show_all=False):
    """Get transcription history table with Bangkok timezone and transcription method"""
    if not user:
        return []
    
    try:
        limit = 100 if show_all else 20
        transcript_jobs = transcription_manager.get_user_history(user.user_id, limit=limit)
        
        table_data = []
        for job in transcript_jobs:
            try:
                created_time = to_bangkok(job.created_at)
                formatted_date = created_time.strftime("%Y-%m-%d %H:%M")
            except Exception:
                formatted_date = job.created_at[:16]
            
            status_display = format_status(job.status)
            time_display = format_processing_time(job.created_at, job.completed_at)
            job_id_display = job.job_id[:8] + "..." if len(job.job_id) > 8 else job.job_id
            language_display = ALLOWED_LANGS.get(job.language, job.language)
            
            # Determine transcription method
            method = "Azure STT"
            if job.settings:
                tm = job.settings.get('transcription_method', '')
                if ('gpt-4o' in tm or 'llm' in tm.lower()
                        or job.settings.get('llm_correction', False)):
                    method = METHOD_LLM
            if job.status == 'processing_gpt4o':
                method = METHOD_LLM
            
            if job.status == 'completed' and job.transcript_text:
                download_status = "✅ Download"
            else:
                download_status = status_display
            
            table_data.append([
                formatted_date,
                job.original_filename,
                language_display,
                method,
                status_display,
                time_display,
                job_id_display,
                download_status
            ])
        
        return table_data
        
    except Exception as e:
        print(f"❌ Error loading transcription history: {str(e)}")
        return []

def get_ai_summary_history_table(user, show_all=False):
    """Get AI summary history table with Bangkok timezone"""
    if not user:
        return []
    
    try:
        limit = 100 if show_all else 20
        summary_jobs = ai_summary_manager.get_user_summary_history(user.user_id, limit=limit)
        
        table_data = []
        for job in summary_jobs:
            try:
                created_time = to_bangkok(job.created_at)
                formatted_date = created_time.strftime("%Y-%m-%d %H:%M")
            except Exception:
                formatted_date = job.created_at[:16]
            
            status_display = format_status(job.status)
            time_display = format_processing_time(job.created_at, job.completed_at)
            job_id_display = job.job_id[:8] + "..." if len(job.job_id) > 8 else job.job_id
            
            # Get source summary
            source_summary = f"{len(job.original_files)} sources"
            if len(job.original_files) <= 2:
                source_summary = ", ".join([f[:20] + "..." if len(f) > 20 else f for f in job.original_files])
            
            if job.status == 'completed' and job.summary_text:
                download_status = "Available"
            else:
                download_status = status_display
            
            table_data.append([
                formatted_date,
                source_summary,
                job.settings.get('output_language', 'Thai') if job.settings else 'Thai',
                status_display,
                time_display,
                job_id_display,
                download_status
            ])
        
        return table_data
        
    except Exception as e:
        print(f"❌ Error loading AI summary history: {str(e)}")
        return []

# app_func.py - Updated history functions

def refresh_transcription_history(user, show_all=False, session_token=None):
    """Refresh transcription history from blob storage with downloadable files"""
    user = _ensure_user(user, session_token)
    if not user:
        empty_outputs = [[], gr.update()] + [gr.update(visible=False)] * 51
        return empty_outputs
    
    try:
        table_data = get_transcription_history_table(user, show_all)
        stats_display = get_user_stats_display(user)
        
        # Get completed transcription jobs from the last 30 days directly from blob storage
        all_jobs = transcription_manager.blob_storage.get_user_transcription_history(user.user_id, limit=200)
        
        # Filter for completed jobs in the last 30 days (Bangkok time)
        thirty_days_ago = now_bangkok() - timedelta(days=30)
        
        completed_transcripts = [
            job for job in all_jobs 
            if job.status == 'completed' and job.transcript_text
            and to_bangkok(job.created_at) >= thirty_days_ago
        ]
        
        print(f"📥 [{user.username}] Found {len(completed_transcripts)} completed transcripts from blob storage (last 30 days)")
        
        # Create download files (stored in temp only for download, not for persistence)
        download_updates = []
        download_updates.append(gr.update(visible=False))  # ZIP file initially hidden
        
        # Individual files
        for i in range(50):
            if i < len(completed_transcripts):
                job = completed_transcripts[i]
                try:
                    # Create temp file for download only
                    file_path = create_transcript_file(job.transcript_text, job.job_id)
                    bkk_time = to_bangkok(job.created_at)
                    date_str = bkk_time.strftime('%Y-%m-%d %H:%M')
                    filename_short = job.original_filename[:25] + "..." if len(job.original_filename) > 25 else job.original_filename
                    # Show method tag in download label
                    method_tag = "🤖LLM" if (job.settings and (job.settings.get('llm_correction', False) or 'gpt-4o' in job.settings.get('transcription_method', ''))) else "STT"
                    label = f"📄 [{method_tag}] {date_str} - {filename_short}"
                    download_updates.append(gr.update(visible=True, value=file_path, label=label))
                except Exception as e:
                    print(f"Error creating download file: {e}")
                    download_updates.append(gr.update(visible=False))
            else:
                download_updates.append(gr.update(visible=False))
        
        return [table_data, stats_display] + download_updates
        
    except Exception as e:
        print(f"❌ Error refreshing history: {str(e)}")
        empty_outputs = [[], gr.update()] + [gr.update(visible=False)] * 51
        return empty_outputs


def refresh_ai_summary_history(user, show_all=False, session_token=None):
    """Refresh AI summary history from blob storage with downloadable files"""
    user = _ensure_user(user, session_token)
    if not user:
        empty_outputs = [[], gr.update()] + [gr.update(visible=False)] * 51
        return empty_outputs
    
    try:
        table_data = get_ai_summary_history_table(user, show_all)
        stats_display = get_user_stats_display(user)
        
        # Get completed AI summary jobs from the last 30 days directly from blob storage
        all_jobs = transcription_manager.blob_storage.get_user_summary_history(user.user_id, limit=200)
        
        thirty_days_ago = now_bangkok() - timedelta(days=30)
        
        completed_summaries = [
            job for job in all_jobs 
            if job.status == 'completed' and job.summary_text
            and to_bangkok(job.created_at) >= thirty_days_ago
        ]
        
        print(f"📥 [{user.username}] Found {len(completed_summaries)} completed summaries from blob storage (last 30 days)")
        
        # Create download files
        download_updates = []
        download_updates.append(gr.update(visible=False))  # ZIP file initially hidden
        
        for i in range(50):
            if i < len(completed_summaries):
                job = completed_summaries[i]
                try:
                    file_path = create_summary_file(job.summary_text, job.job_id)
                    bkk_time = to_bangkok(job.created_at)
                    date_str = bkk_time.strftime('%Y-%m-%d %H:%M')
                    source_name = job.original_files[0][:30] if job.original_files else "Summary"
                    label = f"🤖 {date_str} - {source_name}"
                    download_updates.append(gr.update(visible=True, value=file_path, label=label))
                except Exception as e:
                    print(f"Error creating download file: {e}")
                    download_updates.append(gr.update(visible=False))
            else:
                download_updates.append(gr.update(visible=False))
        
        return [table_data, stats_display] + download_updates
        
    except Exception as e:
        print(f"❌ Error refreshing summary history: {str(e)}")
        empty_outputs = [[], gr.update()] + [gr.update(visible=False)] * 51
        return empty_outputs

# PDPA Compliance Functions
def export_user_data(user, session_token=None):
    """Export comprehensive user data including summaries"""
    user = _ensure_user(user, session_token)
    if not user:
        return "❌ Please log in to export your data", gr.update(visible=False)
    
    try:
        # Export transcript data
        transcript_export = transcription_manager.export_user_data(user.user_id)
        
        # Export AI summary data (if available)
        try:
            summary_export = {
                'ai_summaries': [job.__dict__ for job in ai_summary_manager.get_user_summary_history(user.user_id, limit=1000)],
                'summary_stats': transcription_manager.get_user_summary_stats(user.user_id)
            }
        except Exception:
            summary_export = {'ai_summaries': [], 'summary_stats': {}}
        
        # Combine exports
        combined_export = {
            **transcript_export,
            **summary_export,
            'export_type': 'comprehensive_azure_ai_service',
            'services': ['transcription', 'ai_summarization']
        }
        
        # Create export file
        os.makedirs("temp", exist_ok=True)
        filename = f"temp/user_data_export_{user.user_id}_{now_bangkok().strftime('%Y%m%d_%H%M%S')}.json"
        
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(combined_export, f, indent=2, ensure_ascii=False, default=str)
        
        print(f"📦 [{user.username}] Comprehensive data export created")
        return "✅ Your complete data (transcripts + AI summaries) has been exported successfully", gr.update(visible=True, value=filename, label="Download Your Complete Data Export")
        
    except Exception as e:
        print(f"❌ Error exporting comprehensive user data: {str(e)}")
        return f"❌ Export failed: {str(e)}", gr.update(visible=False)

def update_marketing_consent(user, marketing_consent, session_token=None):
    """Update user's marketing consent"""
    user = _ensure_user(user, session_token)
    if not user:
        return "❌ Please log in to update consent"
    
    try:
        success = transcription_manager.update_user_consent(user.user_id, marketing_consent)
        if success:
            user.marketing_consent = marketing_consent
            print(f"📧 [{user.username}] Marketing consent updated: {marketing_consent}")
            return "✅ Marketing consent updated successfully"
        else:
            return "❌ Failed to update consent"
    except Exception as e:
        return f"❌ Error: {str(e)}"

def delete_user_account(user, confirmation_text, session_token=None):
    """Delete user account and all data (transcripts + summaries)"""
    user = _ensure_user(user, session_token)
    if not user:
        return "❌ Please log in to delete account", None, gr.update(visible=True), gr.update(visible=False)
    
    if confirmation_text != "DELETE MY ACCOUNT":
        return "❌ Please type 'DELETE MY ACCOUNT' to confirm", user, gr.update(visible=False), gr.update(visible=True)
    
    try:
        # Delete transcript data
        success = transcription_manager.delete_user_account(user.user_id)
        
        # Delete AI summary data (if backend supports it)
        try:
            transcription_manager.delete_user_summary_data(user.user_id)
        except Exception as e:
            print(f"⚠️ Warning: Could not delete AI summary data: {e}")
        
        if success:
            print(f"🗑️ [{user.username}] Complete account deleted (transcripts + AI summaries)")
            return "✅ Your account and all data (transcripts + AI summaries) have been permanently deleted", None, gr.update(visible=True), gr.update(visible=False)
        else:
            return "❌ Failed to delete account", user, gr.update(visible=False), gr.update(visible=True)
    except Exception as e:
        return f"❌ Error: {str(e)}", user, gr.update(visible=False), gr.update(visible=True)

def on_user_login(user):
    """Update UI components when user logs in"""
    if user:
        return gr.update(value=user.marketing_consent)
    else:
        return gr.update(value=False)

def submit_ai_summary_new(transcript_text, transcript_file, document_files,
                          ai_instructions, summary_format, output_language,
                          include_timestamps, include_action_items, user, session_token=None):
    """Submit AI summary with new input method (paste text or upload file)"""
    user = _ensure_user(user, session_token)
    if not user:
        return (
            "❌ Please log in to generate AI summaries",
            "",
            gr.update(value=""), gr.update(visible=False),
            "",
            {},
            gr.update(value=""),
            gr.update()
        )
    
    # Get transcript content from either text input or file
    transcript_content = ""
    source_filename = "pasted_transcript"
    
    if transcript_text and transcript_text.strip():
        transcript_content = transcript_text.strip()
        source_filename = "pasted_transcript"
    elif transcript_file:
        try:
            with open(transcript_file, 'r', encoding='utf-8') as f:
                transcript_content = f.read()
            source_filename = os.path.basename(transcript_file)
        except Exception as e:
            return (
                f"❌ Error reading transcript file: {str(e)}",
                "",
                gr.update(value=""), gr.update(visible=False),
                "",
                {},
                gr.update(value=""),
                gr.update()
            )
    
    if not transcript_content:
        return (
            "❌ Please provide transcript text (paste or upload file)",
            "",
            gr.update(value=""), gr.update(visible=False),
            "",
            {},
            gr.update(value=""),
            gr.update()
        )
    
    # Use format-specific default prompt if user left instructions empty
    if not ai_instructions or not ai_instructions.strip():
        default_prompts = {
            "รายงานการประชุมภายใน": (
                "สรุปครบถ้วน เป็นทางการ แบ่งช่วงเวลา ระบุผู้รับผิดชอบ มติ/การตัดสินใจ "
                "จัดกลุ่มประเด็นตามวาระ พร้อม Next Steps จัดกลุ่มตามผู้รับผิดชอบ "
                "ห้ามตกหล่นชื่อโปรเจกต์ ระบบ เครื่องมือ ตัวเลข"
            ),
            "บทสรุปสำหรับผู้บริหาร": (
                "สรุปกระชับ เน้นมติสำคัญ Action Items ประเด็นติดตาม "
                "เหมาะสำหรับผู้บริหารอ่านเร็ว ระบุผลลัพธ์หลักและตัวเลขสำคัญ "
                "ห้ามตกหล่นการตัดสินใจหรือกำหนดเวลา"
            ),
            "รายงานการประชุมภายนอก": (
                "สรุปทางการ ระบุหน่วยงาน ผู้เข้าร่วม มติร่วม ข้อตกลง "
                "แบ่งช่วงเวลา จัดกลุ่มประเด็นตามวาระ "
                "พร้อม Next Steps จัดกลุ่มตามผู้รับผิดชอบและหน่วยงาน"
            ),
            "บทสรุปการเรียนรู้หรืองานสัมมนา": (
                "สรุปประเด็นเรียนรู้ ผู้บรรยาย เครื่องมือ/ลิงก์อ้างอิง Use Cases "
                "แบ่งช่วงเวลาตาม session พร้อมแหล่งข้อมูลเพิ่มเติม "
                "ห้ามตกหล่นข้อมูลเชิงเทคนิค สถิติ หรือ Pain Points"
            ),
            "ทั่วไป": (
                "สรุปครบถ้วน กระชับ เป็นทางการ ครอบคลุมทุกประเด็น "
                "พร้อม Action Items ผู้รับผิดชอบ และกำหนดเวลา "
                "ห้ามตกหล่นรายละเอียดสำคัญ เช่น ตัวเลข สถิติ ชื่อระบบ เครื่องมือ"
            ),
            "custom_format": (
                "ปฏิบัติตามคำสั่งที่ผู้ใช้ระบุอย่างเคร่งครัด "
                "ใช้รูปแบบ โครงสร้าง และข้อกำหนดตามที่ผู้ใช้กำหนด"
            ),
            "no_format": (
                "สรุปเป็นข้อความล้วน อ่านง่าย สละสลวย ครอบคลุมทุกประเด็นสำคัญ "
                "จัดเรียงตามลำดับเวลาหรือความสำคัญ ไม่ต้องใช้ตารางหรือหัวข้อ"
            ),
        }
        ai_instructions = default_prompts.get(summary_format,
            "สรุปครบถ้วน กระชับ เป็นทางการ ครอบคลุมทุกประเด็น "
            "พร้อม Action Items ผู้รับผิดชอบ และกำหนดเวลา "
            "ห้ามตกหล่นรายละเอียดสำคัญ เช่น ตัวเลข สถิติ ชื่อระบบ เครื่องมือ"
        )
    
    try:
        # Prepare settings
        settings = {
            'content_mode': 'Text Input',
            'format': summary_format,
            'output_language': output_language,
            'include_timestamps': include_timestamps,
            'include_action_items': include_action_items,
            'source_filename': source_filename
        }
        
        # Create a temporary file with the transcript
        import tempfile
        temp_transcript = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8')
        temp_transcript.write(transcript_content)
        temp_transcript.close()
        
        # Prepare document files
        doc_files = []
        if document_files:
            if isinstance(document_files, list):
                doc_files = document_files
            else:
                doc_files = [document_files]
        
        # Submit AI summary job
        job_id = ai_summary_manager.submit_summary_job(
            user_id=user.user_id,
            summary_type=summary_format,
            user_prompt=ai_instructions,
            files=[temp_transcript.name] + [f for f in doc_files if f],
            transcript_job_ids=[],
            settings=settings
        )
        
        # Update job state
        summary_job_state = {
            'current_summary_job_id': job_id,
            'start_time': datetime.now().isoformat(),
            'auto_refresh_active': True,
            'last_status': 'pending',
            'source_filename': source_filename
        }
        
        # Get updated user stats
        stats_display = get_user_stats_display(user)
        
        # Create source description
        source_desc = f"transcript: {source_filename}"
        if doc_files:
            source_desc += f" + {len(doc_files)} document(s)"
        
        return (
            f"🤖 AI Summary started with {source_desc}\n📡 Auto-refreshing every 10 seconds...",
            "",
            gr.update(value=""), gr.update(visible=False),
            f"AI Job ID: {job_id}",
            summary_job_state,
            gr.update(value=_refresh_html("🔄 AI Auto-refresh active")),
            stats_display
        )
        
    except Exception as e:
        print(f"❌ Error submitting AI summary: {str(e)}")
        return (
            f"❌ Error: {str(e)}",
            "",
            gr.update(value=""), gr.update(visible=False),
            "",
            {},
            gr.update(value=""),
            gr.update()
        )

def create_transcript_file(transcript_text, job_id):
    """Create a downloadable transcript file with readable name"""
    os.makedirs("temp", exist_ok=True)
    ts = now_bangkok().strftime('%Y%m%d_%H%M%S')
    
    # Try to get original filename from job
    try:
        job = transcription_manager.get_job_status(job_id)
        if job and job.original_filename:
            # Clean filename and add transcript suffix
            base_name = os.path.splitext(job.original_filename)[0]
            # Remove special characters
            import re
            clean_name = re.sub(RE_CLEAN_SPECIAL, '', base_name)
            clean_name = re.sub(RE_CLEAN_SPACES, '_', clean_name)
            filename = f"temp/transcript_{clean_name}_{ts}.txt"
        else:
            filename = f"temp/transcript_{job_id[:8]}_{ts}.txt"
    except Exception:
        filename = f"temp/transcript_{job_id[:8]}_{ts}.txt"
    
    with open(filename, "w", encoding="utf-8") as f:
        f.write(transcript_text)
    
    return filename

def create_summary_file(summary_text, job_id):
    """Create a downloadable AI summary file with readable name"""
    os.makedirs("temp", exist_ok=True)
    ts = now_bangkok().strftime('%Y%m%d_%H%M%S')
    
    # Try to get source information from job
    try:
        job = ai_summary_manager.get_summary_status(job_id)
        if job and job.settings and 'source_filename' in job.settings:
            source_name = job.settings['source_filename']
            # Clean filename
            import re
            clean_name = re.sub(RE_CLEAN_SPECIAL, '', source_name)
            clean_name = re.sub(RE_CLEAN_SPACES, '_', clean_name)
            filename = f"temp/ai_summary_{clean_name}_{ts}.txt"
        elif job and job.original_files and len(job.original_files) > 0:
            first_file = job.original_files[0]
            # Extract name
            if isinstance(first_file, str):
                base_name = first_file.split('/')[-1].split('.')[0]
            else:
                base_name = "meeting"
            import re
            clean_name = re.sub(RE_CLEAN_SPECIAL, '', base_name)
            clean_name = re.sub(RE_CLEAN_SPACES, '_', clean_name)
            filename = f"temp/ai_summary_{clean_name}_{ts}.txt"
        else:
            filename = f"temp/ai_summary_{job_id[:8]}_{ts}.txt"
    except Exception:
        filename = f"temp/ai_summary_{job_id[:8]}_{ts}.txt"
    
    with open(filename, "w", encoding="utf-8") as f:
        f.write(summary_text)
    
    return filename

def login_user_with_session(login, password):
    """Login user and create OAuth2-style session ticket"""
    try:
        print(f"🔐 Login attempt for: {login}")
        
        if not login or not password:
            return ("❌ Please enter both username/email and password", None, "", 
                   gr.update(), gr.update(visible=True), gr.update(visible=False), 
                   MSG_LOGIN_STATS)
        
        success, message, user = transcription_manager.login_user(login, password)
        print(f"🔐 Login result: success={success}, message={message}")
        
        if success and user:
            # Create OAuth2-style session ticket (valid for 60 minutes of inactivity)
            ticket_token = session_manager.create_session(user, initial_tab="transcription")
            
            print(f"✅ User logged in successfully: {user.username}")
            print(f"🎫 Session ticket: {ticket_token[:12]}... (60min validity)")
            
            stats_display = get_user_stats_display(user)
            
            # Return ticket_token for JavaScript to store in localStorage
            return (
                f"✅ Welcome back, {user.username}! (Session valid for 60 minutes)",
                user,
                ticket_token,
                ticket_token,  # This will be stored in browser via JavaScript
                gr.update(visible=False),
                gr.update(visible=True),
                stats_display
            )
        else:
            print(f"❌ Login failed: {message}")
            return (f"❌ {message}", None, "", gr.update(), 
                   gr.update(visible=True), gr.update(visible=False), 
                   MSG_LOGIN_STATS)
            
    except Exception as e:
        print(f"❌ Login error: {str(e)}")
        return (f"❌ Login error: {str(e)}", None, "", gr.update(), 
               gr.update(visible=True), gr.update(visible=False), 
               MSG_LOGIN_STATS)

def logout_user_with_session(ticket_token):
    """Logout user and invalidate session ticket"""
    try:
        if ticket_token:
            session_manager.invalidate_session(ticket_token)
            print(f"👋 User logged out, session ticket invalidated: {ticket_token[:12]}...")
        
        return (
            None,  # current_user
            "",    # session_id_state
            "",    # session_id_input (triggers JS to clear localStorage)
            "👋 You have been logged out. Please log in to continue.",
            gr.update(visible=True),   # auth_section
            gr.update(visible=False),  # main_app
            MSG_LOGIN_STATS
        )
    except Exception as e:
        print(f"❌ Logout error: {str(e)}")
        return (None, "", "", f"❌ Logout error: {str(e)}", 
               gr.update(visible=True), gr.update(visible=False), 
               MSG_LOGIN_STATS)

def restore_session_on_load(stored_ticket_token):
    """Restore session from browser storage on page load/refresh
    
    This is the key function that allows sessions to survive page refresh.
    The 60-minute ticket is validated server-side and restored if still valid.
    """
    try:
        if not stored_ticket_token or stored_ticket_token.strip() == "":
            # No ticket to restore
            return (
                None,
                "",
                gr.update(visible=True),
                gr.update(visible=False),
                MSG_LOGIN_STATS
            )
        
        print(f"🔄 Restoring session from ticket: {stored_ticket_token[:12]}...")
        
        # Validate ticket with server (checks 60-minute inactivity)
        user = session_manager.validate_session(stored_ticket_token)
        
        if user:
            print(f"✅ Session ticket valid, restored: {user.username}")
            stats_display = get_user_stats_display(user)
            
            return (
                user,
                stored_ticket_token,
                gr.update(visible=False),
                gr.update(visible=True),
                stats_display
            )
        else:
            print(f"❌ Session ticket invalid or expired: {stored_ticket_token[:12]}...")
            return (
                None,
                "",
                gr.update(visible=True),
                gr.update(visible=False),
                "👤 Session expired (60 minutes). Please log in again."
            )
            
    except Exception as e:
        print(f"❌ Session restore error: {str(e)}")
        return (
            None,
            "",
            gr.update(visible=True),
            gr.update(visible=False),
            MSG_LOGIN_STATS
        )

def check_session_validity(ticket_token):
    """Periodically check if session ticket is still valid (60-min timeout)"""
    try:
        if not ticket_token or ticket_token.strip() == "":
            # No ticket to check
            return (
                gr.update(),  # Don't change current_user
                gr.update(),  # Don't change auth_section
                gr.update(),  # Don't change main_app
                gr.update()   # Don't change user_stats_display
            )
        
        # Validate and refresh session (resets 60-min timer on server)
        user = session_manager.validate_session(ticket_token)
        
        if user:
            # Session ticket is still valid
            stats_display = get_user_stats_display(user)
            return (
                user,
                gr.update(visible=False),
                gr.update(visible=True),
                stats_display
            )
        else:
            # Session ticket expired - force logout
            print(f"⏰ Session ticket expired during check: {ticket_token[:12]}...")
            return (
                None,
                gr.update(visible=True),
                gr.update(visible=False),
                "⏰ Session expired. Please log in again."
            )
            
    except Exception as e:
        print(f"❌ Session check error: {str(e)}")
        return (
            gr.update(),
            gr.update(),
            gr.update(),
            gr.update()
        )

def create_transcript_zip_archive(user, session_token=None):
    """Create ZIP archive of all transcripts from the last 30 days"""
    user = _ensure_user(user, session_token)
    if not user:
        return gr.update(visible=False)
    
    try:
        # Get completed transcription jobs from the last 30 days
        all_jobs = transcription_manager.get_user_history(user.user_id, limit=200)
        thirty_days_ago = now_bangkok() - timedelta(days=30)
        
        completed_transcripts = [
            job for job in all_jobs 
            if job.status == 'completed' and job.transcript_text
            and to_bangkok(job.created_at) >= thirty_days_ago
        ]
        
        if not completed_transcripts:
            print(f"⚠️ [{user.username}] No transcripts found in last 30 days")
            return gr.update(visible=False)
        
        # Create ZIP file
        os.makedirs("temp", exist_ok=True)
        timestamp = now_bangkok().strftime('%Y%m%d_%H%M%S')
        zip_filename = f"temp/transcripts_{user.username}_{timestamp}.zip"
        
        print(f"📦 Creating ZIP archive with {len(completed_transcripts)} transcripts...")
        
        with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for job in completed_transcripts:
                try:
                    # Create temp file for this transcript
                    temp_file = create_transcript_file(job.transcript_text, job.job_id)
                    
                    # Create a clean filename for ZIP entry
                    import re
                    date_str = job.created_at[:10].replace('-', '')
                    clean_name = re.sub(RE_CLEAN_SPECIAL, '', job.original_filename)
                    clean_name = re.sub(RE_CLEAN_SPACES, '_', clean_name)
                    
                    # Add to ZIP with organized name
                    zip_entry_name = f"{date_str}_{clean_name}_transcript.txt"
                    zipf.write(temp_file, zip_entry_name)
                    
                    # Clean up temp file
                    os.remove(temp_file)
                    
                except Exception as e:
                    print(f"⚠️ Error adding transcript to ZIP: {e}")
                    continue
        
        print(f"✅ ZIP archive created: {len(completed_transcripts)} files")
        return gr.update(visible=True, value=zip_filename, 
                        label=f"📦 {len(completed_transcripts)} Transcripts - Last 30 Days")
        
    except Exception as e:
        print(f"❌ Error creating transcript ZIP: {str(e)}")
        return gr.update(visible=False)


def create_summary_zip_archive(user, session_token=None):
    """Create ZIP archive of all AI summaries from the last 30 days"""
    user = _ensure_user(user, session_token)
    if not user:
        return gr.update(visible=False)
    
    try:
        # Get completed AI summary jobs from the last 30 days
        all_jobs = ai_summary_manager.get_user_summary_history(user.user_id, limit=200)
        thirty_days_ago = now_bangkok() - timedelta(days=30)
        
        completed_summaries = [
            job for job in all_jobs 
            if job.status == 'completed' and job.summary_text
            and to_bangkok(job.created_at) >= thirty_days_ago
        ]
        
        if not completed_summaries:
            print(f"⚠️ [{user.username}] No AI summaries found in last 30 days")
            return gr.update(visible=False)
        
        # Create ZIP file
        os.makedirs("temp", exist_ok=True)
        timestamp = now_bangkok().strftime('%Y%m%d_%H%M%S')
        zip_filename = f"temp/ai_summaries_{user.username}_{timestamp}.zip"
        
        print(f"📦 Creating ZIP archive with {len(completed_summaries)} AI summaries...")
        
        with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for job in completed_summaries:
                try:
                    # Create temp file for this summary
                    temp_file = create_summary_file(job.summary_text, job.job_id)
                    
                    # Create a clean filename for ZIP entry
                    import re
                    date_str = job.created_at[:10].replace('-', '')
                    
                    # Get source name
                    if job.original_files and len(job.original_files) > 0:
                        source = job.original_files[0]
                        if isinstance(source, str):
                            clean_name = re.sub(RE_CLEAN_SPECIAL, '', source.split('/')[-1].split('.')[0])
                        else:
                            clean_name = "summary"
                    else:
                        clean_name = "summary"
                    
                    clean_name = re.sub(RE_CLEAN_SPACES, '_', clean_name)
                    
                    # Add to ZIP with organized name
                    zip_entry_name = f"{date_str}_{clean_name}_ai_summary.txt"
                    zipf.write(temp_file, zip_entry_name)
                    
                    # Clean up temp file
                    os.remove(temp_file)
                    
                except Exception as e:
                    print(f"⚠️ Error adding summary to ZIP: {e}")
                    continue
        
        print(f"✅ ZIP archive created: {len(completed_summaries)} files")
        return gr.update(visible=True, value=zip_filename, 
                        label=f"📦 {len(completed_summaries)} AI Summaries - Last 30 Days")
        
    except Exception as e:
        print(f"❌ Error creating summary ZIP: {str(e)}")
        return gr.update(visible=False)

def request_password_reset(email_or_username):
    """Simplified password reset - finds user and allows immediate reset"""
    try:
        if not email_or_username or not email_or_username.strip():
            return "❌ Please enter your email or username", ""
        
        email_or_username = email_or_username.strip()
        
        # Find user in blob storage
        user = transcription_manager.blob_storage.find_user_by_email(email_or_username)
        if not user:
            user = transcription_manager.blob_storage.find_user_by_username(email_or_username)
        
        if not user:
            return "❌ User not found with that email or username", ""
        
        if not user.is_active:
            return "❌ This account is inactive. Please contact support.", ""
        
        # User found! Return success and show the user ID (masked for security)
        masked_email = user.email[:3] + "***" + user.email[user.email.index("@"):]
        return (
            f"✅ User found!\n\n"
            f"Account: {user.username}\n"
            f"Email: {masked_email}\n\n"
            f"You can now reset your password below.",
            user.user_id  # Return user_id for password reset
        )
            
    except Exception as e:
        print(f"❌ Password reset error: {str(e)}")
        return f"❌ Error: {str(e)}", ""

def reset_password_with_token(user_id_from_lookup, new_password, confirm_password):
    """Reset password directly after user verification"""
    try:
        if not user_id_from_lookup or not new_password or not confirm_password:
            return "❌ All fields are required", gr.update(visible=False)
        
        if new_password != confirm_password:
            return "❌ Passwords do not match", gr.update(visible=False)
        
        # Validate password strength
        from backend import AuthManager
        is_valid, message = AuthManager.validate_password(new_password)
        if not is_valid:
            return f"❌ {message}", gr.update(visible=False)
        
        # Get user from blob storage
        user = transcription_manager.blob_storage.get_user(user_id_from_lookup)
        if not user:
            return "❌ User session expired. Please search for your account again.", gr.update(visible=False)
        
        # Update password
        user.password_hash = AuthManager.hash_password(new_password)
        user.last_login = datetime.now().isoformat()
        
        # Save to blob storage
        if transcription_manager.blob_storage.save_user(user):
            print(f"✅ Password reset successful for: {user.username}")
            return "✅ Password reset successful!\n\nYou can now log in with your new password.", gr.update(visible=True)
        else:
            return "❌ Failed to save new password. Please try again.", gr.update(visible=False)
            
    except Exception as e:
        print(f"❌ Password reset error: {str(e)}")
        return f"❌ Error: {str(e)}", gr.update(visible=False)

def request_password_reset_ui(email_or_username):
    """UI-friendly password reset - finds user and shows form"""
    try:
        if not email_or_username or not email_or_username.strip():
            return (
                gr.update(value="❌ กรุณากรอกอีเมลหรือชื่อผู้ใช้", visible=True),
                gr.update(visible=False),
                ""
            )
        
        email_or_username = email_or_username.strip()
        
        # Find user in blob storage
        user = transcription_manager.blob_storage.find_user_by_email(email_or_username)
        if not user:
            user = transcription_manager.blob_storage.find_user_by_username(email_or_username)
        
        if not user:
            return (
                gr.update(value="❌ **ไม่พบบัญชีผู้ใช้**\n\nไม่พบบัญชีที่ตรงกับอีเมลหรือชื่อผู้ใช้นี้", visible=True),
                gr.update(visible=False),
                ""
            )
        
        if not user.is_active:
            return (
                gr.update(value="❌ **บัญชีไม่ active**\n\nบัญชีนี้ถูกปิดการใช้งาน กรุณาติดต่อผู้ดูแลระบบ", visible=True),
                gr.update(visible=False),
                ""
            )
        
        # User found! Show success and password form
        masked_email = user.email[:3] + "***" + user.email[user.email.index("@"):]
        success_message = f"""
        ✅ **พบบัญชีผู้ใช้แล้ว!**

        - **ชื่อผู้ใช้:** {user.username}
        - **อีเมล:** {masked_email}

        กรุณากรอกรหัสผ่านใหม่ด้านล่าง
        """
        
        return (
            gr.update(value=success_message, visible=True),
            gr.update(visible=True),  # Show password form
            user.user_id  # Store user_id for next step
        )
            
    except Exception as e:
        print(f"❌ Password reset error: {str(e)}")
        return (
            gr.update(value=f"❌ **เกิดข้อผิดพลาด**\n\n{str(e)}", visible=True),
            gr.update(visible=False),
            ""
        )

def reset_password_with_token_ui(user_id_from_lookup, new_password, confirm_password):
    """UI-friendly password reset with proper feedback"""
    try:
        if not user_id_from_lookup or not new_password or not confirm_password:
            return (
                gr.update(value="❌ กรุณากรอกข้อมูลให้ครบทุกช่อง", visible=True),
                gr.update(visible=False)
            )
        
        if new_password != confirm_password:
            return (
                gr.update(value="❌ **รหัสผ่านไม่ตรงกัน**\n\nกรุณากรอกรหัสผ่านให้ตรงกันทั้งสองช่อง", visible=True),
                gr.update(visible=False)
            )
        
        # Validate password strength
        from backend import AuthManager
        is_valid, message = AuthManager.validate_password(new_password)
        if not is_valid:
            return (
                gr.update(value=f"❌ **รหัสผ่านไม่ตรงตามเงื่อนไข**\n\n{message}", visible=True),
                gr.update(visible=False)
            )
        
        # Get user from blob storage
        user = transcription_manager.blob_storage.get_user(user_id_from_lookup)
        if not user:
            return (
                gr.update(value="❌ **ไม่พบข้อมูลผู้ใช้**\n\nกรุณาลองค้นหาบัญชีใหม่อีกครั้ง", visible=True),
                gr.update(visible=False)
            )
        
        # Update password
        user.password_hash = AuthManager.hash_password(new_password)
        user.last_login = datetime.now().isoformat()
        
        # Save to blob storage
        if transcription_manager.blob_storage.save_user(user):
            print(f"✅ Password reset successful for: {user.username}")
            success_msg = f"""
            ✅ **รีเซ็ตรหัสผ่านสำเร็จ!**

            บัญชี **{user.username}** สามารถเข้าสู่ระบบด้วยรหัสผ่านใหม่ได้แล้ว

            กรุณาคลิกปุ่มด้านล่างเพื่อกลับไปหน้าเข้าสู่ระบบ
            """
            return (
                gr.update(value=success_msg, visible=True),
                gr.update(visible=True)  # Show back to login button
            )
        else:
            return (
                gr.update(value="❌ **บันทึกรหัสผ่านล้มเหลว**\n\nกรุณาลองใหม่อีกครั้ง", visible=True),
                gr.update(visible=False)
            )
            
    except Exception as e:
        print(f"❌ Password reset error: {str(e)}")
        return (
            gr.update(value=f"❌ **เกิดข้อผิดพลาด**\n\n{str(e)}", visible=True),
            gr.update(visible=False)
        )

def view_cloud_storage_stats(user, session_token=None):
    """View cloud storage statistics (admin feature)"""
    user = _ensure_user(user, session_token)
    if not user:
        return "❌ Please log in to view statistics"
    
    try:
        stats = transcription_manager.get_storage_stats()
        
        stats_text = f"""
📊 **Cloud Storage Statistics**

👥 Users: {stats.get('users_count', 0)}
📋 Metadata Records: {stats.get('metadata_count', 0)}
🔑 Password Reset Tokens: {stats.get('password_resets_count', 0)}
💾 Total Size: {stats.get('total_size_mb', 0):.2f} MB
        """
        
        return stats_text
        
    except Exception as e:
        return f"❌ Error: {str(e)}"
