from fastapi import APIRouter, HTTPException, Request, BackgroundTasks, Form
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from typing import Optional
import asyncio

from ..db.database import (
    get_news_item,
    get_news_items_by_status,
    update_news_item,
    get_counts_by_status,
    get_stream_config,
    set_setting,
    get_all_channels,
    get_channel,
    create_channel,
    update_channel,
    create_news_item,
)
from ..db.models import NewsStatus, NewsItemUpdate, ChannelCreate, ChannelUpdate, NewsItemCreate
from ..video.frame_generator import generate_frame

router = APIRouter()

# Templates
templates_path = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(templates_path))


def get_stream_manager(channel_id: int):
    """Get stream manager for a channel."""
    from .app import get_stream_manager as app_get_stream_manager
    return app_get_stream_manager(channel_id)


# --- Channel API Endpoints ---

@router.post("/api/channel/create", response_class=HTMLResponse)
async def create_new_channel(
    request: Request,
    name: str = Form(...),
    news_topic: str = Form(...),
    stream_key: str = Form(""),
    rtmp_url: str = Form("rtmp://a.rtmp.youtube.com/live2"),
    display_seconds: int = Form(30),
):
    """Create a new channel."""
    channel_data = ChannelCreate(
        name=name,
        news_topic=news_topic,
        stream_key=stream_key,
        rtmp_url=rtmp_url,
        display_seconds=display_seconds,
    )
    await create_channel(channel_data)

    # Return updated channel list
    channels = await get_all_channels()
    return templates.TemplateResponse(
        "components/channel_list.html",
        {"request": request, "channels": channels}
    )


@router.get("/api/channel/{channel_id}/stream/status", response_class=HTMLResponse)
async def channel_stream_status(request: Request, channel_id: int):
    """Get stream status for a channel."""
    channel = await get_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    manager = get_stream_manager(channel_id)
    status = manager.get_status()
    counts = await get_counts_by_status(channel_id=channel_id)

    return templates.TemplateResponse(
        "components/stream_status.html",
        {
            "request": request,
            "stream_status": status,
            "counts": counts,
            "config": {
                "stream_key": channel.stream_key,
                "rtmp_url": channel.rtmp_url,
                "display_seconds": channel.display_seconds,
            },
            "channel_id": channel_id,
        }
    )


@router.post("/api/channel/{channel_id}/stream/start", response_class=HTMLResponse)
async def start_channel_stream(request: Request, channel_id: int, background_tasks: BackgroundTasks):
    """Start streaming for a channel."""
    channel = await get_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    manager = get_stream_manager(channel_id)

    if manager.is_running:
        return await channel_stream_status(request, channel_id)

    # Configure and start
    manager.update_config(
        stream_key=channel.stream_key,
        rtmp_url=channel.rtmp_url,
        display_seconds=channel.display_seconds,
    )
    manager._channel_id = channel_id  # Store channel ID for fetching news
    background_tasks.add_task(manager.start)

    await asyncio.sleep(0.5)
    return await channel_stream_status(request, channel_id)


@router.post("/api/channel/{channel_id}/stream/stop", response_class=HTMLResponse)
async def stop_channel_stream(request: Request, channel_id: int):
    """Stop streaming for a channel."""
    manager = get_stream_manager(channel_id)
    await manager.stop()
    return await channel_stream_status(request, channel_id)


@router.post("/api/channel/{channel_id}/fetch", response_class=HTMLResponse)
async def fetch_channel_news(request: Request, channel_id: int, background_tasks: BackgroundTasks):
    """Fetch news for a specific channel based on its topic."""
    channel = await get_channel(channel_id)
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    from ..news.fetcher import fetch_news_for_topic
    from ..ai.summarizer import summarize_news

    async def do_fetch():
        items = await fetch_news_for_topic(channel.news_topic)
        for item in items:
            result = await summarize_news(item["title"], item["description"])
            if result:
                news_item = NewsItemCreate(
                    channel_id=channel_id,
                    title=result["headline"],
                    original_title=item["title"],
                    summary=result["summary"],
                    source_name=item["source_name"],
                    source_url=item["url"],
                )
                created = await create_news_item(news_item)
                if created:
                    try:
                        frame_path = generate_frame(created)
                        await update_news_item(created.id, NewsItemUpdate(frame_path=str(frame_path)))
                    except Exception:
                        pass

    background_tasks.add_task(do_fetch)

    # Return current queue
    items = await get_news_items_by_status(NewsStatus.APPROVED, limit=25, channel_id=channel_id)
    return templates.TemplateResponse(
        "components/news_queue.html",
        {
            "request": request,
            "items": items,
            "message": f"Fetching news about '{channel.news_topic}'...",
        }
    )


