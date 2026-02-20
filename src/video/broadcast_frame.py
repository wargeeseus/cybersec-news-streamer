"""
Broadcast Frame Generator - Creates overlay images for the broadcast stream.
The actual animation (ticker, transitions) is handled by FFmpeg filters.
"""

from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
from typing import Optional, List
import logging

from ..config import settings
from ..db.models import NewsItem

logger = logging.getLogger(__name__)

# Colors
COLORS = {
    "bg_dark": "#0a0a12",
    "primary": "#00ff88",
    "secondary": "#00aaff",
    "accent": "#ff3366",
    "text_white": "#ffffff",
    "text_dim": "#888888",
    "panel_bg": (10, 10, 20, 230),
    "ticker_bg": "#cc0000",
}

TICKER_HEIGHT = 70
HEADER_HEIGHT = 90


def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Get font with fallbacks."""
    font_paths = [
        settings.assets_path / "fonts" / "JetBrainsMono-Bold.ttf" if bold else settings.assets_path / "fonts" / "JetBrainsMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ]
    for path in font_paths:
        if Path(path).exists():
            try:
                return ImageFont.truetype(str(path), size)
            except:
                continue
    return ImageFont.load_default()


def generate_broadcast_overlay(news_item: NewsItem, next_items: List[NewsItem] = None) -> Path:
    """
    Generate the static overlay image for a news item.
    This will be composited over an animated background by FFmpeg.
    Ticker animation is handled by FFmpeg drawtext filter.
    """
    width = settings.frame_width
    height = settings.frame_height

    # Create transparent overlay
    img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # === HEADER BAR ===
    draw.rectangle([(0, 0), (width, HEADER_HEIGHT)], fill=(10, 10, 20, 240))
    draw.line([(0, HEADER_HEIGHT), (width, HEADER_HEIGHT)], fill=COLORS["primary"], width=3)

    # Live indicator
    draw.ellipse([30, 30, 58, 58], fill=COLORS["accent"])
    live_font = get_font(24, bold=True)
    draw.text((70, 32), "LIVE", font=live_font, fill=COLORS["accent"])

    # Channel title
    title_font = get_font(36, bold=True)
    draw.text((150, 25), "CYBERSEC NEWS NETWORK", font=title_font, fill=COLORS["text_white"])

    # Date/Time in configured timezone
    from datetime import datetime
    from zoneinfo import ZoneInfo
    try:
        tz = ZoneInfo(settings.timezone)
        now = datetime.now(tz)
    except Exception:
        now = datetime.now()
    time_font = get_font(30, bold=True)
    date_font = get_font(18)
    draw.text((width - 160, 20), now.strftime("%H:%M"), font=time_font, fill=COLORS["text_white"])
    draw.text((width - 160, 55), now.strftime("%b %d, %Y"), font=date_font, fill=COLORS["text_dim"])

    # === MAIN NEWS PANEL ===
    panel_margin = 50
    panel_top = HEADER_HEIGHT + 30
    panel_width = width - panel_margin * 2 - 280  # Room for QR panel
    panel_height = height - panel_top - TICKER_HEIGHT - 50

    # Panel background
    panel = Image.new('RGBA', (panel_width, panel_height), COLORS["panel_bg"])
    panel_draw = ImageDraw.Draw(panel)

    # Panel border
    panel_draw.rectangle([(0, 0), (panel_width-1, panel_height-1)],
                        outline=COLORS["primary"], width=2)

    # Category tag
    tag_font = get_font(16, bold=True)
    is_breaking = any(word in news_item.title.lower() for word in
                     ['critical', 'breach', 'attack', 'ransomware', 'zero-day', 'urgent'])
    tag_text = "BREAKING" if is_breaking else "CYBERSECURITY"
    tag_color = COLORS["accent"] if is_breaking else COLORS["secondary"]

    tag_bbox = panel_draw.textbbox((0, 0), tag_text, font=tag_font)
    tag_w = tag_bbox[2] - tag_bbox[0] + 24
    panel_draw.rectangle([(25, 25), (25 + tag_w, 55)], fill=tag_color)
    panel_draw.text((37, 28), tag_text, font=tag_font, fill=COLORS["text_white"])

    # Headline
    headline_font = get_font(44, bold=True)
    headline = news_item.title[:130]

    # Word wrap
    lines = []
    words = headline.split()
    current = []
    for word in words:
        current.append(word)
        line = ' '.join(current)
        bbox = panel_draw.textbbox((0, 0), line, font=headline_font)
        if bbox[2] - bbox[0] > panel_width - 60:
            current.pop()
            if current:
                lines.append(' '.join(current))
            current = [word]
    if current:
        lines.append(' '.join(current))

    y = 80
    for line in lines[:3]:
        panel_draw.text((30, y), line, font=headline_font, fill=COLORS["primary"])
        y += 55

    # Summary
    summary_font = get_font(28)
    summary = news_item.summary[:450]

    lines = []
    words = summary.split()
    current = []
    for word in words:
        current.append(word)
        line = ' '.join(current)
        bbox = panel_draw.textbbox((0, 0), line, font=summary_font)
        if bbox[2] - bbox[0] > panel_width - 60:
            current.pop()
            if current:
                lines.append(' '.join(current))
            current = [word]
    if current:
        lines.append(' '.join(current))

    y += 25
    for line in lines[:7]:
        panel_draw.text((30, y), line, font=summary_font, fill=COLORS["text_white"])
        y += 38

    # Source
    source_font = get_font(22)
    panel_draw.text((30, panel_height - 55), f"Source: {news_item.source_name}",
                   font=source_font, fill=COLORS["text_dim"])

    img.paste(panel, (panel_margin, panel_top), panel)

    # === QR CODE PANEL ===
    qr_panel_width = 250
    qr_panel_x = width - qr_panel_width - 40
    qr_panel_y = HEADER_HEIGHT + 30
    qr_panel_height = panel_height

    qr_panel = Image.new('RGBA', (qr_panel_width, qr_panel_height), (15, 15, 25, 230))
    qr_draw = ImageDraw.Draw(qr_panel)
    qr_draw.rectangle([(0, 0), (qr_panel_width-1, qr_panel_height-1)],
                     outline=COLORS["secondary"], width=1)

    # QR Code
    from .qr_generator import generate_qr_code
    qr_size = 190
    try:
        qr_img = generate_qr_code(news_item.source_url, qr_size)
        qr_x = (qr_panel_width - qr_size) // 2
        qr_panel.paste(qr_img, (qr_x, 30), qr_img)
    except Exception as e:
        logger.warning(f"QR failed: {e}")

    # QR label
    label_font = get_font(14)
    qr_draw.text((35, qr_size + 50), "SCAN FOR FULL STORY", font=label_font, fill=COLORS["text_dim"])

    # Separator
    qr_draw.line([(20, qr_size + 85), (qr_panel_width - 20, qr_size + 85)],
                fill=COLORS["secondary"], width=1)

    # Info
    info_font = get_font(13)
    qr_draw.text((30, qr_size + 105), "24/7 AUTOMATED FEED", font=info_font, fill=COLORS["text_dim"])
    qr_draw.text((30, qr_size + 130), "CYBERSEC NEWS NETWORK", font=info_font, fill=COLORS["primary"])

    img.paste(qr_panel, (qr_panel_x, qr_panel_y), qr_panel)

    # === TICKER BAR (static part - text animated by FFmpeg) ===
    ticker_y = height - TICKER_HEIGHT
    draw.rectangle([(0, ticker_y), (width, height)], fill=COLORS["ticker_bg"])
    draw.line([(0, ticker_y), (width, ticker_y)], fill=COLORS["primary"], width=4)

    # "UP NEXT" label
    label_width = 140
    draw.rectangle([(0, ticker_y), (label_width, height)], fill=(0, 0, 0, 255))
    label_font = get_font(22, bold=True)
    draw.text((20, ticker_y + 22), "UP NEXT", font=label_font, fill=COLORS["text_white"])

    # Save
    overlay_dir = Path(settings.database_path).parent / "overlays"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    output_path = overlay_dir / f"overlay_{news_item.id}.png"

    img.save(output_path, 'PNG')
    logger.info(f"Generated broadcast overlay: {output_path}")
    return output_path


def generate_background_video(duration_seconds: int = 60, fps: int = 15) -> Path:
    """
    Generate a looping animated background video.
    This only needs to be done once.
    """
    import subprocess
    import math

    width = settings.frame_width
    height = settings.frame_height

    bg_dir = Path(settings.database_path).parent / "background"
    bg_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = bg_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    total_frames = duration_seconds * fps
    logger.info(f"Generating {total_frames} background frames...")

    for i in range(total_frames):
        img = Image.new('RGB', (width, height), COLORS["bg_dark"])
        draw = ImageDraw.Draw(img)

        phase = (i / total_frames) * 2 * math.pi * 4  # 4 full cycles

        # Animated scan lines
        for y in range(0, height, 3):
            intensity = int(8 + 4 * math.sin(phase + y * 0.02))
            draw.line([(0, y), (width, y)], fill=f'#{intensity:02x}{intensity:02x}{intensity+2:02x}')

        # Moving vertical grid
        offset = int(50 * math.sin(phase * 0.5))
        grid_color = '#0a1218'
        for x in range(-100 + offset, width + 100, 80):
            draw.line([(x, 0), (x, height)], fill=grid_color, width=1)

        # Corner glows (pulsing)
        pulse = 0.4 + 0.6 * math.sin(phase)
        glow = int(25 * pulse)

        # Top-left cyan glow
        for r in range(200, 0, -15):
            a = int(glow * (1 - r/200))
            if a > 0:
                draw.ellipse([(-r, -r), (r, r)], fill=f'#{0:02x}{a:02x}{int(a*0.6):02x}')

        # Bottom-right green glow
        for r in range(200, 0, -15):
            a = int(glow * (1 - r/200))
            if a > 0:
                draw.ellipse([(width-r, height-r), (width+r, height+r)],
                           fill=f'#{0:02x}{int(a*0.6):02x}{a:02x}')

        frame_path = frames_dir / f"bg_{i:04d}.png"
        img.save(frame_path, 'PNG')

        if i % (fps * 10) == 0:
            logger.info(f"Background: {i}/{total_frames} frames")

    # Encode to video
    output_path = bg_dir / "background_loop.mp4"

    cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "bg_%04d.png"),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        str(output_path)
    ]

    logger.info("Encoding background video...")
    subprocess.run(cmd, capture_output=True, timeout=300)

    # Cleanup frames
    for f in frames_dir.glob("*.png"):
        f.unlink()
    frames_dir.rmdir()

    logger.info(f"Background video created: {output_path}")
    return output_path


def get_ticker_text(items: List[NewsItem], current_index: int) -> str:
    """Get ticker text from upcoming news items."""
    # Get items after current one
    next_items = items[current_index+1:] + items[:current_index]

    parts = []
    for item in next_items[:5]:
        parts.append(item.title[:80])

    return "  ★  ".join(parts) if parts else "More cybersecurity news coming up..."
