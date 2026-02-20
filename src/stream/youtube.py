import subprocess
import asyncio
import logging
import os
from pathlib import Path
from typing import Optional, List
from enum import Enum

from ..config import settings
from ..db.database import get_news_items_by_status, update_news_item, get_setting
from ..db.models import NewsStatus, NewsItemUpdate, NewsItem
from ..video.frame_generator import generate_frame

logger = logging.getLogger(__name__)


class StreamState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class YouTubeStreamer:
    """Manages FFmpeg streaming to YouTube Live - supports simple and broadcast modes."""

    def __init__(self, broadcast_mode: bool = False):
        self.process: Optional[subprocess.Popen] = None
        self.state = StreamState.STOPPED
        self.current_item_id: Optional[int] = None
        self.current_item_title: Optional[str] = None
        self._stop_event = asyncio.Event()
        self._slideshow_task: Optional[asyncio.Task] = None

        # Stream mode
        self._broadcast_mode = broadcast_mode

        # Runtime config
        self._stream_key: str = settings.youtube_stream_key
        self._rtmp_url: str = "rtmp://a.rtmp.youtube.com/live2"
        self._display_seconds: int = settings.news_display_seconds
        self._channel_id: Optional[int] = None

        # Auto-restart settings
        self._max_retries: int = 10
        self._retry_delay: int = 5
        self._consecutive_failures: int = 0

        # Paths
        self._data_dir = Path(settings.database_path).parent
        self._frames_dir = self._data_dir / "frames"
        self._current_frame = self._data_dir / "current_frame.png"
        self._playlist_file = self._data_dir / "playlist.txt"
        self._generated_bg_video = self._data_dir / "background" / "background_loop.mp4"
        self._custom_bg_video = settings.assets_path / "video" / "background.mp4"
        self._music_path = settings.assets_path / "music" / "background.mp3"

    async def load_config_from_db(self):
        """Load configuration from database."""
        stream_key = await get_setting('youtube_stream_key', '')
        rtmp_url = await get_setting('rtmp_url', 'rtmp://a.rtmp.youtube.com/live2')
        display_seconds = await get_setting('news_display_seconds', '30')

        if stream_key:
            self._stream_key = stream_key
        if rtmp_url:
            self._rtmp_url = rtmp_url
        if display_seconds:
            self._display_seconds = int(display_seconds)

    def update_config(self, stream_key: str = None, rtmp_url: str = None,
                      display_seconds: int = None, channel_id: int = None,
                      broadcast_mode: bool = None):
        """Update stream configuration at runtime."""
        if stream_key is not None:
            self._stream_key = stream_key
        if rtmp_url is not None:
            self._rtmp_url = rtmp_url
        if display_seconds is not None:
            self._display_seconds = display_seconds
        if channel_id is not None:
            self._channel_id = channel_id
        if broadcast_mode is not None:
            self._broadcast_mode = broadcast_mode
        logger.info(f"Stream config updated: key={'*' * 8 if self._stream_key else 'not set'}, broadcast={self._broadcast_mode}")

    @property
    def stream_key(self) -> str:
        return self._stream_key

    @property
    def rtmp_full_url(self) -> str:
        return f"{self._rtmp_url}/{self._stream_key}"

    @property
    def is_running(self) -> bool:
        return self.state == StreamState.RUNNING

    @property
    def _background_video(self) -> Path:
        """Get background video path - prefer custom over generated."""
        if self._custom_bg_video.exists():
            return self._custom_bg_video
        return self._generated_bg_video

    async def start(self):
        """Start the stream with auto-restart."""
        if self.state == StreamState.RUNNING:
            logger.warning("Stream already running")
            return

        await self.load_config_from_db()

        if not self._stream_key:
            logger.error("No YouTube stream key configured")
            self.state = StreamState.ERROR
            return

        self.state = StreamState.STARTING
        self._stop_event.clear()
        self._frames_dir.mkdir(parents=True, exist_ok=True)
        self._consecutive_failures = 0

        mode = "BROADCAST" if self._broadcast_mode else "SIMPLE"
        logger.info(f"Starting YouTube stream in {mode} mode with auto-restart...")
        logger.info(f"Music path: {self._music_path} (exists: {self._music_path.exists()})")

        # Check for background video in broadcast mode
        if self._broadcast_mode:
            if self._custom_bg_video.exists():
                logger.info(f"Using custom background video: {self._custom_bg_video}")
            elif not self._generated_bg_video.exists():
                logger.info("No custom video found, generating animated background...")
                await self._generate_background()
            else:
                logger.info(f"Using generated background: {self._generated_bg_video}")

        while not self._stop_event.is_set():
            try:
                if self._broadcast_mode:
                    await self._run_broadcast_stream()
                else:
                    await self._run_continuous_stream()

                if not self._stop_event.is_set():
                    self._consecutive_failures += 1
                    if self._consecutive_failures >= self._max_retries:
                        logger.error(f"Stream failed {self._max_retries} times, giving up")
                        self.state = StreamState.ERROR
                        break

                    logger.warning(f"Stream ended, restarting in {self._retry_delay}s... ({self._consecutive_failures}/{self._max_retries})")
                    await asyncio.sleep(self._retry_delay)

            except Exception as e:
                logger.error(f"Stream error: {e}")
                self._consecutive_failures += 1

                if self._stop_event.is_set():
                    break

                if self._consecutive_failures >= self._max_retries:
                    self.state = StreamState.ERROR
                    break

                await asyncio.sleep(self._retry_delay)

        self._cleanup()

    async def stop(self):
        """Stop the streaming."""
        logger.info("Stopping stream...")
        self.state = StreamState.STOPPING
        self._stop_event.set()

        if self._slideshow_task:
            self._slideshow_task.cancel()

        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()

        self.state = StreamState.STOPPED
        self.current_item_id = None
        self.current_item_title = None

    async def _generate_background(self):
        """Generate the animated background video."""
        from ..video.broadcast_frame import generate_background_video
        await asyncio.get_event_loop().run_in_executor(
            None, lambda: generate_background_video(duration_seconds=30, fps=15)
        )

    async def _get_items(self) -> List[NewsItem]:
        """Get approved items for current channel."""
        if self._channel_id:
            return await get_news_items_by_status(NewsStatus.APPROVED, limit=25, channel_id=self._channel_id)
        return await get_news_items_by_status(NewsStatus.APPROVED, limit=25)

    # ==================== SIMPLE MODE ====================

    async def _run_continuous_stream(self):
        """Simple mode: cycle through static images."""
        self.state = StreamState.RUNNING
        logger.info("Starting simple image streaming...")

        transition_frame = self._generate_transition_frame()

        while not self._stop_event.is_set():
            items = await self._get_items()

            if not items:
                logger.info("No approved items, waiting...")
                await asyncio.sleep(10)
                continue

            for item in items:
                if self._stop_event.is_set():
                    break

                # Always regenerate frame for current date
                frame_path = generate_frame(item)
                await update_news_item(item.id, NewsItemUpdate(frame_path=str(frame_path)))
                item.frame_path = str(frame_path)

                self.current_item_id = item.id
                self.current_item_title = item.title[:50]

                logger.info(f"Streaming: {item.title[:40]}...")

                success = await self._stream_single_image(item.frame_path)

                if not success and not self._stop_event.is_set():
                    logger.error(f"Failed to stream item {item.id}, retrying...")
                    await asyncio.sleep(5)
                    continue

                # Transition
                if not self._stop_event.is_set():
                    await self._stream_transition(str(transition_frame))

    async def _stream_single_image(self, frame_path: str) -> bool:
        """Stream a single image for the configured duration."""
        has_music = self._music_path.exists()

        if has_music:
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1", "-i", frame_path,
                "-stream_loop", "-1", "-i", str(self._music_path),
                "-t", str(self._display_seconds),
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
                "-pix_fmt", "yuv420p", "-r", "30", "-g", "60",
                "-b:v", "4500k", "-maxrate", "4500k", "-bufsize", "9000k",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                "-shortest", "-f", "flv", self.rtmp_full_url,
            ]
        else:
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1", "-i", frame_path,
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", str(self._display_seconds),
                "-c:v", "libx264", "-preset", "ultrafast", "-tune", "stillimage",
                "-pix_fmt", "yuv420p", "-r", "30", "-g", "60",
                "-b:v", "4500k", "-maxrate", "4500k", "-bufsize", "9000k",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                "-shortest", "-f", "flv", self.rtmp_full_url,
            ]

        return await self._run_ffmpeg(cmd)

    # ==================== BROADCAST MODE ====================

    async def _run_broadcast_stream(self):
        """Broadcast mode: animated background, overlay, scrolling ticker."""
        from ..video.broadcast_frame import generate_broadcast_overlay, get_ticker_text

        self.state = StreamState.RUNNING
        logger.info("Starting broadcast stream with animations...")

        while not self._stop_event.is_set():
            items = await self._get_items()

            if not items:
                logger.info("No approved items, waiting...")
                await asyncio.sleep(10)
                continue

            for i, item in enumerate(items):
                if self._stop_event.is_set():
                    break

                self.current_item_id = item.id
                self.current_item_title = item.title[:50]

                # Generate overlay image
                logger.info(f"Generating overlay for: {item.title[:40]}...")
                overlay_path = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: generate_broadcast_overlay(item, items)
                )

                # Get ticker text
                ticker_text = get_ticker_text(items, i)

                # Stream with compositing
                logger.info(f"Streaming broadcast: {item.title[:40]}...")
                success = await self._stream_broadcast_segment(overlay_path, ticker_text)

                if not success and not self._stop_event.is_set():
                    await asyncio.sleep(5)
                    continue

                self._consecutive_failures = 0

    async def _stream_broadcast_segment(self, overlay_path: Path, ticker_text: str) -> bool:
        """Stream a broadcast segment with animated background and scrolling ticker."""
        has_music = self._music_path.exists()
        has_bg = self._background_video.exists()

        logger.info(f"Broadcast segment: bg={has_bg} ({self._background_video}), music={has_music} ({self._music_path})")

        # Escape special characters for FFmpeg drawtext
        safe_ticker = ticker_text.replace("'", "'\\''").replace(":", "\\:")

        # Calculate scroll speed (pixels per second)
        # Text should scroll across screen in about 20 seconds
        scroll_speed = 80  # pixels per second

        # FFmpeg filter for scrolling ticker
        ticker_filter = (
            f"drawtext=text='{safe_ticker}  ★  {safe_ticker}':"
            f"fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf:"
            f"fontsize=28:fontcolor=white:"
            f"x='w-mod(t*{scroll_speed}\\,w+tw)':"  # Scroll from right to left
            f"y=h-45"  # Position in ticker bar
        )

        if has_bg:
            # Full broadcast mode with animated background
            # Video filter: overlay PNG on looping background, add ticker
            filter_complex = (
                f"[0:v][1:v]overlay=0:0[main];"  # Overlay news on background
                f"[main]{ticker_filter}[out]"  # Add scrolling ticker
            )

            if has_music:
                cmd = [
                    "ffmpeg", "-y",
                    "-stream_loop", "-1", "-re", "-i", str(self._background_video),
                    "-loop", "1", "-i", str(overlay_path),
                    "-stream_loop", "-1", "-i", str(self._music_path),
                    "-t", str(self._display_seconds),
                    "-filter_complex", filter_complex,
                    "-map", "[out]", "-map", "2:a",
                    "-c:v", "libx264", "-preset", "veryfast",
                    "-b:v", "4500k", "-maxrate", "4500k", "-bufsize", "9000k",
                    "-pix_fmt", "yuv420p", "-r", "30", "-g", "60",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                    "-f", "flv", self.rtmp_full_url,
                ]
                logger.info("Using background video + overlay + music")
            else:
                cmd = [
                    "ffmpeg", "-y",
                    "-stream_loop", "-1", "-re", "-i", str(self._background_video),
                    "-loop", "1", "-i", str(overlay_path),
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-t", str(self._display_seconds),
                    "-filter_complex", filter_complex,
                    "-map", "[out]", "-map", "2:a",
                    "-c:v", "libx264", "-preset", "veryfast",
                    "-b:v", "4500k", "-maxrate", "4500k", "-bufsize", "9000k",
                    "-pix_fmt", "yuv420p", "-r", "30", "-g", "60",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                    "-f", "flv", self.rtmp_full_url,
                ]
                logger.info("Using background video + overlay (no music)")
        else:
            # Fallback: use overlay image with ticker animation only
            filter_complex = f"[0:v]{ticker_filter}[out]"

            if has_music:
                cmd = [
                    "ffmpeg", "-y",
                    "-loop", "1", "-i", str(overlay_path),
                    "-stream_loop", "-1", "-i", str(self._music_path),
                    "-t", str(self._display_seconds),
                    "-filter_complex", filter_complex,
                    "-map", "[out]", "-map", "1:a",
                    "-c:v", "libx264", "-preset", "veryfast",
                    "-b:v", "4500k", "-maxrate", "4500k", "-bufsize", "9000k",
                    "-pix_fmt", "yuv420p", "-r", "30", "-g", "60",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                    "-f", "flv", self.rtmp_full_url,
                ]
                logger.info("Using overlay + ticker + music (no bg video)")
            else:
                cmd = [
                    "ffmpeg", "-y",
                    "-loop", "1", "-i", str(overlay_path),
                    "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                    "-t", str(self._display_seconds),
                    "-filter_complex", filter_complex,
                    "-map", "[out]", "-map", "1:a",
                    "-c:v", "libx264", "-preset", "veryfast",
                    "-b:v", "4500k", "-maxrate", "4500k", "-bufsize", "9000k",
                    "-pix_fmt", "yuv420p", "-r", "30", "-g", "60",
                    "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                    "-shortest", "-f", "flv", self.rtmp_full_url,
                ]

        return await self._run_ffmpeg(cmd)

    # ==================== COMMON METHODS ====================

    async def _run_ffmpeg(self, cmd: List[str]) -> bool:
        """Run FFmpeg command and wait for completion."""
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            while proc.returncode is None:
                if self._stop_event.is_set():
                    proc.terminate()
                    await proc.wait()
                    return False

                try:
                    await asyncio.wait_for(proc.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue

            if proc.returncode != 0:
                stderr = await proc.stderr.read()
                logger.error(f"FFmpeg error: {stderr.decode()[-500:]}")
                return False

            self._consecutive_failures = 0
            return True

        except Exception as e:
            logger.error(f"FFmpeg error: {e}")
            return False

    def _generate_transition_frame(self) -> Path:
        """Generate a transition frame."""
        from PIL import Image, ImageDraw, ImageFont
        import math

        width = settings.frame_width
        height = settings.frame_height

        img = Image.new('RGB', (width, height), '#050508')
        draw = ImageDraw.Draw(img)

        # Grid pattern
        for x in range(0, width, 40):
            draw.line([(x, 0), (x, height)], fill='#0a0a12', width=1)
        for y in range(0, height, 40):
            draw.line([(0, y), (width, y)], fill='#0a0a12', width=1)

        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 48)
            small_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 24)
        except:
            font = ImageFont.load_default()
            small_font = font

        title = "CYBERSEC NEWS"
        bbox = draw.textbbox((0, 0), title, font=font)
        x = (width - (bbox[2] - bbox[0])) // 2
        draw.text((x, height // 2 - 40), title, font=font, fill='#00ff88')

        subtitle = "• • •"
        bbox = draw.textbbox((0, 0), subtitle, font=small_font)
        x = (width - (bbox[2] - bbox[0])) // 2
        draw.text((x, height // 2 + 30), subtitle, font=small_font, fill='#00aaff')

        # Corner decorations
        c = '#00ff88'
        draw.line([(50, 50), (100, 50)], fill=c, width=2)
        draw.line([(50, 50), (50, 100)], fill=c, width=2)
        draw.line([(width-100, 50), (width-50, 50)], fill=c, width=2)
        draw.line([(width-50, 50), (width-50, 100)], fill=c, width=2)
        draw.line([(50, height-50), (100, height-50)], fill=c, width=2)
        draw.line([(50, height-100), (50, height-50)], fill=c, width=2)
        draw.line([(width-100, height-50), (width-50, height-50)], fill=c, width=2)
        draw.line([(width-50, height-100), (width-50, height-50)], fill=c, width=2)

        path = self._data_dir / "transition.png"
        img.save(path, 'PNG')
        return path

    async def _stream_transition(self, frame_path: str) -> bool:
        """Stream a transition frame."""
        has_music = self._music_path.exists()

        if has_music:
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1", "-i", frame_path,
                "-stream_loop", "-1", "-i", str(self._music_path),
                "-t", "3",  # 3 second transition
                "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-r", "30", "-g", "60",
                "-b:v", "4500k", "-maxrate", "4500k", "-bufsize", "9000k",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                "-shortest", "-f", "flv", self.rtmp_full_url,
            ]
        else:
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1", "-i", frame_path,
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-t", "3",
                "-c:v", "libx264", "-preset", "ultrafast",
                "-pix_fmt", "yuv420p", "-r", "30", "-g", "60",
                "-b:v", "4500k", "-maxrate", "4500k", "-bufsize", "9000k",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
                "-shortest", "-f", "flv", self.rtmp_full_url,
            ]

        return await self._run_ffmpeg(cmd)

    def _cleanup(self):
        """Clean up resources."""
        if self.process:
            try:
                self.process.terminate()
            except:
                pass
            self.process = None

    def get_status(self) -> dict:
        """Get current stream status."""
        return {
            "state": self.state.value,
            "is_running": self.is_running,
            "current_item_id": self.current_item_id,
            "current_item_title": self.current_item_title,
            "broadcast_mode": self._broadcast_mode,
        }


# Global stream manager instance
stream_manager = YouTubeStreamer()
