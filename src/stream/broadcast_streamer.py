"""
Broadcast-style YouTube Streamer

Creates professional news channel style streams with:
- Animated backgrounds
- News overlays
- Scrolling ticker
- Smooth transitions
- Background music
"""

import subprocess
import asyncio
import logging
import os
from pathlib import Path
from typing import Optional, List
from enum import Enum

from ..config import settings
from ..db.database import get_news_items_by_status, update_news_item
from ..db.models import NewsStatus, NewsItemUpdate, NewsItem
from ..video.news_video_generator import generate_news_segment, generate_transition

logger = logging.getLogger(__name__)


class StreamState(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    GENERATING = "generating"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class BroadcastStreamer:
    """Professional broadcast-style streamer with video segments."""

    def __init__(self):
        self.process: Optional[subprocess.Popen] = None
        self.state = StreamState.STOPPED
        self.current_item_id: Optional[int] = None
        self.current_item_title: Optional[str] = None
        self._stop_event = asyncio.Event()

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
        self._segments_dir = self._data_dir / "segments"
        self._music_path = self._data_dir.parent / "assets" / "music" / "background.mp3"

    def update_config(self, stream_key: str = None, rtmp_url: str = None,
                      display_seconds: int = None, channel_id: int = None):
        """Update stream configuration."""
        if stream_key is not None:
            self._stream_key = stream_key
        if rtmp_url is not None:
            self._rtmp_url = rtmp_url
        if display_seconds is not None:
            self._display_seconds = display_seconds
        if channel_id is not None:
            self._channel_id = channel_id
        logger.info(f"Broadcast config updated: key={'*' * 8 if self._stream_key else 'not set'}")

    @property
    def rtmp_full_url(self) -> str:
        return f"{self._rtmp_url}/{self._stream_key}"

    @property
    def is_running(self) -> bool:
        return self.state in [StreamState.RUNNING, StreamState.GENERATING]

    async def start(self):
        """Start the broadcast stream."""
        if self.state == StreamState.RUNNING:
            logger.warning("Stream already running")
            return

        if not self._stream_key:
            logger.error("No stream key configured")
            self.state = StreamState.ERROR
            return

        self.state = StreamState.STARTING
        self._stop_event.clear()
        self._segments_dir.mkdir(parents=True, exist_ok=True)
        self._consecutive_failures = 0

        logger.info("Starting broadcast stream...")

        while not self._stop_event.is_set():
            try:
                await self._run_broadcast_loop()

                if not self._stop_event.is_set():
                    self._consecutive_failures += 1
                    if self._consecutive_failures >= self._max_retries:
                        logger.error(f"Stream failed {self._max_retries} times")
                        self.state = StreamState.ERROR
                        break

                    logger.warning(f"Restarting in {self._retry_delay}s... ({self._consecutive_failures}/{self._max_retries})")
                    await asyncio.sleep(self._retry_delay)

            except Exception as e:
                logger.error(f"Broadcast error: {e}")
                self._consecutive_failures += 1

                if self._stop_event.is_set():
                    break

                if self._consecutive_failures >= self._max_retries:
                    self.state = StreamState.ERROR
                    break

                await asyncio.sleep(self._retry_delay)

        self._cleanup()

    async def stop(self):
        """Stop the stream."""
        logger.info("Stopping broadcast stream...")
        self.state = StreamState.STOPPING
        self._stop_event.set()

        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()

        self.state = StreamState.STOPPED
        self.current_item_id = None
        self.current_item_title = None

    async def _run_broadcast_loop(self):
        """Main broadcast loop - generate and stream video segments."""
        self.state = StreamState.RUNNING

        # Generate transition clip once
        logger.info("Generating transition clip...")
        transition_path = generate_transition(duration_ms=800, fps=30)

        while not self._stop_event.is_set():
            # Get approved items
            if self._channel_id:
                from ..db.database import get_news_items_by_status
                items = await get_news_items_by_status(
                    NewsStatus.APPROVED, limit=25, channel_id=self._channel_id
                )
            else:
                items = await get_news_items_by_status(NewsStatus.APPROVED, limit=25)

            if not items:
                logger.info("No approved items, waiting...")
                self.current_item_title = "Waiting for news..."
                await asyncio.sleep(10)
                continue

            # Stream each item
            for i, item in enumerate(items):
                if self._stop_event.is_set():
                    break

                self.state = StreamState.GENERATING
                self.current_item_id = item.id
                self.current_item_title = f"Preparing: {item.title[:40]}..."

                # Get next items for ticker
                next_items = items[i+1:] + items[:i]

                # Generate video segment
                logger.info(f"Generating segment for: {item.title[:50]}...")
                try:
                    segment_path = await asyncio.get_event_loop().run_in_executor(
                        None,
                        lambda: generate_news_segment(
                            item, next_items,
                            duration_seconds=self._display_seconds,
                            fps=30
                        )
                    )
                except Exception as e:
                    logger.error(f"Failed to generate segment: {e}")
                    continue

                self.state = StreamState.RUNNING
                self.current_item_title = item.title[:50]

                # Stream the segment
                logger.info(f"Streaming: {item.title[:40]}...")
                success = await self._stream_segment(segment_path)

                if not success and not self._stop_event.is_set():
                    logger.error(f"Failed to stream segment {item.id}")
                    await asyncio.sleep(5)
                    continue

                # Stream transition
                if not self._stop_event.is_set() and i < len(items) - 1:
                    logger.info("Streaming transition...")
                    await self._stream_segment(transition_path, is_transition=True)

                # Reset failure counter on success
                self._consecutive_failures = 0

    async def _stream_segment(self, video_path: Path, is_transition: bool = False) -> bool:
        """Stream a video segment to YouTube."""
        has_music = self._music_path.exists() and not is_transition

        if has_music:
            cmd = [
                "ffmpeg", "-y",
                "-re",  # Real-time streaming
                "-i", str(video_path),
                "-stream_loop", "-1",
                "-i", str(self._music_path),
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-b:v", "4500k",
                "-maxrate", "4500k",
                "-bufsize", "9000k",
                "-pix_fmt", "yuv420p",
                "-g", "60",
                "-c:a", "aac",
                "-b:a", "128k",
                "-ar", "44100",
                "-map", "0:v",
                "-map", "1:a",
                "-shortest",
                "-f", "flv",
                self.rtmp_full_url,
            ]
        else:
            cmd = [
                "ffmpeg", "-y",
                "-re",
                "-i", str(video_path),
                "-f", "lavfi",
                "-i", "anullsrc=r=44100:cl=stereo",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-b:v", "4500k",
                "-maxrate", "4500k",
                "-bufsize", "9000k",
                "-pix_fmt", "yuv420p",
                "-g", "60",
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

            return True

        except Exception as e:
            logger.error(f"Stream error: {e}")
            return False

    def _cleanup(self):
        """Cleanup resources."""
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass
            self.process = None

        # Cleanup old segments
        try:
            for f in self._segments_dir.glob("segment_*.mp4"):
                f.unlink()
        except Exception:
            pass

    def get_status(self) -> dict:
        """Get stream status."""
        return {
            "state": self.state.value,
            "is_running": self.is_running,
            "current_item_id": self.current_item_id,
            "current_item_title": self.current_item_title,
        }