@router.post("/api/channel/{channel_id}/config", response_class=HTMLResponse)
async def update_channel_config(
    request: Request,
    channel_id: int,
    stream_key: str = Form(""),
    rtmp_url: str = Form("rtmp://a.rtmp.youtube.com/live2"),
    display_seconds: int = Form(30),
):
    """Update channel stream configuration."""
    update_data = ChannelUpdate(
        stream_key=stream_key,
        rtmp_url=rtmp_url,
        display_seconds=display_seconds,
    )
    await update_channel(channel_id, update_data)

    # Update stream manager config
    manager = get_stream_manager(channel_id)
    manager.update_config(
        stream_key=stream_key,
        rtmp_url=rtmp_url,
        display_seconds=display_seconds,
    )

    return await channel_stream_status(request, channel_id)


# --- News Item Endpoints ---

@router.get("/api/news", response_class=HTMLResponse)
async def list_news(
    request: Request,
    status: Optional[str] = "pending"
):
    """List news items by status (returns HTML fragment for HTMX)."""
    try:
        news_status = NewsStatus(status)
    except ValueError:
        news_status = NewsStatus.PENDING

    items = await get_news_items_by_status(news_status)

    return templates.TemplateResponse(
        "components/news_list.html",
        {
            "request": request,
            "items": items,
            "status": status,
        }
    )


@router.post("/api/news/{item_id}/approve", response_class=HTMLResponse)
async def approve_news(request: Request, item_id: int, background_tasks: BackgroundTasks):
    """Approve a news item."""
    item = await get_news_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="News item not found")

    # Update status
    updated = await update_news_item(
        item_id,
        NewsItemUpdate(status=NewsStatus.APPROVED)
    )

    # Generate frame in background
    background_tasks.add_task(_generate_frame_task, item_id)

    # Return updated card
    return templates.TemplateResponse(
        "components/news_card.html",
        {
            "request": request,
            "item": updated,
            "show_actions": True,
        }
    )


@router.post("/api/news/{item_id}/reject", response_class=HTMLResponse)
async def reject_news(request: Request, item_id: int):
    """Reject a news item."""
    item = await get_news_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="News item not found")

    updated = await update_news_item(
        item_id,
        NewsItemUpdate(status=NewsStatus.REJECTED)
    )

    return templates.TemplateResponse(
        "components/news_card.html",
        {
            "request": request,
            "item": updated,
            "show_actions": False,
        }
    )


@router.put("/api/news/{item_id}", response_class=HTMLResponse)
async def update_news(
    request: Request,
    item_id: int,
):
    """Update a news item's title/summary."""
    item = await get_news_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="News item not found")

    form_data = await request.form()
    title = form_data.get("title")
    summary = form_data.get("summary")

    update = NewsItemUpdate()
    if title:
        update.title = str(title)
    if summary:
        update.summary = str(summary)

    updated = await update_news_item(item_id, update)

    return templates.TemplateResponse(
        "components/news_card.html",
        {
            "request": request,
            "item": updated,
            "show_actions": True,
        }
    )


@router.get("/api/news/{item_id}/edit", response_class=HTMLResponse)
async def edit_news_form(request: Request, item_id: int):
    """Get edit form for a news item."""
    item = await get_news_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="News item not found")

    return templates.TemplateResponse(
        "components/news_edit.html",
        {
            "request": request,
            "item": item,
        }
    )


@router.get("/api/news/{item_id}/preview")
async def preview_frame(item_id: int):
    """Preview the generated frame for a news item."""
    item = await get_news_item(item_id)
    if not item:
        raise HTTPException(status_code=404, detail="News item not found")

    # Generate frame if needed
    if not item.frame_path or not Path(item.frame_path).exists():
        frame_path = generate_frame(item)
        await update_news_item(item_id, NewsItemUpdate(frame_path=str(frame_path)))
    else:
        frame_path = Path(item.frame_path)

    return FileResponse(frame_path, media_type="image/png")


@router.get("/api/stream/current-frame")
async def get_current_frame():
    """Get the current streaming frame."""
    # Get current item from stream manager
    current_id = stream_manager.current_item_id

    if current_id:
        item = await get_news_item(current_id)
        if item and item.frame_path and Path(item.frame_path).exists():
            return FileResponse(item.frame_path, media_type="image/png")

    # Fallback to first approved item
    items = await get_news_items_by_status(NewsStatus.APPROVED, limit=1)
    if items:
        item = items[0]
        if not item.frame_path or not Path(item.frame_path).exists():
            frame_path = generate_frame(item)
            await update_news_item(item.id, NewsItemUpdate(frame_path=str(frame_path)))
        else:
            frame_path = Path(item.frame_path)
        return FileResponse(frame_path, media_type="image/png")

    # No frames available
    raise HTTPException(status_code=404, detail="No frames available")


# --- Stream Control Endpoints ---

@router.get("/api/stream/status", response_class=HTMLResponse)
async def stream_status(request: Request):
    """Get current stream status (returns HTML fragment)."""
    status = stream_manager.get_status()
    counts = await get_counts_by_status()
    config = await get_stream_config()

    return templates.TemplateResponse(
        "components/stream_status.html",
        {
            "request": request,
            "stream_status": status,
            "counts": counts,
            "config": config,
        }
    )


