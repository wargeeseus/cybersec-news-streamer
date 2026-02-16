import aiosqlite
from pathlib import Path
from typing import Optional, List
from datetime import datetime

from ..config import settings
from .models import (
    NewsItem, NewsStatus, NewsItemCreate, NewsItemUpdate,
    Channel, ChannelCreate, ChannelUpdate
)


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    news_topic TEXT NOT NULL,
    stream_key TEXT DEFAULT '',
    rtmp_url TEXT DEFAULT 'rtmp://a.rtmp.youtube.com/live2',
    display_seconds INTEGER DEFAULT 30,
    is_active INTEGER DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS news_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER DEFAULT 1,
    title TEXT NOT NULL,
    original_title TEXT NOT NULL,
    summary TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_url TEXT NOT NULL,
    status TEXT DEFAULT 'approved',
    frame_path TEXT,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    approved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    streamed_at TIMESTAMP,
    UNIQUE(channel_id, source_url)
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_status ON news_items(status);
CREATE INDEX IF NOT EXISTS idx_fetched_at ON news_items(fetched_at);
CREATE INDEX IF NOT EXISTS idx_channel ON news_items(channel_id);
"""

MAX_NEWS_ITEMS_PER_CHANNEL = 25


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

        # Create default channel if none exists
        cursor = await db.execute("SELECT COUNT(*) FROM channels")
        row = await cursor.fetchone()
        if row[0] == 0:
            await db.execute(
                """
                INSERT INTO channels (name, news_topic)
                VALUES ('Cybersecurity', 'cybersecurity,security,hacking,malware,ransomware,vulnerability')
                """
            )
            await db.commit()
    finally:
        await db.close()


# --- Channel Functions ---

async def create_channel(channel: ChannelCreate) -> Channel:
    """Create a new channel."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            INSERT INTO channels (name, news_topic, stream_key, rtmp_url, display_seconds)
            VALUES (?, ?, ?, ?, ?)
            """,
            (channel.name, channel.news_topic, channel.stream_key, channel.rtmp_url, channel.display_seconds)
        )
        await db.commit()

        cursor = await db.execute("SELECT * FROM channels WHERE id = ?", (cursor.lastrowid,))
        row = await cursor.fetchone()
        return _row_to_channel(row)
    finally:
        await db.close()


async def get_channel(channel_id: int) -> Optional[Channel]:
    """Get a channel by ID."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM channels WHERE id = ?", (channel_id,))
        row = await cursor.fetchone()
        return _row_to_channel(row) if row else None
    finally:
        await db.close()


async def get_all_channels() -> List[Channel]:
    """Get all channels."""
    db = await get_db()
    try:
        cursor = await db.execute("SELECT * FROM channels ORDER BY created_at ASC")
        rows = await cursor.fetchall()
        return [_row_to_channel(row) for row in rows]
    finally:
        await db.close()


async def update_channel(channel_id: int, update: ChannelUpdate) -> Optional[Channel]:
    """Update a channel."""
    db = await get_db()
    try:
        updates = []
        values = []

        if update.name is not None:
            updates.append("name = ?")
            values.append(update.name)
        if update.news_topic is not None:
            updates.append("news_topic = ?")
            values.append(update.news_topic)
        if update.stream_key is not None:
            updates.append("stream_key = ?")
            values.append(update.stream_key)
        if update.rtmp_url is not None:
            updates.append("rtmp_url = ?")
            values.append(update.rtmp_url)
        if update.display_seconds is not None:
            updates.append("display_seconds = ?")
            values.append(update.display_seconds)
        if update.is_active is not None:
            updates.append("is_active = ?")
            values.append(1 if update.is_active else 0)

        if not updates:
            return await get_channel(channel_id)

        values.append(channel_id)
        await db.execute(f"UPDATE channels SET {', '.join(updates)} WHERE id = ?", values)
        await db.commit()
        return await get_channel(channel_id)
    finally:
        await db.close()


