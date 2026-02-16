"""
Standalone streamer service that monitors for commands.
"""
import asyncio
import logging
import signal
from pathlib import Path

from .config import settings
from .db.database import init_db
from .stream.youtube import stream_manager

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Control file for stream commands
CONTROL_FILE = Path(settings.database_path).parent / "stream_control.txt"


async def check_control_file():
    """Check control file for start/stop commands."""
    if not CONTROL_FILE.exists():
        return None

    try:
        command = CONTROL_FILE.read_text().strip().lower()
        CONTROL_FILE.unlink()  # Remove after reading
        return command
    except Exception:
        return None


async def main():
    """Main streamer loop."""
    logger.info("Starting CyberSec Streamer Service...")

    # Initialize database
    await init_db()

    # Handle shutdown gracefully
    loop = asyncio.get_event_loop()

    def signal_handler():
        logger.info("Received shutdown signal")
        asyncio.create_task(stream_manager.stop())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    logger.info("Streamer ready. Waiting for commands...")
    logger.info(f"Stream key configured: {'Yes' if settings.youtube_stream_key else 'No'}")

    if not settings.youtube_stream_key:
        logger.warning("No YouTube stream key configured. Streamer will wait for configuration.")

    # Main loop - wait for control commands or just stay alive
    try:
        while True:
            command = await check_control_file()
            if command == "start" and not stream_manager.is_running:
                logger.info("Received start command")
                asyncio.create_task(stream_manager.start())
            elif command == "stop" and stream_manager.is_running:
                logger.info("Received stop command")
                await stream_manager.stop()

            await asyncio.sleep(2)
    except KeyboardInterrupt:
        logger.info("Shutting down streamer...")
        await stream_manager.stop()


if __name__ == "__main__":
    asyncio.run(main())
