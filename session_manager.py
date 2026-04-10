"""
HMAC-signed Session Management

Sessions survive container restarts by using self-validating signed tokens.
The signing key comes from PASSWORD_SALT (Key Vault).
Inactivity timeout (60min) is enforced client-side via localStorage.
Absolute expiry (6h) is encoded in the token and verified server-side.
"""
import hmac
import hashlib
import json
import base64
import time
import threading
from typing import Optional, Dict, Set
from backend import User, transcription_manager
import config


class SessionManager:
    """Session Manager using HMAC-signed tokens that survive container restarts.

    Tokens are self-validating: base64(payload).base64(hmac_sha256).
    No in-memory session state is required for validation.
    """

    def __init__(self, session_timeout: int = 21600, inactivity_timeout: int = 3600):
        self.session_timeout = session_timeout
        self.inactivity_timeout = inactivity_timeout
        self._signing_key = (config.PASSWORD_SALT or "fallback-session-key-change-me").encode('utf-8')
        self._invalidated: Set[str] = set()  # blacklist for logout (in-memory only)
        self._user_cache: Dict[str, User] = {}  # uid → User, avoids repeated blob calls
        self._lock = threading.Lock()

        print("🔐 HMAC Session Manager initialized:")
        print(f"   - Session timeout: {session_timeout}s ({session_timeout // 3600}h)")
        print(f"   - Inactivity timeout: {inactivity_timeout}s ({inactivity_timeout // 60} minutes) [client-side]")
        print("   - Survives container restart: ✅")

    # ── signing helpers ───────────────────────────────────────────────
    def _sign(self, payload_b64: str) -> str:
        sig = hmac.new(self._signing_key, payload_b64.encode('utf-8'), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(sig).rstrip(b'=').decode('ascii')

    def _verify(self, payload_b64: str, signature: str) -> bool:
        return hmac.compare_digest(self._sign(payload_b64), signature)

    @staticmethod
    def _b64_encode(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')

    @staticmethod
    def _b64_decode(s: str) -> bytes:
        padding = 4 - len(s) % 4
        if padding != 4:
            s += '=' * padding
        return base64.urlsafe_b64decode(s)

    # ── public API ────────────────────────────────────────────────────
    def create_session(self, user: User, initial_tab: str = "transcription") -> str:  # noqa: S1172
        """Create HMAC-signed session token. Returns token for browser localStorage."""
        current_time = time.time()
        payload = {
            'uid': user.user_id,
            'usr': user.username,
            'iat': current_time,
            'exp': current_time + self.session_timeout,
        }
        payload_b64 = self._b64_encode(json.dumps(payload, separators=(',', ':')).encode('utf-8'))
        token = f"{payload_b64}.{self._sign(payload_b64)}"

        # Cache user so validate_session won't need blob storage
        with self._lock:
            self._user_cache[user.user_id] = user

        print(f"✅ Session ticket created: {token[:12]}... for {user.username}")
        print("   - Valid for 60 minutes of inactivity")
        return token

    def verify_token(self, token: str) -> Optional[Dict]:
        """Lightweight token check: HMAC signature + expiry only. NO blob storage call.

        Returns the decoded payload dict if valid, None otherwise.
        Use this for frequent checks (timers, guards) where you don't need
        a full User object from blob storage.
        """
        if not token or not token.strip():
            return None
        token = token.strip()

        with self._lock:
            if token in self._invalidated:
                return None

        parts = token.split('.')
        if len(parts) != 2:
            return None
        payload_b64, signature = parts

        if not self._verify(payload_b64, signature):
            return None

        try:
            payload = json.loads(self._b64_decode(payload_b64).decode('utf-8'))
        except Exception:
            return None

        if time.time() > payload.get('exp', 0):
            return None

        return payload

    def validate_session(self, token: str) -> Optional[User]:
        """Validate HMAC-signed token and return User if valid.

        Uses an in-memory cache so repeated calls don't hit blob storage.
        """
        payload = self.verify_token(token)
        if payload is None:
            return None

        user_id = payload.get('uid')
        if not user_id:
            return None

        # Check cache first
        with self._lock:
            cached = self._user_cache.get(user_id)
            if cached:
                return cached

        # Cache miss — fetch from blob storage once
        try:
            user = transcription_manager.blob_storage.get_user(user_id)
            if not user:
                print(f"❌ User not found for token: {token[:12]}...")
                return None
            with self._lock:
                self._user_cache[user_id] = user
            return user
        except Exception as e:
            print(f"❌ Error loading user from token: {e}")
            return None

    def refresh_session(self, token: str, current_tab: str = None) -> bool:  # noqa: S1172
        """Check if token is still valid. Inactivity is handled client-side."""
        return self.validate_session(token) is not None

    def invalidate_session(self, token: str) -> bool:
        """Invalidate session token on logout."""
        if not token:
            return False
        with self._lock:
            self._invalidated.add(token.strip())
        print(f"👋 Session token invalidated: {token[:12]}...")
        return True

    def get_session_info(self, token: str) -> Optional[Dict]:
        """Get session info decoded from token payload."""
        if not token or not token.strip():
            return None
        parts = token.strip().split('.')
        if len(parts) != 2:
            return None
        try:
            payload = json.loads(self._b64_decode(parts[0]).decode('utf-8'))
        except Exception:
            return None
        current_time = time.time()
        return {
            'ticket': token[:12] + '...',
            'user': payload.get('usr', '?'),
            'absolute_time_remaining': f"{int(max(0, payload['exp'] - current_time))}s",
            'is_active': current_time < payload['exp'],
        }

    def get_active_sessions_count(self) -> int:
        return -1  # Not tracked with stateless tokens

    def shutdown(self):
        print("🛑 Session Manager shutdown")


# Global session manager instance
session_manager = SessionManager(
    session_timeout=21600,   # 6 hours absolute
    inactivity_timeout=3600  # 60 minutes — enforced client-side via localStorage
)