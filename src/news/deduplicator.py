import hashlib
from typing import Set

# In-memory cache of seen URLs (will be checked against DB too)
_seen_urls: Set[str] = set()


def url_hash(url: str) -> str:
    """Generate a hash for a URL."""
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def is_duplicate(url: str) -> bool:
    """Check if we've already processed this URL in memory."""
    return url in _seen_urls


def mark_seen(url: str):
    """Mark a URL as seen."""
    _seen_urls.add(url)


def clear_cache():
    """Clear the in-memory cache."""
    _seen_urls.clear()
