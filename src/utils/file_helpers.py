"""
File Helper Utilities
"""
import os
from typing import Optional


def normalize_filepath(fp) -> Optional[str]:
    """
    Normalize file path from various Gradio input formats.
    Returns a usable path or None.
    """
    if isinstance(fp, (list, tuple)):
        fp = fp[0] if fp else None
    
    if isinstance(fp, dict):
        fp = fp.get("path") or fp.get("name") or fp.get("file")
    
    if isinstance(fp, str) and fp.strip():
        return fp
    return None


def get_file_extension(filepath: str) -> str:
    """Get lowercase file extension without dot"""
    if not filepath:
        return ""
    ext = os.path.splitext(filepath)[1] or ""
    return ext.lower().lstrip('.')


def get_file_type(filepath: str) -> str:
    """Determine file type category"""
    ext = get_file_extension(filepath)
    
    AUDIO_EXTS = {'mp3', 'wav', 'ogg', 'opus', 'm4a', 'aac', 'flac', 'wma', 'amr', 'speex'}
    VIDEO_EXTS = {'mp4', 'webm', 'mov', 'avi', 'mkv', 'flv', 'wmv', '3gp'}
    IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'bmp', 'gif', 'tiff', 'webp'}
    DOCUMENT_EXTS = {'pdf', 'docx', 'doc', 'pptx', 'ppt', 'xlsx', 'xls', 'txt', 'csv', 'json'}
    
    if ext in AUDIO_EXTS:
        return 'audio'
    elif ext in VIDEO_EXTS:
        return 'video'
    elif ext in IMAGE_EXTS:
        return 'image'
    elif ext in DOCUMENT_EXTS:
        return 'document'
    return 'unknown'


def format_file_size(size_bytes: int) -> str:
    """Format file size in human readable format"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def is_file_too_large(filepath: str, max_size_mb: int = 200) -> bool:
    """Check if file exceeds size limit"""
    try:
        size = os.path.getsize(filepath)
        return size > max_size_mb * 1024 * 1024
    except:
        return False
