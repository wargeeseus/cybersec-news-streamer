"""
Background worker for fetching and summarizing news.
Auto-approves all news and generates frames for streaming.
"""
import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .config import settings
from .db.database import init_db, create_news_item, update_news_item
from .db.models import NewsItemCreate, NewsItemUpdate
from .news.fetcher import fetch_news
from .ai.summarizer import summarize_news, check_ollama_health
from .video.frame_generator import generate_frame

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def process_news():
    """Fetch news, summarize with LLM, and auto-generate frames."""
    logger.info("Starting news fetch cycle...")

    # Check Ollama health
    ollama_healthy = await check_ollama_health()
    if not ollama_healthy:
        logger.warning("Ollama is not available, will use fallback summaries")

    # Fetch new items
    items = await fetch_news()
    logger.info(f"Fetched {len(items)} new items")

    # Process each item
    for item in items:
        try:
            # Summarize with LLM
            result = await summarize_news(item["title"], item["description"])

            if result:
                # Create news item (auto-approved by default)
                news_item = NewsItemCreate(
                    title=result["headline"],
                    original_title=item["title"],
                    summary=result["summary"],
                    source_name=item["source_name"],
                    source_url=item["url"],
                )

                created = await create_news_item(news_item)
                if created:
                    logger.info(f"Created news item: {created.title[:50]}...")

                    # Auto-generate frame for streaming
                    try:
                        frame_path = generate_frame(created)
                        await update_news_item(created.id, NewsItemUpdate(frame_path=str(frame_path)))
                        logger.info(f"Generated frame for item {created.id}")
                    except Exception as e:
                        logger.error(f"Error generating frame: {e}")
                else:
                    logger.debug(f"Item already exists: {item['url']}")

        except Exception as e:
            logger.error(f"Error processing item: {e}")
            continue

    logger.info("News fetch cycle complete")


async def main():
    """Main worker loop."""
    logger.info("Starting CyberSec News Worker...")

    # Initialize database
    await init_db()

    # Run once immediately
    await process_news()

    # Set up scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        process_news,
        'interval',
        minutes=settings.news_fetch_interval_minutes,
        id='fetch_news',
        name='Fetch and summarize news',
    )
    scheduler.start()

    logger.info(f"Scheduler started. Will fetch news every {settings.news_fetch_interval_minutes} minutes")

    # Keep running
    try:
        while True:
            await asyncio.sleep(60)
    except KeyboardInterrupt:
        logger.info("Shutting down worker...")
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
