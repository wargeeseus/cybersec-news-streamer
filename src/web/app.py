from fastapi import FastAPI, Request, Form
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, RedirectResponse
from pathlib import Path
from contextlib import asynccontextmanager
import logging
import asyncio

from ..db.database import init_db, get_setting, get_all_channels, get_channel, create_channel
from ..db.models import ChannelCreate
from ..config import settings
from .routes import router
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

# Channel stream managers (one per channel)
channel_stream_managers = {}


def get_stream_manager(channel_id: int):
    """Get or create a stream manager for a channel."""
    from ..stream.youtube import YouTubeStreamer
    if channel_id not in channel_stream_managers:
        channel_stream_managers[channel_id] = YouTubeStreamer()
    return channel_stream_managers[channel_id]


async def auto_start_streams():
    """Auto-start streams for all active channels with stream keys."""
    await asyncio.sleep(5)  # Wait for app to fully start

    channels = await get_all_channels()
    for channel in channels:
        if channel.stream_key and channel.is_active:
            manager = get_stream_manager(channel.id)
            if not manager.is_running:
                logger.info(f"Auto-starting stream for channel: {channel.name}")
                manager.update_config(
                    stream_key=channel.stream_key,
                    rtmp_url=channel.rtmp_url,
                    display_seconds=channel.display_seconds
                )
                asyncio.create_task(manager.start())


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    logger.info("Initializing database...")
    await init_db()
    logger.info("Database initialized")

    # Auto-start streams
    asyncio.create_task(auto_start_streams())

    yield

    # Shutdown - stop all streams
    logger.info("Shutting down...")
    for channel_id, manager in channel_stream_managers.items():
        if manager.is_running:
            await manager.stop()


app = FastAPI(
    title="CyberSec News Streamer",
    description="Multi-channel news streaming portal",
    version="2.0.0",
    lifespan=lifespan,
)

# Mount static files
static_path = settings.assets_path / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

# Templates
templates_path = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_path))

# Include API routes
app.include_router(router)


# --- Authentication Routes ---

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = None):
    """Login page with QR code for Google Authenticator."""
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
    if await verify_totp(code):
        if not await is_totp_setup():
            await mark_totp_setup_complete()
            logger.info("TOTP setup completed")

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
        logger.warning("Failed login attempt - invalid TOTP code")
        return RedirectResponse(url="/login?error=Invalid+code.+Please+try+again.", status_code=302)


@app.get("/logout")
async def logout():
    """Logout and clear session."""
    response = RedirectResponse(url="/login", status_code=302)
    response.delete_cookie(SESSION_COOKIE_NAME)
    return response


# --- Channel Routes ---

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    """Redirect to first channel."""
    if not auth_required(request):
        return RedirectResponse(url="/login", status_code=302)

    channels = await get_all_channels()
    if channels:
        return RedirectResponse(url=f"/channel/{channels[0].id}", status_code=302)

    # No channels, show empty dashboard
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "channels": [],
            "current_channel": None,
            "queue_items": [],
            "counts": {"approved": 0, "streamed": 0},
            "stream_status": {"is_running": False},
            "config": {},
        }
    )


@app.get("/channel/{channel_id}", response_class=HTMLResponse)
async def channel_dashboard(request: Request, channel_id: int):
    """Dashboard for a specific channel."""
    if not auth_required(request):
        return RedirectResponse(url="/login", status_code=302)

    from ..db.database import get_news_items_by_status, get_counts_by_status
    from ..db.models import NewsStatus

    channels = await get_all_channels()
    current_channel = await get_channel(channel_id)

    if not current_channel:
        return RedirectResponse(url="/", status_code=302)

    # Get channel-specific data
    counts = await get_counts_by_status(channel_id=channel_id)
    queue_items = await get_news_items_by_status(NewsStatus.APPROVED, limit=25, channel_id=channel_id)

    # Get stream manager for this channel
    manager = get_stream_manager(channel_id)
    stream_status = manager.get_status()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "channels": channels,
            "current_channel": current_channel,
            "queue_items": queue_items,
            "counts": counts,
            "stream_status": stream_status,
            "config": {
                "stream_key": current_channel.stream_key,
                "rtmp_url": current_channel.rtmp_url,
                "display_seconds": current_channel.display_seconds,
            },
        }
    )


@app.get("/health")
async def health_check():
    """Health check endpoint (no auth required)."""
    return {"status": "healthy"}


# --- Middleware ---

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Protect API routes with authentication."""
    path = request.url.path
    public_paths = ["/login", "/health", "/static"]
    is_public = any(path.startswith(p) for p in public_paths)

    if not is_public and path.startswith("/api"):
        if not auth_required(request):
            return RedirectResponse(url="/login", status_code=302)

    response = await call_next(request)
    return response


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.portal_port)
