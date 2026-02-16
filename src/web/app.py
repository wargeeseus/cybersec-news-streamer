from fastapi import FastAPI, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from pathlib import Path
from contextlib import asynccontextmanager
import logging
import asyncio

from ..db.database import init_db, get_setting
from ..config import settings
from .routes import router
from ..stream.youtube import stream_manager
from .auth import (
    auth_required,
    get_or_create_totp_secret,
    generate_totp_qr_code,
    verify_totp,
    is_totp_setup,
    mark_totp_setup_complete,
    create_session_token,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def auto_start_stream():
    """Auto-start the stream if configured."""
    await asyncio.sleep(5)  # Wait for app to fully start

    stream_key = await get_setting('youtube_stream_key', '')
    if stream_key and not stream_manager.is_running:
        logger.info("Auto-starting stream...")
        asyncio.create_task(stream_manager.start())
    else:
        if not stream_key:
            logger.warning("No stream key configured - stream will not auto-start")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    # Startup
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized")

    # Auto-start stream
    asyncio.create_task(auto_start_stream())

    yield
    # Shutdown
    logger.info("Shutting down...")
    if stream_manager.is_running:
        await stream_manager.stop()


app = FastAPI(
    title="CyberSec News Streamer",
    description="Web portal for managing cybersecurity news stream",
    version="1.0.0",
    lifespan=lifespan,
)

# Mount static files
static_path = settings.assets_path / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# Templates
templates_path = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_path))

# Include API routes (will be protected by middleware)
app.include_router(router)


# --- Authentication Routes ---

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None):
    """Login page with QR code for Google Authenticator."""
    # Check if already authenticated
    if auth_required(request):
        return RedirectResponse(url="/", status_code=302)

    is_setup = await is_totp_setup()
    qr_code = None
    secret_key = None

    if not is_setup:
        qr_code = await generate_totp_qr_code()
        secret_key = await get_or_create_totp_secret()

    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "is_setup": is_setup,
            "qr_code": qr_code,
            "secret_key": secret_key,
            "error": error,
        }
    )


@app.post("/login")
async def login_submit(request: Request, code: str = Form(...)):
    """Handle login form submission."""
    # Verify TOTP code
    if await verify_totp(code):
        # Mark setup complete on first successful login
        if not await is_totp_setup():
            await mark_totp_setup_complete()
            logger.info("TOTP setup completed")

        # Create session
        token = create_session_token()
        response = RedirectResponse(url="/", status_code=302)
        response.set_cookie(
            key=SESSION_COOKIE_NAME,
            value=token,
            max_age=SESSION_MAX_AGE,
            httponly=True,
            samesite="lax",
        )
        logger.info("User logged in successfully")
        return response
    else:
        # Invalid code
        logger.warning("Failed login attempt - invalid TOTP code")
        return RedirectResponse(url="/login?error=Invalid+code.+Please+try+again.", status_code=302)


@app.get("/logout")
async def logout():
    """Logout and clear session."""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


# --- Protected Routes ---

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Main dashboard view - shows stream queue (auto-approved items)."""
    # Check authentication
    if not auth_required(request):
        return RedirectResponse(url="/login", status_code=302)

    from ..db.database import get_news_items_by_status, get_counts_by_status, get_stream_config
    from ..db.models import NewsStatus

    # Get counts
    counts = await get_counts_by_status()

    # Get approved items (the stream queue)
    queue_items = await get_news_items_by_status(NewsStatus.APPROVED, limit=100)

    # Stream status and config
    stream_status = stream_manager.get_status()
    config = await get_stream_config()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "counts": counts,
            "queue_items": queue_items,
            "stream_status": stream_status,
            "config": config,
        }
    )


@app.get("/health")
async def health_check():
    """Health check endpoint (no auth required)."""
    return {"status": "healthy"}


# --- Middleware for API route protection ---

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Protect API routes with authentication."""
    path = request.url.path

    # Public paths that don't require auth
    public_paths = ["/login", "/health", "/static"]

    # Check if path is public
    is_public = any(path.startswith(p) for p in public_paths)

    if not is_public and path.startswith("/api"):
        # Check authentication for API routes
        if not auth_required(request):
            return RedirectResponse(url="/login", status_code=302)

    response = await call_next(request)
    return response


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.portal_port)
