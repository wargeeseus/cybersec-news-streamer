from PIL import Image, ImageDraw, ImageFont
from pathlib import Path
from typing import Optional
import textwrap
import logging

from ..config import settings
from ..db.models import NewsItem
from .qr_generator import generate_qr_code

logger = logging.getLogger(__name__)

# Colors - Cyber theme
COLORS = {
    "background": "#0a0a0f",
    "primary": "#00ff88",
    "secondary": "#00aaff",
    "text": "#ffffff",
    "text_dim": "#888888",
    "accent": "#ff0066",
    "panel": "#12121a",
    "border": "#1a1a2e",
}


def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Get a font, falling back to default if custom font not available."""
    font_paths = [
        settings.assets_path / "fonts" / "JetBrainsMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
    ]

    for font_path in font_paths:
        if Path(font_path).exists():
            try:
                return ImageFont.truetype(str(font_path), size)
            except Exception:
                continue

    # Fallback to default
    return ImageFont.load_default()


def generate_frame(news_item: NewsItem, output_path: Optional[Path] = None) -> Path:
    """
    Generate a video frame for a news item.

    Args:
        news_item: The news item to render
        output_path: Optional custom output path

    Returns:
        Path to the generated frame image
    """
    width = settings.frame_width
    height = settings.frame_height

    # Create base image
    img = Image.new('RGBA', (width, height), COLORS["background"])
    draw = ImageDraw.Draw(img)

    # Load background if exists
    bg_path = settings.assets_path / "backgrounds" / "dark_cyber.png"
    if bg_path.exists():
        try:
            bg = Image.open(bg_path).convert('RGBA')
            bg = bg.resize((width, height), Image.Resampling.LANCZOS)
            img.paste(bg, (0, 0))
            draw = ImageDraw.Draw(img)
        except Exception as e:
            logger.warning(f"Could not load background: {e}")

    # Draw decorative elements
    _draw_decorations(draw, width, height)

    # Draw header
    _draw_header(draw, width)

    # Draw main content panel
    panel_margin = 80
    panel_top = 150
    panel_height = height - panel_top - 220  # More room for footer

    # Panel background
    draw.rounded_rectangle(
        [panel_margin, panel_top, width - panel_margin, panel_top + panel_height],
        radius=20,
        fill=COLORS["panel"],
        outline=COLORS["border"],
        width=2
    )

    # Draw headline
    headline_font = get_font(48, bold=True)
    headline_text = news_item.title[:100]

    # Wrap headline text
    headline_wrapped = textwrap.fill(headline_text, width=45)
    headline_y = panel_top + 40

    draw.text(
        (panel_margin + 40, headline_y),
        headline_wrapped,
        font=headline_font,
        fill=COLORS["primary"]
    )

    # Calculate headline height
    headline_lines = headline_wrapped.count('\n') + 1
    headline_height = headline_lines * 56

    # Draw summary (narrower to leave room for QR code on right)
    summary_font = get_font(32)
    summary_y = headline_y + headline_height + 30

    summary_wrapped = textwrap.fill(news_item.summary, width=50)
    draw.text(
        (panel_margin + 40, summary_y),
        summary_wrapped,
        font=summary_font,
        fill=COLORS["text"]
    )

    # Draw source info
    source_font = get_font(24)
    source_y = panel_top + panel_height - 60

    draw.text(
        (panel_margin + 40, source_y),
        f"Source: {news_item.source_name}",
        font=source_font,
        fill=COLORS["text_dim"]
    )

    # Draw QR code - right middle of frame (bigger size)
    qr_size = 220
    qr_x = width - panel_margin - qr_size - 30
    qr_y = (height // 2) - (qr_size // 2)  # Center vertically

    try:
        qr_img = generate_qr_code(news_item.source_url, qr_size)
        img.paste(qr_img, (qr_x, qr_y), qr_img)

        # QR label
        qr_label_font = get_font(18)
        label_text = "Scan for source"
        label_bbox = draw.textbbox((0, 0), label_text, font=qr_label_font)
        label_width = label_bbox[2] - label_bbox[0]
        label_x = qr_x + (qr_size - label_width) // 2
        draw.text(
            (label_x, qr_y + qr_size + 10),
            label_text,
            font=qr_label_font,
            fill=COLORS["text_dim"]
        )
    except Exception as e:
        logger.warning(f"Could not generate QR code: {e}")

    # Draw footer
    _draw_footer(draw, width, height)

    # Save the frame
    if output_path is None:
        frames_dir = Path(settings.database_path).parent / "frames"
        frames_dir.mkdir(parents=True, exist_ok=True)
        output_path = frames_dir / f"frame_{news_item.id}.png"

    img = img.convert('RGB')
    img.save(output_path, 'PNG', quality=95)

    logger.info(f"Generated frame: {output_path}")
    return output_path


def _draw_decorations(draw: ImageDraw.Draw, width: int, height: int):
    """Draw decorative cyber elements."""
    # Top accent line
    draw.line([(0, 5), (width, 5)], fill=COLORS["primary"], width=2)

    # Corner accents
    corner_size = 30
    # Top left
    draw.line([(0, 20), (corner_size, 20)], fill=COLORS["secondary"], width=2)
    draw.line([(20, 0), (20, corner_size)], fill=COLORS["secondary"], width=2)
    # Top right
    draw.line([(width - corner_size, 20), (width, 20)], fill=COLORS["secondary"], width=2)
    draw.line([(width - 20, 0), (width - 20, corner_size)], fill=COLORS["secondary"], width=2)


def _draw_header(draw: ImageDraw.Draw, width: int):
    """Draw the header with branding."""
    header_font = get_font(36, bold=True)
    live_font = get_font(24)

    # Live indicator
    draw.ellipse([40, 50, 60, 70], fill=COLORS["accent"])
    draw.text((70, 48), "LIVE", font=live_font, fill=COLORS["accent"])

    # Title
    title = "CYBERSEC NEWS"
    title_bbox = draw.textbbox((0, 0), title, font=header_font)
    title_width = title_bbox[2] - title_bbox[0]
    title_x = (width - title_width) // 2

    draw.text((title_x, 45), title, font=header_font, fill=COLORS["text"])


def _draw_footer(draw: ImageDraw.Draw, width: int, height: int):
    """Draw the footer with today's date."""
    from datetime import datetime

    # Get current date
    today = datetime.now()

    footer_y = height - 80

    # Separator line
    draw.line([(80, footer_y - 10), (width - 80, footer_y - 10)],
              fill=COLORS["border"], width=1)

    # Today's date - centered and prominent
    date_font = get_font(32)
    date_str = today.strftime("%A, %B %d, %Y")
    date_bbox = draw.textbbox((0, 0), date_str, font=date_font)
    date_width = date_bbox[2] - date_bbox[0]
    date_x = (width - date_width) // 2

    draw.text(
        (date_x, footer_y + 5),
        date_str,
        font=date_font,
        fill=COLORS["text"]
    )

    # Auto-generated label - bottom right
    label_font = get_font(14)
    draw.text(
        (width - 280, footer_y + 45),
        "Auto-generated news stream",
        font=label_font,
        fill=COLORS["text_dim"]
    )


async def generate_frame_for_item(news_item: NewsItem) -> Optional[str]:
    """Generate frame and return the path as string."""
    try:
        path = generate_frame(news_item)
        return str(path)
    except Exception as e:
        logger.error(f"Failed to generate frame for item {news_item.id}: {e}")
        return None
