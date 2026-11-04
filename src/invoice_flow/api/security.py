"""Sessions, CSRF and rate limiting for the browser API.

* A session is a random workspace id signed with ``SESSION_SECRET`` (itsdangerous), stored in an
  HttpOnly, SameSite cookie with an expiry. The server derives the workspace from it. No header,
  query value or body field is ever trusted as an owner.
* Every mutating request must carry a CSRF token tied to the session AND a same-origin
  ``Origin``/``Referer``. A forged cross-site request fails both.
* A demo access code, when configured, is a shared passcode. It is access control for a public
  demo, not user identity.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from urllib.parse import urlparse

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

SESSION_COOKIE = "invoice_flow_session"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


@dataclass(frozen=True)
class SessionData:
    workspace_id: str


class SessionManager:
    def __init__(self, secret: str, ttl_seconds: int) -> None:
        if not secret or len(secret) < 16:
            raise ValueError("SESSION_SECRET must be at least 16 characters.")
        self._secret = secret
        self._ttl = ttl_seconds
        self._serializer = URLSafeTimedSerializer(secret, salt="invoice-flow-session")

    @property
    def ttl(self) -> int:
        return self._ttl

    def new_workspace_id(self) -> str:
        return "ws_" + secrets.token_urlsafe(18)

    def issue(self, workspace_id: str) -> str:
        return self._serializer.dumps({"w": workspace_id})

    def read(self, cookie: str | None) -> SessionData | None:
        """The workspace this cookie proves, or ``None`` for missing, tampered or expired."""
        if not cookie:
            return None
        try:
            data = self._serializer.loads(cookie, max_age=self._ttl)
        except (BadSignature, SignatureExpired):
            return None
        workspace = data.get("w") if isinstance(data, dict) else None
        if not isinstance(workspace, str) or not workspace.startswith("ws_"):
            return None
        return SessionData(workspace)

    def csrf_for(self, workspace_id: str) -> str:
        mac = hmac.new(self._secret.encode(), f"csrf|{workspace_id}".encode(), hashlib.sha256)
        return mac.hexdigest()

    def csrf_ok(self, workspace_id: str, supplied: str | None) -> bool:
        return bool(supplied) and hmac.compare_digest(self.csrf_for(workspace_id), supplied or "")


def same_origin(
    origin: str | None, referer: str | None, host: str | None, allowed: set[str]
) -> bool:
    """A mutating request must come from this site. A missing Origin is accepted only when the
    Referer proves it (some clients omit Origin); both missing is refused."""
    candidate = origin or referer
    if not candidate:
        return False
    parsed = urlparse(candidate)
    if not parsed.netloc:
        return False
    return parsed.netloc == host or parsed.netloc in allowed


class RateLimiter:
    """Sliding window per key. In-memory, one process: enough for a single-instance prototype."""

    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            if len(self._hits) > 5000:  # bound memory
                for stale in [k for k, v in self._hits.items() if not v][:1000]:
                    self._hits.pop(stale, None)
            return True


def passcode_ok(supplied: str | None, expected: str | None) -> bool:
    if expected is None:
        return True
    return bool(supplied) and hmac.compare_digest(supplied or "", expected)
