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
    """Manages FFmpeg streaming to YouTube Live with continuous slideshow."""

    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        self.state = StreamState.STOPPED
        self.current_item_id: Optional[int] = None
        self.current_item_title: Optional[str] = None
        self._stop_event = asyncio.Event()
        self._slideshow_task: Optional[asyncio.Task] = None

        # Runtime config (can be updated from web UI)
        self._stream_key: str = settings.youtube_stream_key
        self._rtmp_url: str = "rtmp://a.rtmp.youtube.com/live2"
        self._display_seconds: int = settings.news_display_seconds

        # Auto-restart settings
        self._max_retries: int = 10
        self._retry_delay: int = 5  # seconds
        self._consecutive_failures: int = 0

        # Paths
        self._data_dir = Path(settings.database_path).parent
        self._frames_dir = self._data_dir / "frames"
        self._current_frame = self._data_dir / "current_frame.png"
        self._playlist_file = self._data_dir / "playlist.txt"

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

    def update_config(self, stream_key: str = None, rtmp_url: str = None, display_seconds: int = None):
        """Update stream configuration at runtime."""
        if stream_key is not None:
            self._stream_key = stream_key
        if rtmp_url is not None:
            self._rtmp_url = rtmp_url
        if display_seconds is not None:
            self._display_seconds = display_seconds
        logger.info(f"Stream config updated: key={'*' * 8 if self._stream_key else 'not set'}")

    @property
    def stream_key(self) -> str:
        return self._stream_key

    @property
    def rtmp_full_url(self) -> str:
        return f"{self._rtmp_url}/{self._stream_key}"

    @property
    def is_running(self) -> bool:
        return self.state == StreamState.RUNNING

    async def start(self):
        """Start the continuous slideshow stream with auto-restart."""
        if self.state == StreamState.RUNNING:
            logger.warning("Stream already running")
            return

        # Load config from DB
        await self.load_config_from_db()

        if not self._stream_key:
            logger.error("No YouTube stream key configured")
            self.state = StreamState.ERROR
            return

        self.state = StreamState.STARTING
        self._stop_event.clear()
        self._frames_dir.mkdir(parents=True, exist_ok=True)
        self._consecutive_failures = 0

        logger.info("Starting YouTube stream with auto-restart enabled...")

        while not self._stop_event.is_set():
            try:
                # Generate initial frames
                await self._prepare_frames()

                # Start the continuous stream
                await self._run_continuous_stream()

                # If we get here normally (not stopped), it means stream ended
                if not self._stop_event.is_set():
                    self._consecutive_failures += 1
                    if self._consecutive_failures >= self._max_retries:
                        logger.error(f"Stream failed {self._max_retries} times, giving up")
                        self.state = StreamState.ERROR
                        break

                    logger.warning(f"Stream ended unexpectedly, restarting in {self._retry_delay}s... (attempt {self._consecutive_failures}/{self._max_retries})")
                    await asyncio.sleep(self._retry_delay)

            except Exception as e:
                logger.error(f"Stream error: {e}")
                self._consecutive_failures += 1

                if self._stop_event.is_set():
                    break

                if self._consecutive_failures >= self._max_retries:
                    logger.error(f"Stream failed {self._max_retries} times, giving up")
                    self.state = StreamState.ERROR
                    break

                logger.info(f"Restarting stream in {self._retry_delay}s... (attempt {self._consecutive_failures}/{self._max_retries})")
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

    async def _prepare_frames(self) -> List[Path]:
        """Generate frames for all approved items."""
        items = await get_news_items_by_status(NewsStatus.APPROVED, limit=100)
        frame_paths = []

        for item in items:
            if not item.frame_path or not Path(item.frame_path).exists():
                frame_path = generate_frame(item)
                await update_news_item(item.id, NewsItemUpdate(frame_path=str(frame_path)))
            else:
                frame_path = Path(item.frame_path)
            frame_paths.append(frame_path)

        logger.info(f"Prepared {len(frame_paths)} frames")
        return frame_paths

    async def _get_approved_items(self) -> List[NewsItem]:
        """Get all approved items."""
        return await get_news_items_by_status(NewsStatus.APPROVED, limit=100)

    async def _run_continuous_stream(self):
        """Run a continuous stream by cycling through images one at a time."""
        self.state = StreamState.RUNNING

        logger.info("Starting direct image streaming (no MP4 creation)...")

        while not self._stop_event.is_set():
            items = await self._get_approved_items()

            if not items:
                logger.info("No approved items, waiting...")
                await asyncio.sleep(10)
                continue

            # Stream each image one at a time
            for item in items:
                if self._stop_event.is_set():
                    break

                # Generate frame if needed
                if not item.frame_path or not Path(item.frame_path).exists():
                    frame_path = generate_frame(item)
                    await update_news_item(item.id, NewsItemUpdate(frame_path=str(frame_path)))
                    item.frame_path = str(frame_path)

                self.current_item_id = item.id
                self.current_item_title = item.title[:50]

                logger.info(f"Streaming: {item.title[:40]}...")

                # Stream this single image
                success = await self._stream_single_image(item.frame_path)

                if not success and not self._stop_event.is_set():
                    logger.error(f"Failed to stream item {item.id}, retrying in 5s...")
                    await asyncio.sleep(5)

    async def _stream_single_image(self, frame_path: str) -> bool:
        """Stream a single image for the configured duration."""
        # Check for background music
        music_path = self._data_dir.parent / "assets" / "music" / "background.mp3"
        has_music = music_path.exists()

        if has_music:
            cmd = [
                "ffmpeg",
                "-y",
                "-loop", "1",
                "-i", frame_path,
                "-stream_loop", "-1",
                "-i", str(music_path),
                "-t", str(self._display_seconds),
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "stillimage",
                "-pix_fmt", "yuv420p",
                "-r", "30",
                "-g", "60",
                "-b:v", "4500k",
                "-maxrate", "4500k",
                "-bufsize", "9000k",
                "-c:a", "aac",
                "-b:a", "128k",
                "-ar", "44100",
                "-shortest",
                "-f", "flv",
                self.rtmp_full_url,
            ]
        else:
            cmd = [
                "ffmpeg",
                "-y",
                "-loop", "1",
                "-i", frame_path,
                "-f", "lavfi",
                "-i", "anullsrc=r=44100:cl=stereo",
                "-t", str(self._display_seconds),
                "-c:v", "libx264",
                "-preset", "ultrafast",
                "-tune", "stillimage",
                "-pix_fmt", "yuv420p",
                "-r", "30",
                "-g", "60",
                "-b:v", "4500k",
                "-maxrate", "4500k",
                "-bufsize", "9000k",
                "-c:a", "aac",
                "-b:a", "128k",
                "-ar", "44100",
                "-shortest",
                "-f", "flv",
                self.rtmp_full_url,
            ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Wait for completion or stop signal
            while proc.returncode is None:
                if self._stop_event.is_set():
                    proc.terminate()
                    await proc.wait()
                    return False

                # Check every 0.5 seconds
                try:
                    await asyncio.wait_for(proc.wait(), timeout=0.5)
                except asyncio.TimeoutError:
                    continue

            if proc.returncode != 0:
                stderr = await proc.stderr.read()
                error_msg = stderr.decode()[-500:]
                logger.error(f"FFmpeg error streaming image: {error_msg}")

                # Check for specific errors
                if "Connection refused" in error_msg or "Connection timed out" in error_msg:
                    logger.error("RTMP connection failed - will retry")
                return False

            # Success - reset failure counter
            self._consecutive_failures = 0
            return True

        except Exception as e:
            logger.error(f"Error streaming image: {e}")
            return False

    async def _create_playlist(self, items: List[NewsItem]):
        """Create FFmpeg concat playlist file."""
        lines = ["ffconcat version 1.0"]

        for item in items:
            if item.frame_path and Path(item.frame_path).exists():
                # Escape single quotes in path
                safe_path = str(item.frame_path).replace("'", "'\\''")
                lines.append(f"file '{safe_path}'")
                lines.append(f"duration {self._display_seconds}")

        # Add last file again (required for concat to work properly)
        if items and items[-1].frame_path:
            safe_path = str(items[-1].frame_path).replace("'", "'\\''")
            lines.append(f"file '{safe_path}'")

        self._playlist_file.write_text("\n".join(lines))
        logger.info(f"Created playlist with {len(items)} items")

    async def _stream_slideshow(self, has_audio: bool, audio_path: Path):
        """Stream slideshow by creating video first, then streaming in loop."""

        # First, create a video file from the slideshow
        video_file = self._data_dir / "slideshow.mp4"

        logger.info("Creating slideshow video...")

        # Create video from concat playlist
        create_cmd = [
            "ffmpeg",
            "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(self._playlist_file),
            "-c:v", "libx264",
            "-preset", "fast",
            "-tune", "stillimage",
            "-pix_fmt", "yuv420p",
            "-r", "30",
            "-g", "60",
            str(video_file),
        ]

        proc = subprocess.run(create_cmd, capture_output=True, timeout=300)
        if proc.returncode != 0:
            logger.error(f"Failed to create slideshow video: {proc.stderr.decode()[-500:]}")
            return

        logger.info(f"Slideshow video created: {video_file}")

        # Now stream the video in a loop
        if has_audio:
            cmd = [
                "ffmpeg",
                "-y",
                "-re",
                "-stream_loop", "-1",
                "-i", str(video_file),
                "-stream_loop", "-1",
                "-i", str(audio_path),
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-pix_fmt", "yuv420p",
                "-r", "30",
                "-g", "60",
                "-b:v", "4500k",
                "-maxrate", "4500k",
                "-bufsize", "9000k",
                "-map", "0:v",
                "-map", "1:a",
                "-c:a", "aac",
                "-b:a", "128k",
                "-ar", "44100",
                "-f", "flv",
                self.rtmp_full_url,
            ]
        else:
            cmd = [
                "ffmpeg",
                "-y",
                "-re",
                "-stream_loop", "-1",
                "-i", str(video_file),
                "-f", "lavfi",
                "-i", "anullsrc=r=44100:cl=stereo",
                "-c:v", "copy",
                "-map", "0:v",
                "-map", "1:a",
                "-c:a", "aac",
                "-b:a", "128k",
                "-f", "flv",
                self.rtmp_full_url,
            ]

        logger.info("Starting FFmpeg slideshow stream...")
        logger.info(f"Streaming to: {self._rtmp_url}/****{self._stream_key[-4:] if self._stream_key else 'NONE'}")
        logger.debug(f"FFmpeg command: {' '.join(cmd)}")

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            # Track current item for status display
            items = await self._get_approved_items()
            item_index = 0
            last_switch = asyncio.get_event_loop().time()

            while self.process.poll() is None:
                if self._stop_event.is_set():
                    self.process.terminate()
                    return

                # Update current item display
                if items:
                    current_time = asyncio.get_event_loop().time()
                    if current_time - last_switch >= self._display_seconds:
                        item_index = (item_index + 1) % len(items)
                        last_switch = current_time

                    current_item = items[item_index]
                    self.current_item_id = current_item.id
                    self.current_item_title = current_item.title[:50]

                await asyncio.sleep(1)

            # Check if FFmpeg exited with error
            if self.process.returncode != 0:
                stderr = self.process.stderr.read().decode() if self.process.stderr else ""
                # Log the full error for debugging
                logger.error(f"FFmpeg exited with code {self.process.returncode}")
                # Look for specific RTMP errors
                if "Connection refused" in stderr:
                    logger.error("RTMP Connection refused - check stream key and URL")
                elif "Server returned 403" in stderr:
                    logger.error("RTMP 403 Forbidden - stream key may be invalid")
                elif "Connection timed out" in stderr:
                    logger.error("RTMP Connection timed out - check network")
                elif "rtmp://" in stderr.lower():
                    # Find and log the RTMP-related error
                    for line in stderr.split('\n'):
                        if 'rtmp' in line.lower() or 'error' in line.lower():
                            logger.error(f"RTMP: {line.strip()}")
                else:
                    # Log last part of error
                    logger.error(f"FFmpeg stderr (last 1000 chars): {stderr[-1000:]}")

                # Wait before retry
                await asyncio.sleep(5)

        except Exception as e:
            logger.error(f"FFmpeg error: {e}")
            await asyncio.sleep(5)

    async def _stream_waiting_screen(self):
        """Stream a waiting screen when no items are approved."""
        # Create a simple waiting frame
        from PIL import Image, ImageDraw, ImageFont

        img = Image.new('RGB', (settings.frame_width, settings.frame_height), '#0a0a0f')
        draw = ImageDraw.Draw(img)

        # Draw waiting message
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", 48)
        except:
            font = ImageFont.load_default()

        text = "Waiting for approved news..."
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        x = (settings.frame_width - text_width) // 2
        y = settings.frame_height // 2

        draw.text((x, y), text, font=font, fill='#00ff88')

        waiting_frame = self._data_dir / "waiting.png"
        img.save(waiting_frame)

        self.current_item_id = None
        self.current_item_title = "Waiting for content..."

    def _cleanup(self):
        """Clean up resources."""
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass
            self.process = None

    def get_status(self) -> dict:
        """Get current stream status."""
        return {
            "state": self.state.value,
            "is_running": self.is_running,
            "current_item_id": self.current_item_id,
            "current_item_title": self.current_item_title,
        }


# Global stream manager instance
stream_manager = YouTubeStreamer()
