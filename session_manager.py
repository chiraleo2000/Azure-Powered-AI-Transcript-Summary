"""
Enhanced Session Management with OAuth2-style Tickets
Maintains user sessions across page refreshes with 60-minute inactivity timeout
Sessions survive page refresh by storing ticket tokens in browser localStorage
"""
import uuid
import time
import threading
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional, Dict
from dataclasses import dataclass, field
from backend import User, transcription_manager

@dataclass
class SessionTicket:
    """OAuth2-style session ticket that survives page refresh"""
    session_id: str
    ticket_token: str  # Secure token stored in browser
    user_id: str
    user: User
    created_at: float
    last_activity: float
    expires_at: float
    refresh_count: int = 0
    last_refresh_tab: str = "transcription"  # Track which tab user was on

class SessionManager:
    """OAuth2-style Session Manager with 60-minute inactivity tickets
    
    Features:
    - Sessions survive page refresh via secure ticket tokens
    - 60-minute inactivity timeout (resets on any activity)
    - 6-hour absolute session lifetime
    - Tab state persistence (remembers which tab user was on)
    """
    
    def __init__(self, session_timeout: int = 21600, inactivity_timeout: int = 3600):
        """
        Args:
            session_timeout: Total session lifetime (default: 6 hours = 21600s)
            inactivity_timeout: Inactivity timeout (default: 60 minutes = 3600s)
        """
        self.sessions: Dict[str, SessionTicket] = {}  # session_id -> ticket
        self.tokens: Dict[str, str] = {}  # ticket_token -> session_id (for lookup)
        self.session_timeout = session_timeout  # 6 hours total session time
        self.inactivity_timeout = inactivity_timeout  # 60 minutes inactivity
        self._lock = threading.Lock()
        
        # Start cleanup worker
        self.running = True
        self.cleanup_thread = threading.Thread(target=self._cleanup_worker, daemon=True)
        self.cleanup_thread.start()
        
        print("🔐 OAuth2-style Session Manager initialized:")
        print(f"   - Session timeout: {session_timeout}s ({session_timeout//3600}h)")
        print(f"   - Inactivity timeout: {inactivity_timeout}s ({inactivity_timeout//60} minutes)")
        print("   - Survives page refresh: ✅")
    
    def _generate_ticket_token(self) -> str:
        """Generate a secure ticket token for OAuth2-style authentication"""
        return secrets.token_urlsafe(32)
    
    def create_session(self, user: User, initial_tab: str = "transcription") -> str:
        """Create a new session with OAuth2-style ticket token for authenticated user
        
        Returns the ticket_token (to be stored in browser localStorage)
        """
        session_id = str(uuid.uuid4())
        ticket_token = self._generate_ticket_token()
        current_time = time.time()
        
        ticket = SessionTicket(
            session_id=session_id,
            ticket_token=ticket_token,
            user_id=user.user_id,
            user=user,
            created_at=current_time,
            last_activity=current_time,
            expires_at=current_time + self.session_timeout,
            refresh_count=0,
            last_refresh_tab=initial_tab
        )
        
        with self._lock:
            self.sessions[session_id] = ticket
            self.tokens[ticket_token] = session_id
        
        print(f"✅ Session ticket created: {ticket_token[:12]}... for {user.username}")
        print("   - Valid for 60 minutes of inactivity")
        return ticket_token  # Return ticket token (not session_id) for browser storage
    
    def validate_session(self, ticket_token: str) -> Optional[User]:
        """Validate session ticket and return user if valid
        
        This validates the ticket_token stored in browser localStorage
        """
        if not ticket_token or ticket_token.strip() == "":
            return None
        
        with self._lock:
            # Look up session by ticket token
            session_id = self.tokens.get(ticket_token)
            if not session_id:
                return None
            
            ticket = self.sessions.get(session_id)
            if not ticket:
                # Clean up orphaned token
                del self.tokens[ticket_token]
                return None
            
            current_time = time.time()
            
            # Check if session expired (total timeout - 6 hours)
            if current_time > ticket.expires_at:
                print(f"⏰ Session ticket expired (absolute): {ticket_token[:12]}... for {ticket.user.username}")
                del self.sessions[session_id]
                del self.tokens[ticket_token]
                return None
            
            # Check if session inactive (60-minute inactivity timeout)
            if current_time - ticket.last_activity > self.inactivity_timeout:
                print(f"💤 Session ticket expired (60min inactive): {ticket_token[:12]}... for {ticket.user.username}")
                del self.sessions[session_id]
                del self.tokens[ticket_token]
                return None
            
            # Session is valid - update activity timestamp
            ticket.last_activity = current_time
            ticket.refresh_count += 1
            
            return ticket.user
    
    def refresh_session(self, ticket_token: str, current_tab: str = None) -> bool:
        """Refresh session activity timestamp and optionally update tab state
        
        Call this on any user activity to reset the 60-minute timer
        """
        if not ticket_token:
            return False
        
        with self._lock:
            session_id = self.tokens.get(ticket_token)
            if not session_id:
                return False
            
            ticket = self.sessions.get(session_id)
            if not ticket:
                return False
            
            current_time = time.time()
            
            # Check if expired
            if current_time > ticket.expires_at:
                del self.sessions[session_id]
                del self.tokens[ticket_token]
                return False
            
            # Update activity and optionally tab state
            ticket.last_activity = current_time
            if current_tab:
                ticket.last_refresh_tab = current_tab
            
            return True
    
    def get_last_tab(self, ticket_token: str) -> str:
        """Get the last tab the user was on (for restoration after refresh)"""
        with self._lock:
            session_id = self.tokens.get(ticket_token, "")
            if not session_id:
                return "transcription"
            
            ticket = self.sessions.get(session_id)
            return ticket.last_refresh_tab if ticket else "transcription"
    
    def set_last_tab(self, ticket_token: str, tab_name: str) -> bool:
        """Update the last tab the user was on"""
        with self._lock:
            session_id = self.tokens.get(ticket_token, "")
            if not session_id:
                return False
            
            ticket = self.sessions.get(session_id)
            if ticket:
                ticket.last_refresh_tab = tab_name
                return True
            return False
    
    def invalidate_session(self, ticket_token: str) -> bool:
        """Invalidate/logout a session by ticket token"""
        if not ticket_token:
            return False
        
        with self._lock:
            session_id = self.tokens.get(ticket_token)
            if session_id and session_id in self.sessions:
                ticket = self.sessions[session_id]
                print(f"👋 Session ticket invalidated: {ticket_token[:12]}... for {ticket.user.username}")
                del self.sessions[session_id]
                del self.tokens[ticket_token]
                return True
        
        return False
    
    def get_session_info(self, ticket_token: str) -> Optional[Dict]:
        """Get session information including time remaining"""
        with self._lock:
            session_id = self.tokens.get(ticket_token, "")
            if not session_id:
                return None
            
            ticket = self.sessions.get(session_id)
            if not ticket:
                return None
            
            current_time = time.time()
            time_remaining = ticket.expires_at - current_time
            inactive_time = current_time - ticket.last_activity
            inactivity_remaining = self.inactivity_timeout - inactive_time
            
            return {
                'ticket': ticket_token[:12] + '...',
                'user': ticket.user.username,
                'created': datetime.fromtimestamp(ticket.created_at).isoformat(),
                'last_activity': datetime.fromtimestamp(ticket.last_activity).isoformat(),
                'absolute_time_remaining': f"{int(time_remaining)}s",
                'inactivity_remaining': f"{int(max(0, inactivity_remaining))}s ({int(max(0, inactivity_remaining)//60)}min)",
                'refresh_count': ticket.refresh_count,
                'last_tab': ticket.last_refresh_tab,
                'is_active': inactive_time < self.inactivity_timeout
            }
    
    def get_active_sessions_count(self) -> int:
        """Get count of active sessions"""
        with self._lock:
            return len(self.sessions)
    
    def _cleanup_worker(self):
        """Background worker to clean up expired sessions"""
        while self.running:
            try:
                current_time = time.time()
                expired_tickets = []
                
                with self._lock:
                    for session_id, ticket in list(self.sessions.items()):
                        reason = None
                        
                        # Check total expiration (6 hours)
                        if current_time > ticket.expires_at:
                            reason = 'absolute_timeout'
                        # Check inactivity (60 minutes)
                        elif current_time - ticket.last_activity > self.inactivity_timeout:
                            reason = 'inactive_60min'
                        
                        if reason:
                            expired_tickets.append((session_id, ticket.ticket_token, ticket.user.username, reason))
                            del self.sessions[session_id]
                            if ticket.ticket_token in self.tokens:
                                del self.tokens[ticket.ticket_token]
                
                if expired_tickets:
                    for sid, token, username, reason in expired_tickets:
                        print(f"🧹 Cleaned up {reason} ticket: {token[:12]}... ({username})")
                
                time.sleep(30)  # Check every 30 seconds
                
            except Exception as e:
                print(f"❌ Session cleanup error: {e}")
                time.sleep(60)
    
    def shutdown(self):
        """Shutdown session manager"""
        self.running = False
        with self._lock:
            session_count = len(self.sessions)
            self.sessions.clear()
            self.tokens.clear()
        print(f"🛑 Session Manager shutdown ({session_count} sessions cleared)")

# Global session manager instance with 60-minute inactivity timeout
session_manager = SessionManager(
    session_timeout=21600,  # 6 hours total
    inactivity_timeout=3600  # 60 minutes inactivity - survives page refresh!
)