"""
News Video Generator - Creates broadcast-style news segments with:
- Animated background
- News content overlay
- Scrolling ticker
- Professional transitions
"""

from PIL import Image, ImageDraw, ImageFont, ImageFilter
from pathlib import Path
from typing import Optional, List, Tuple
import subprocess
import logging
import math
import os

from ..config import settings
from ..db.models import NewsItem

logger = logging.getLogger(__name__)

# Colors - Broadcast news theme
COLORS = {
    "bg_dark": "#0a0a12",
    "bg_gradient_top": "#0f1922",
    "bg_gradient_bottom": "#050510",
    "primary": "#00ff88",
    "secondary": "#00aaff",
    "accent": "#ff3366",
    "text_white": "#ffffff",
    "text_dim": "#aaaaaa",
    "ticker_bg": "#cc0000",
    "ticker_text": "#ffffff",
    "panel_bg": "rgba(10, 10, 20, 200)",
    "breaking": "#ff0000",
}

# Frame settings
TICKER_HEIGHT = 60
HEADER_HEIGHT = 80


def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Get a font, with fallbacks."""
    font_paths = [
        settings.assets_path / "fonts" / "JetBrainsMono-Bold.ttf" if bold else settings.assets_path / "fonts" / "JetBrainsMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ]

    for font_path in font_paths:
        if Path(font_path).exists():
            try:
                return ImageFont.truetype(str(font_path), size)
            except Exception:
                continue

    return ImageFont.load_default()


def create_animated_background(width: int, height: int, frame_count: int = 30) -> List[Image.Image]:
    """Create animated background frames with subtle movement."""
    frames = []

    for i in range(frame_count):
        img = Image.new('RGB', (width, height), COLORS["bg_dark"])
        draw = ImageDraw.Draw(img)

        # Animated grid pattern
        phase = (i / frame_count) * 2 * math.pi

        # Horizontal scan lines (subtle)
        for y in range(0, height, 4):
            alpha = int(15 + 5 * math.sin(phase + y * 0.01))
            draw.line([(0, y), (width, y)], fill=f'#{alpha:02x}{alpha:02x}{alpha + 5:02x}')

        # Vertical grid with movement
        offset = int(10 * math.sin(phase))
        grid_color = '#0a1520'
        for x in range(-50 + offset, width + 50, 100):
            draw.line([(x, 0), (x, height)], fill=grid_color, width=1)

        # Glowing orbs in corners (pulsing)
        pulse = 0.5 + 0.5 * math.sin(phase)
        glow_alpha = int(30 * pulse)

        # Create glow effect
        for radius in range(150, 0, -10):
            alpha = int(glow_alpha * (1 - radius / 150))
            if alpha > 0:
                # Top left glow (cyan)
                draw.ellipse([(-radius, -radius), (radius, radius)],
                           fill=f'#{0:02x}{alpha:02x}{int(alpha*0.7):02x}')
                # Bottom right glow (green)
                draw.ellipse([(width - radius, height - radius), (width + radius, height + radius)],
                           fill=f'#{0:02x}{int(alpha*0.7):02x}{alpha:02x}')

        frames.append(img)

    return frames


def draw_header(draw: ImageDraw.Draw, width: int, frame_num: int = 0):
    """Draw the broadcast header."""
    # Header background
    draw.rectangle([(0, 0), (width, HEADER_HEIGHT)], fill='#0a0a15')
    draw.line([(0, HEADER_HEIGHT), (width, HEADER_HEIGHT)], fill=COLORS["primary"], width=2)

    # Live indicator with pulse
    pulse = 0.5 + 0.5 * math.sin(frame_num * 0.3)
    live_color = f'#{int(255 * pulse):02x}0033'
    draw.ellipse([30, 25, 55, 50], fill=live_color)
    draw.ellipse([33, 28, 52, 47], fill='#ff0033')

    live_font = get_font(22, bold=True)
    draw.text((65, 27), "LIVE", font=live_font, fill='#ff0033')

    # Channel name
    title_font = get_font(32, bold=True)
    title = "CYBERSEC NEWS NETWORK"
    draw.text((140, 22), title, font=title_font, fill=COLORS["text_white"])

    # Current date/time area
    from datetime import datetime
    now = datetime.now()
    time_str = now.strftime("%H:%M")
    date_str = now.strftime("%b %d, %Y")

    time_font = get_font(28, bold=True)
    date_font = get_font(18)

    draw.text((width - 150, 18), time_str, font=time_font, fill=COLORS["text_white"])
    draw.text((width - 150, 48), date_str, font=date_font, fill=COLORS["text_dim"])


def draw_news_panel(draw: ImageDraw.Draw, img: Image.Image, news_item: NewsItem,
                    width: int, height: int, panel_y: int):
    """Draw the main news content panel."""
    panel_margin = 60
    panel_width = width - (panel_margin * 2) - 280  # Leave room for side panel
    panel_height = height - panel_y - TICKER_HEIGHT - 40

    # Semi-transparent panel background
    panel = Image.new('RGBA', (panel_width, panel_height), (10, 10, 20, 220))
    panel_draw = ImageDraw.Draw(panel)

    # Panel border
    panel_draw.rectangle([(0, 0), (panel_width - 1, panel_height - 1)],
                        outline=COLORS["primary"], width=2)

    # Category tag
    tag_font = get_font(16, bold=True)
    tag_text = "BREAKING" if "critical" in news_item.title.lower() or "breach" in news_item.title.lower() else "CYBERSECURITY"
    tag_color = COLORS["breaking"] if tag_text == "BREAKING" else COLORS["secondary"]

    tag_bbox = panel_draw.textbbox((0, 0), tag_text, font=tag_font)
    tag_width = tag_bbox[2] - tag_bbox[0] + 20
    panel_draw.rectangle([(20, 20), (20 + tag_width, 50)], fill=tag_color)
    panel_draw.text((30, 24), tag_text, font=tag_font, fill=COLORS["text_white"])

    # Headline
    headline_font = get_font(42, bold=True)
    headline = news_item.title[:120]

    # Word wrap headline
    words = headline.split()
    lines = []
    current_line = []

    for word in words:
        current_line.append(word)
        test_line = ' '.join(current_line)
        bbox = panel_draw.textbbox((0, 0), test_line, font=headline_font)
        if bbox[2] - bbox[0] > panel_width - 60:
            current_line.pop()
            if current_line:
                lines.append(' '.join(current_line))
            current_line = [word]
    if current_line:
        lines.append(' '.join(current_line))

    headline_y = 70
    for line in lines[:3]:  # Max 3 lines
        panel_draw.text((30, headline_y), line, font=headline_font, fill=COLORS["primary"])
        headline_y += 52

    # Summary
    summary_font = get_font(26)
    summary = news_item.summary[:400]

    # Word wrap summary
    words = summary.split()
    lines = []
    current_line = []

    for word in words:
        current_line.append(word)
        test_line = ' '.join(current_line)
        bbox = panel_draw.textbbox((0, 0), test_line, font=summary_font)
        if bbox[2] - bbox[0] > panel_width - 60:
            current_line.pop()
            if current_line:
                lines.append(' '.join(current_line))
            current_line = [word]
    if current_line:
        lines.append(' '.join(current_line))

    summary_y = headline_y + 30
    for line in lines[:6]:  # Max 6 lines
        panel_draw.text((30, summary_y), line, font=summary_font, fill=COLORS["text_white"])
        summary_y += 34

    # Source
    source_font = get_font(20)
    source_y = panel_height - 50
    panel_draw.text((30, source_y), f"Source: {news_item.source_name}",
                   font=source_font, fill=COLORS["text_dim"])

    # Paste panel onto main image
    img.paste(panel, (panel_margin, panel_y), panel)


def draw_side_panel(draw: ImageDraw.Draw, img: Image.Image, news_item: NewsItem,
                    width: int, height: int):
    """Draw side panel with QR code and additional info."""
    from .qr_generator import generate_qr_code

    panel_width = 240
    panel_x = width - panel_width - 40
    panel_y = HEADER_HEIGHT + 30
    panel_height = height - panel_y - TICKER_HEIGHT - 40

    # Side panel background
    side_panel = Image.new('RGBA', (panel_width, panel_height), (15, 15, 25, 230))
    side_draw = ImageDraw.Draw(side_panel)

    # Border
    side_draw.rectangle([(0, 0), (panel_width - 1, panel_height - 1)],
                       outline=COLORS["secondary"], width=1)

    # QR Code
    qr_size = 180
    qr_x = (panel_width - qr_size) // 2
    try:
        qr_img = generate_qr_code(news_item.source_url, qr_size)
        side_panel.paste(qr_img, (qr_x, 30), qr_img)
    except Exception as e:
        logger.warning(f"QR generation failed: {e}")

    # QR label
    label_font = get_font(14)
    label = "SCAN FOR FULL STORY"
    label_bbox = side_draw.textbbox((0, 0), label, font=label_font)
    label_x = (panel_width - (label_bbox[2] - label_bbox[0])) // 2
    side_draw.text((label_x, qr_size + 40), label, font=label_font, fill=COLORS["text_dim"])

    # Decorative element
    side_draw.line([(20, qr_size + 70), (panel_width - 20, qr_size + 70)],
                  fill=COLORS["secondary"], width=1)

    # Stats or additional info
    info_font = get_font(12)
    side_draw.text((20, qr_size + 90), "24/7 AUTOMATED FEED", font=info_font, fill=COLORS["text_dim"])

    img.paste(side_panel, (panel_x, panel_y), side_panel)


def draw_ticker(draw: ImageDraw.Draw, width: int, height: int,
                ticker_text: str, scroll_offset: int):
    """Draw scrolling news ticker at bottom."""
    ticker_y = height - TICKER_HEIGHT

    # Ticker background
    draw.rectangle([(0, ticker_y), (width, height)], fill=COLORS["ticker_bg"])

    # "NEXT UP" label
    label_font = get_font(18, bold=True)
    label_bg_width = 120
    draw.rectangle([(0, ticker_y), (label_bg_width, height)], fill='#000000')
    draw.text((15, ticker_y + 18), "NEXT UP", font=label_font, fill=COLORS["text_white"])

    # Scrolling text
    ticker_font = get_font(24, bold=True)

    # Calculate text position with scroll
    text_x = label_bg_width + 30 - scroll_offset

    # Draw ticker text (repeat for seamless scroll)
    full_text = ticker_text + "     •     " + ticker_text
    draw.text((text_x, ticker_y + 15), full_text, font=ticker_font, fill=COLORS["ticker_text"])

    # Top border accent
    draw.line([(0, ticker_y), (width, ticker_y)], fill=COLORS["primary"], width=3)


def generate_news_segment(news_item: NewsItem, next_items: List[NewsItem],
                          duration_seconds: int = 30, fps: int = 30) -> Path:
    """
    Generate a video segment for a news item with animations.

    Returns path to the generated video segment.
    """
    width = settings.frame_width
    height = settings.frame_height
    total_frames = duration_seconds * fps

    # Prepare output directory
    segments_dir = Path(settings.database_path).parent / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)

    frames_dir = segments_dir / f"frames_{news_item.id}"
    frames_dir.mkdir(parents=True, exist_ok=True)

    # Generate background frames (reusable)
    bg_frames = create_animated_background(width, height, frame_count=fps)  # 1 second of unique bg

    # Prepare ticker text from next items
    ticker_parts = []
    for item in next_items[:5]:
        ticker_parts.append(item.title[:80])
    ticker_text = "  •  ".join(ticker_parts) if ticker_parts else "More cybersecurity news coming up..."

    # Calculate ticker scroll speed (pixels per frame)
    ticker_font = get_font(24, bold=True)
    temp_img = Image.new('RGB', (1, 1))
    temp_draw = ImageDraw.Draw(temp_img)
    ticker_bbox = temp_draw.textbbox((0, 0), ticker_text + "     •     ", font=ticker_font)
    ticker_width = ticker_bbox[2] - ticker_bbox[0]
    scroll_speed = (ticker_width + width) / total_frames

    logger.info(f"Generating {total_frames} frames for news segment...")

    # Generate all frames
    for frame_num in range(total_frames):
        # Get background frame (cycle through)
        bg_index = frame_num % len(bg_frames)
        img = bg_frames[bg_index].copy()
        draw = ImageDraw.Draw(img)

        # Draw header
        draw_header(draw, width, frame_num)

        # Draw news panel (with fade-in effect for first 15 frames)
        alpha = min(255, int((frame_num / 15) * 255)) if frame_num < 15 else 255

        # Create content overlay
        content_img = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        content_draw = ImageDraw.Draw(content_img)

        panel_y = HEADER_HEIGHT + 20
        draw_news_panel(content_draw, content_img, news_item, width, height, panel_y)
        draw_side_panel(content_draw, content_img, news_item, width, height)

        # Apply fade-in
        if alpha < 255:
            content_img.putalpha(alpha)

        img = Image.alpha_composite(img.convert('RGBA'), content_img)
        img = img.convert('RGB')
        draw = ImageDraw.Draw(img)

        # Draw ticker with scroll
        scroll_offset = int(frame_num * scroll_speed) % (ticker_width + 200)
        draw_ticker(draw, width, height, ticker_text, scroll_offset)

        # Save frame
        frame_path = frames_dir / f"frame_{frame_num:05d}.png"
        img.save(frame_path, 'PNG')

        if frame_num % (fps * 5) == 0:  # Log every 5 seconds
            logger.info(f"Generated frame {frame_num}/{total_frames}")

    # Use FFmpeg to create video from frames
    output_path = segments_dir / f"segment_{news_item.id}.mp4"

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%05d.png"),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "23",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(output_path)
    ]

    logger.info("Encoding video segment with FFmpeg...")
    result = subprocess.run(ffmpeg_cmd, capture_output=True, timeout=300)

    if result.returncode != 0:
        logger.error(f"FFmpeg error: {result.stderr.decode()[-500:]}")
        raise RuntimeError("Failed to encode video segment")

    # Cleanup frames
    for f in frames_dir.glob("*.png"):
        f.unlink()
    frames_dir.rmdir()

    logger.info(f"Generated video segment: {output_path}")
    return output_path


def generate_transition(duration_ms: int = 500, fps: int = 30) -> Path:
    """Generate a transition video clip."""
    width = settings.frame_width
    height = settings.frame_height
    total_frames = int((duration_ms / 1000) * fps)

    segments_dir = Path(settings.database_path).parent / "segments"
    segments_dir.mkdir(parents=True, exist_ok=True)

    frames_dir = segments_dir / "transition_frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    for frame_num in range(total_frames):
        progress = frame_num / total_frames

        img = Image.new('RGB', (width, height), COLORS["bg_dark"])
        draw = ImageDraw.Draw(img)

        # Wipe effect - green line moving across
        wipe_x = int(width * progress)

        # Trail effect
        for i in range(50):
            alpha = int(255 * (1 - i / 50))
            x = wipe_x - i * 3
            if x > 0:
                draw.line([(x, 0), (x, height)], fill=f'#00{alpha:02x}44', width=2)

        # Main wipe line
        draw.line([(wipe_x, 0), (wipe_x, height)], fill=COLORS["primary"], width=4)

        # Flash at the line
        glow_width = 30
        for i in range(glow_width):
            alpha = int(100 * (1 - i / glow_width))
            draw.line([(wipe_x + i, 0), (wipe_x + i, height)],
                     fill=f'#00{alpha:02x}88', width=1)

        frame_path = frames_dir / f"trans_{frame_num:03d}.png"
        img.save(frame_path, 'PNG')

    output_path = segments_dir / "transition.mp4"

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "trans_%03d.png"),
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
        str(output_path)
    ]

    subprocess.run(ffmpeg_cmd, capture_output=True, timeout=60)

    # Cleanup
    for f in frames_dir.glob("*.png"):
        f.unlink()
    frames_dir.rmdir()

    return output_path