async def delete_channel(channel_id: int) -> bool:
    """Delete a channel and its news items."""
    db = await get_db()
    try:
        # Delete news items for this channel
        await db.execute("DELETE FROM news_items WHERE channel_id = ?", (channel_id,))
        # Delete channel
        await db.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
        await db.commit()
        return True
    finally:
        await db.close()


def _row_to_channel(row) -> Channel:
    """Convert a database row to a Channel."""
    return Channel(
        id=row['id'],
        name=row['name'],
        news_topic=row['news_topic'],
        stream_key=row['stream_key'] or '',
        rtmp_url=row['rtmp_url'] or 'rtmp://a.rtmp.youtube.com/live2',
        display_seconds=row['display_seconds'] or 30,
        is_active=bool(row['is_active']),
        created_at=datetime.fromisoformat(row['created_at']) if row['created_at'] else datetime.utcnow(),
    )


# --- News Item Functions ---

async def create_news_item(item: NewsItemCreate) -> Optional[NewsItem]:
    """Create a new news item."""
    db = await get_db()
    try:
        cursor = await db.execute(
            """
            INSERT OR IGNORE INTO news_items (channel_id, title, original_title, summary, source_name, source_url)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (item.channel_id, item.title, item.original_title, item.summary, item.source_name, item.source_url)
        )
        await db.commit()

        if cursor.rowcount == 0:
            return None  # Duplicate URL for this channel

        cursor = await db.execute("SELECT * FROM news_items WHERE id = ?", (cursor.lastrowid,))
        row = await cursor.fetchone()
        created_item = _row_to_news_item(row)

        # Flush old items for this channel
        await _flush_old_news_for_channel(db, item.channel_id)

        return created_item
    finally:
        await db.close()


async def _flush_old_news_for_channel(db, channel_id: int):
    """Delete oldest news items to keep only MAX_NEWS_ITEMS_PER_CHANNEL per channel."""
    cursor = await db.execute(
        "SELECT COUNT(*) FROM news_items WHERE channel_id = ? AND status = 'approved'",
        (channel_id,)
    )
    row = await cursor.fetchone()
    count = row[0] if row else 0

    if count > MAX_NEWS_ITEMS_PER_CHANNEL:
        delete_count = count - MAX_NEWS_ITEMS_PER_CHANNEL
        await db.execute(
            """
            DELETE FROM news_items
            WHERE id IN (
                SELECT id FROM news_items
                WHERE channel_id = ? AND status = 'approved'
                ORDER BY fetched_at ASC
                LIMIT ?
            )
            """,
            (channel_id, delete_count)
        )
        await db.commit()


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


async def get_news_items_by_status(status: NewsStatus, limit: int = 50, channel_id: int = None) -> List[NewsItem]:
    """Get news items by status, optionally filtered by channel."""
    db = await get_db()
    try:
        if channel_id:
            cursor = await db.execute(
                """
                SELECT * FROM news_items
                WHERE status = ? AND channel_id = ?
                ORDER BY fetched_at DESC
                LIMIT ?
                """,
                (status.value, channel_id, limit)
            )
        else:
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


async def get_counts_by_status(channel_id: int = None) -> dict:
    """Get count of items by status, optionally for a specific channel."""
    db = await get_db()
    try:
        if channel_id:
            cursor = await db.execute(
                """
                SELECT status, COUNT(*) as count
                FROM news_items
                WHERE channel_id = ?
                GROUP BY status
                """,
                (channel_id,)
            )
        else:
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


async def url_exists_for_channel(url: str, channel_id: int) -> bool:
    """Check if a URL already exists for a specific channel."""
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT 1 FROM news_items WHERE source_url = ? AND channel_id = ?",
            (url, channel_id)
        )
        row = await cursor.fetchone()
        return row is not None
    finally:
        await db.close()


async def url_exists(url: str) -> bool:
    """Check if a URL already exists in any channel."""
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
        channel_id=row['channel_id'] if 'channel_id' in row.keys() else 1,
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