@router.post("/api/stream/start", response_class=HTMLResponse)
async def start_stream(request: Request, background_tasks: BackgroundTasks):
    """Start the YouTube stream."""
    if stream_manager.is_running:
        return templates.TemplateResponse(
            "components/stream_status.html",
            {
                "request": request,
                "stream_status": stream_manager.get_status(),
                "message": "Stream already running",
            }
        )

    # Start stream in background
    background_tasks.add_task(stream_manager.start)

    # Wait briefly for state change
    await asyncio.sleep(0.5)

    return templates.TemplateResponse(
        "components/stream_status.html",
        {
            "request": request,
            "stream_status": stream_manager.get_status(),
        }
    )


@router.post("/api/stream/stop", response_class=HTMLResponse)
async def stop_stream(request: Request):
    """Stop the YouTube stream."""
    await stream_manager.stop()

    return templates.TemplateResponse(
        "components/stream_status.html",
        {
            "request": request,
            "stream_status": stream_manager.get_status(),
        }
    )


# --- Configuration Endpoints ---

@router.post("/api/config/stream", response_class=HTMLResponse)
async def update_stream_config(request: Request):
    """Update stream configuration."""
    form_data = await request.form()

    youtube_stream_key = form_data.get("youtube_stream_key", "")
    news_display_seconds = form_data.get("news_display_seconds", "30")
    rtmp_url = form_data.get("rtmp_url", "rtmp://a.rtmp.youtube.com/live2")

    # Save settings
    await set_setting("youtube_stream_key", str(youtube_stream_key))
    await set_setting("news_display_seconds", str(news_display_seconds))
    await set_setting("rtmp_url", str(rtmp_url))

    # Update stream manager with new config
    stream_manager.update_config(
        stream_key=str(youtube_stream_key),
        rtmp_url=str(rtmp_url),
        display_seconds=int(news_display_seconds)
    )

    # Return updated status panel
    status = stream_manager.get_status()
    counts = await get_counts_by_status()
    config = await get_stream_config()

    return templates.TemplateResponse(
        "components/stream_status.html",
        {
            "request": request,
            "stream_status": status,
            "counts": counts,
            "config": config,
            "message": "Configuration saved!",
        }
    )


@router.post("/api/config/youtube-embed", response_class=HTMLResponse)
async def update_youtube_embed(request: Request):
    """Update YouTube video ID for embed preview."""
    form_data = await request.form()
    youtube_video_id = form_data.get("youtube_video_id", "")

    # Extract video ID if full URL was pasted
    video_id = str(youtube_video_id).strip()
    if "youtube.com" in video_id or "youtu.be" in video_id:
        # Extract ID from URL
        import re
        match = re.search(r'(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})', video_id)
        if match:
            video_id = match.group(1)

    await set_setting("youtube_video_id", video_id)

    # Return updated status panel
    status = stream_manager.get_status()
    counts = await get_counts_by_status()
    config = await get_stream_config()

    return templates.TemplateResponse(
        "components/stream_status.html",
        {
            "request": request,
            "stream_status": status,
            "counts": counts,
            "config": config,
            "message": "YouTube embed updated!" if video_id else "YouTube embed removed.",
        }
    )


# --- News Fetching ---

@router.post("/api/news/fetch", response_class=HTMLResponse)
async def fetch_news_now(request: Request, background_tasks: BackgroundTasks):
    """Manually trigger news fetching."""
    from ..news.fetcher import fetch_news
    from ..ai.summarizer import summarize_news
    from ..db.database import create_news_item, update_news_item
    from ..db.models import NewsItemCreate, NewsItemUpdate
    from ..video.frame_generator import generate_frame

    async def do_fetch():
        items = await fetch_news()
        for item in items:
            result = await summarize_news(item["title"], item["description"])
            if result:
                news_item = NewsItemCreate(
                    title=result["headline"],
                    original_title=item["title"],
                    summary=result["summary"],
                    source_name=item["source_name"],
                    source_url=item["url"],
                )
                created = await create_news_item(news_item)
                if created:
                    # Auto-generate frame
                    try:
                        frame_path = generate_frame(created)
                        await update_news_item(created.id, NewsItemUpdate(frame_path=str(frame_path)))
                    except Exception:
                        pass

    background_tasks.add_task(do_fetch)

    # Return the current queue
    items = await get_news_items_by_status(NewsStatus.APPROVED, limit=100)
    return templates.TemplateResponse(
        "components/news_queue.html",
        {
            "request": request,
            "items": items,
            "message": "Fetching news in background...",
        }
    )


# --- Dashboard Fragments ---

@router.get("/api/counts", response_class=HTMLResponse)
async def get_counts(request: Request):
    """Get status counts (returns HTML fragment)."""
    counts = await get_counts_by_status()

    return templates.TemplateResponse(
        "components/status_tabs.html",
        {
            "request": request,
            "counts": counts,
        }
    )


# --- Helper Functions ---

async def _generate_frame_task(item_id: int):
    """Background task to generate frame for an item."""
    item = await get_news_item(item_id)
    if item:
        frame_path = generate_frame(item)
        await update_news_item(item_id, NewsItemUpdate(frame_path=str(frame_path)))
