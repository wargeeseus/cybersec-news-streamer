import feedparser
import httpx
from typing import List, Dict, Any
from datetime import datetime
import logging

from .sources import RSS_FEEDS
from .deduplicator import is_duplicate, mark_seen
from ..db.database import url_exists

logger = logging.getLogger(__name__)


async def fetch_feed(feed_info: Dict[str, str]) -> List[Dict[str, Any]]:
    """Fetch and parse a single RSS feed."""
    items = []

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(feed_info["url"])
            response.raise_for_status()

        feed = feedparser.parse(response.text)

        for entry in feed.entries[:10]:  # Limit to 10 most recent per feed
            url = entry.get("link", "")

            # Skip if no URL or already processed
            if not url:
                continue

            if is_duplicate(url):
                continue

            # Check database for duplicates
            if await url_exists(url):
                mark_seen(url)
                continue

            title = entry.get("title", "Untitled")
            description = entry.get("description", entry.get("summary", ""))

            # Clean up HTML from description
            description = _clean_html(description)

            items.append({
                "title": title,
                "description": description[:1000],  # Limit description length
                "url": url,
                "source_name": feed_info["name"],
                "published": entry.get("published", ""),
            })

            mark_seen(url)

        logger.info(f"Fetched {len(items)} new items from {feed_info['name']}")

    except Exception as e:
        logger.error(f"Error fetching {feed_info['name']}: {e}")

    return items


async def fetch_news() -> List[Dict[str, Any]]:
    """Fetch news from all configured RSS feeds."""
    all_items = []

    for feed_info in RSS_FEEDS:
        items = await fetch_feed(feed_info)
        all_items.extend(items)

    logger.info(f"Total new items fetched: {len(all_items)}")
    return all_items


def _clean_html(text: str) -> str:
    """Remove HTML tags from text."""
    import re
    # Remove HTML tags
    clean = re.sub(r'<[^>]+>', '', text)
    # Remove extra whitespace
    clean = re.sub(r'\s+', ' ', clean).strip()
    # Decode common HTML entities
    clean = clean.replace('&amp;', '&')
    clean = clean.replace('&lt;', '<')
    clean = clean.replace('&gt;', '>')
    clean = clean.replace('&quot;', '"')
    clean = clean.replace('&#39;', "'")
    clean = clean.replace('&nbsp;', ' ')
    return clean
