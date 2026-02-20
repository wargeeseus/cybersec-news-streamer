"""
Authentication module with TOTP (Google Authenticator) support.
"""
import pyotp
import qrcode
import io
import base64
from typing import Optional
from fastapi import Request, HTTPException, Depends
from fastapi.responses import RedirectResponse
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
import logging

from ..db.database import get_setting, set_setting
from ..config import settings

logger = logging.getLogger(__name__)

# Session config
SECRET_KEY = settings.secret_key if hasattr(settings, 'secret_key') else "cybersec-news-streamer-secret-key-change-in-production"
SESSION_COOKIE_NAME = "session"
SESSION_MAX_AGE = 86400 * 7  # 7 days

serializer = URLSafeTimedSerializer(SECRET_KEY)


async def get_or_create_totp_secret() -> str:
    """Get existing TOTP secret or create a new one."""
    secret = await get_setting('totp_secret')
    if not secret:
        secret = pyotp.random_base32()
        await set_setting('totp_secret', secret)
        logger.info("Generated new TOTP secret")
    return secret


async def is_totp_setup() -> bool:
    """Check if TOTP has been set up (user has scanned QR code)."""
    setup_complete = await get_setting('totp_setup_complete')
    return setup_complete == 'true'


async def mark_totp_setup_complete():
    """Mark TOTP setup as complete after first successful login."""
    await set_setting('totp_setup_complete', 'true')


async def generate_totp_qr_code() -> Optional[str]:
    """Generate QR code for Google Authenticator setup.
    Returns None if TOTP is already set up (security: never expose secret again).
    """
    # Security: Never show QR code if already registered
    if await is_totp_setup():
        logger.warning("Attempted to generate QR code after setup complete - blocked")
        return None

    secret = await get_or_create_totp_secret()

    # Create TOTP URI
    totp = pyotp.TOTP(secret)
    provisioning_uri = totp.provisioning_uri(
        name="admin",
        issuer_name="CyberSec News Streamer"
    )

    # Generate QR code
    qr = qrcode.QRCode(version=1, box_size=6, border=2)
    qr.add_data(provisioning_uri)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # Convert to base64
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    img_base64 = base64.b64encode(buffer.getvalue()).decode()

    return f"data:image/png;base64,{img_base64}"


async def verify_totp(code: str) -> bool:
    """Verify a TOTP code."""
    secret = await get_or_create_totp_secret()
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)  # Allow 1 window tolerance


def create_session_token(user_id: str = "admin") -> str:
    """Create a signed session token."""
    return serializer.dumps({"user_id": user_id})


def verify_session_token(token: str) -> Optional[dict]:
    """Verify and decode a session token."""
    try:
        data = serializer.loads(token, max_age=SESSION_MAX_AGE)
        return data
    except (BadSignature, SignatureExpired):
        return None


async def get_current_user(request: Request) -> Optional[str]:
    """Get the current authenticated user from session."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None

    data = verify_session_token(token)
    if data:
        return data.get("user_id")
    return None


async def require_auth(request: Request) -> str:
    """Dependency that requires authentication."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def auth_required(request: Request):
    """Check if user is authenticated, redirect to login if not."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return False

    data = verify_session_token(token)
    return data is not None
