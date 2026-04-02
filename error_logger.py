"""
Global Error Logger for Real-time Error Display
Captures errors from all modules and displays them in the UI
"""
import threading
import time
from datetime import datetime
from collections import deque
from typing import List, Dict


class ErrorLogger:
    """Global error logger with thread-safe operations"""
    
    def __init__(self, max_errors: int = 50):
        self.errors = deque(maxlen=max_errors)
        self._lock = threading.Lock()
        self.enabled = True
        
    def log_error(self, source: str, error_type: str, message: str, details: str = ""):
        """Log an error with timestamp"""
        if not self.enabled:
            return
            
        with self._lock:
            error_entry = {
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'source': source,
                'type': error_type,
                'message': message,
                'details': details
            }
            self.errors.append(error_entry)
            
            # Also print to console
            print(f"❌ [{source}] {error_type}: {message}")
            if details:
                print(f"   Details: {details}")
    
    def get_recent_errors(self, limit: int = 20) -> List[Dict]:
        """Get recent errors for display"""
        with self._lock:
            return list(self.errors)[-limit:]
    
    def get_error_summary(self) -> str:
        """Get formatted error summary for display"""
        with self._lock:
            if not self.errors:
                return "✅ No errors"
            
            recent = list(self.errors)[-5:]  # Show last 5 errors
            summary_lines = [f"⚠️ {len(self.errors)} errors logged (showing last {len(recent)})\n\n"]
            
            for err in reversed(recent):
                summary_lines.append(
                    f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"[{err['timestamp']}] {err['source']}: {err['type']}\n"
                    f"→ {err['message']}\n"
                )
                if err['details']:
                    # Show FULL details, not truncated
                    summary_lines.append(f"\nℹ️ Details:\n{err['details']}\n")
                summary_lines.append("\n")
            
            return "".join(summary_lines)
    
    def clear_errors(self):
        """Clear all logged errors"""
        with self._lock:
            self.errors.clear()
            print("🧹 Error log cleared")
    
    def get_error_count(self) -> int:
        """Get total error count"""
        with self._lock:
            return len(self.errors)


# Global error logger instance
error_logger = ErrorLogger()


def log_error(source: str, error_type: str, message: str, details: str = ""):
    """Convenience function to log errors"""
    error_logger.log_error(source, error_type, message, details)


def get_error_display() -> str:
    """Get error display text"""
    return error_logger.get_error_summary()
