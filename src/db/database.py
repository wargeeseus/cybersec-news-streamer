import aiosqlite
from pathlib import Path
from typing import Optional, List
from datetime import datetime

from ..config import settings
from .models import NewsItem, NewsStatus, NewsItemCreate, NewsItemUpdate


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    original_title TEXT NOT NULL,
    summary TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL UNIQUE,
    status TEXT DEFAULT 'approved',
    frame_path TEXT,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    streamed_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_status ON news_items(status);
CREATE INDEX IF NOT EXISTS idx_fetched_at ON news_items(fetched_at);
"""


async def get_db() -> aiosqlite.Connection:
    """Get database connection."""
    db_path = Path(settings.database_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    db = await aiosqlite.connect(str(db_path))
    db.row_factory = aiosqlite.Row
    return db


async def init_db():
    """Initialize database schema."""
    db = await get_db()
    try:
        await db.executescript(CREATE_TABLE_SQL)
        await db.commit()
    finally:
        await db.close()


async def create_news_item(item: NewsItemCreate) -> Optional[NewsItem]:
    """Create a new news item."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            INSERT OR IGNORE INTO news_items (title, original_title, summary, source_name, source_url)
            VALUES (?, ?, ?, ?, ?)
            """,
            (item.title, item.original_title, item.summary, item.source_name, item.source_url)
        )
        await db.commit()

        if cursor.rowcount == 0:
            return None  # Duplicate URL

        # Fetch the created item
        cursor = await db.execute(
            "SELECT * FROM news_items WHERE id = ?",
            (cursor.lastrowid,)
        )
        row = await cursor.fetchone()
        return _row_to_news_item(row)
    finally:
        await db.close()


async def get_news_item(item_id: int) -> Optional[NewsItem]:
    """Get a news item by ID."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT * FROM news_items WHERE id = ?",
            (item_id,)
        )
        row = await cursor.fetchone()
        return _row_to_news_item(row) if row else None
    finally:
        await db.close()


async def get_news_items_by_status(status: NewsStatus, limit: int = 50) -> List[NewsItem]:
    """Get news items by status."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT * FROM news_items
            WHERE status = ?
            ORDER BY fetched_at DESC
            LIMIT ?
            """,
            (status.value, limit)
        )
        rows = await cursor.fetchall()
        return [_row_to_news_item(row) for row in rows]
    finally:
        await db.close()


async def get_all_news_items(limit: int = 100) -> List[NewsItem]:
    """Get all news items."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT * FROM news_items
            ORDER BY fetched_at DESC
            LIMIT ?
            """,
            (limit,)
        )
        rows = await cursor.fetchall()
        return [_row_to_news_item(row) for row in rows]
    finally:
        await db.close()


async def update_news_item(item_id: int, update: NewsItemUpdate) -> Optional[NewsItem]:
    """Update a news item."""
    db = await get_db()
    try:
        updates = []
        values = []

        if update.title is not None:
            updates.append("title = ?")
            values.append(update.title)

        if update.summary is not None:
            updates.append("summary = ?")
            values.append(update.summary)

        if update.status is not None:
            updates.append("status = ?")
            values.append(update.status.value if isinstance(update.status, NewsStatus) else update.status)

            if update.status == NewsStatus.APPROVED:
                updates.append("approved_at = ?")
                values.append(datetime.utcnow().isoformat())
            elif update.status == NewsStatus.STREAMED:
                updates.append("streamed_at = ?")
                values.append(datetime.utcnow().isoformat())

        if update.frame_path is not None:
            updates.append("frame_path = ?")
            values.append(update.frame_path)

        if not updates:
            return await get_news_item(item_id)

        values.append(item_id)
        await db.execute(
            f"UPDATE news_items SET {', '.join(updates)} WHERE id = ?",
            values
        )
        await db.commit()

        return await get_news_item(item_id)
    finally:
        await db.close()


async def get_next_approved_item() -> Optional[NewsItem]:
    """Get the next approved item to stream (FIFO)."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT * FROM news_items
            WHERE status = 'approved'
            ORDER BY approved_at ASC
            LIMIT 1
            """
        )
        row = await cursor.fetchone()
        return _row_to_news_item(row) if row else None
    finally:
        await db.close()


async def get_counts_by_status() -> dict:
    """Get count of items by status."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            SELECT status, COUNT(*) as count
            FROM news_items
            GROUP BY status
            """
        )
        rows = await cursor.fetchall()
        counts = {status.value: 0 for status in NewsStatus}
        for row in rows:
            counts[row['status']] = row['count']
        return counts
    finally:
        await db.close()


async def url_exists(url: str) -> bool:
    """Check if a URL already exists in the database."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT 1 FROM news_items WHERE source_url = ?",
            (url,)
        )
        row = await cursor.fetchone()
        return row is not None
    finally:
        await db.close()


def _row_to_news_item(row) -> NewsItem:
    """Convert a database row to a NewsItem."""
    return NewsItem(
        id=row['id'],
        title=row['title'],
        original_title=row['original_title'],
        summary=row['summary'],
        source_name=row['source_name'],
        source_url=row['source_url'],
        status=NewsStatus(row['status']),
        frame_path=row['frame_path'],
        fetched_at=datetime.fromisoformat(row['fetched_at']) if row['fetched_at'] else datetime.utcnow(),
        approved_at=datetime.fromisoformat(row['approved_at']) if row['approved_at'] else None,
        streamed_at=datetime.fromisoformat(row['streamed_at']) if row['streamed_at'] else None,
    )


# --- Settings Functions ---

async def get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    """Get a setting value by key."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT value FROM settings WHERE key = ?",
            (key,)
        )
        row = await cursor.fetchone()
        return row['value'] if row else default
    finally:
        await db.close()


async def set_setting(key: str, value: str) -> None:
    """Set a setting value."""
    db = await get_db()
    try:
        await db.execute(
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = ?
            """,
            (key, value, datetime.utcnow().isoformat(),
             value, datetime.utcnow().isoformat())
        )
        await db.commit()
    finally:
        await db.close()


async def get_all_settings() -> dict:
    """Get all settings as a dictionary."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT key, value FROM settings")
        rows = await cursor.fetchall()
        return {row['key']: row['value'] for row in rows}
    finally:
        await db.close()


async def get_stream_config() -> dict:
    """Get stream configuration settings."""
    return {
        'youtube_stream_key': await get_setting('youtube_stream_key', ''),
        'news_display_seconds': int(await get_setting('news_display_seconds', '30')),
        'rtmp_url': await get_setting('rtmp_url', 'rtmp://a.rtmp.youtube.com/live2'),
        'youtube_video_id': await get_setting('youtube_video_id', ''),
    }
