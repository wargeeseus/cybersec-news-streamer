from enum import Enum
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class NewsStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    STREAMED = "streamed"


# --- Channel Models ---

class Channel(BaseModel):
    id: Optional[int] = None
    name: str
    news_topic: str  # Keywords to search for
    stream_key: str = ""
    rtmp_url: str = "rtmp://a.rtmp.youtube.com/live2"
    display_seconds: int = 30
    is_active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        use_enum_values = True


class ChannelCreate(BaseModel):
    name: str
    news_topic: str
    stream_key: str = ""
    rtmp_url: str = "rtmp://a.rtmp.youtube.com/live2"
    display_seconds: int = 30


class ChannelUpdate(BaseModel):
    name: Optional[str] = None
    news_topic: Optional[str] = None
    stream_key: Optional[str] = None
    rtmp_url: Optional[str] = None
    display_seconds: Optional[int] = None
    is_active: Optional[bool] = None


# --- News Item Models ---

class NewsItem(BaseModel):
    id: Optional[int] = None
    channel_id: int = 1  # Default channel
    title: str
    original_title: str
    summary: str
    source_name: str
    source_url: str
    status: NewsStatus = NewsStatus.APPROVED
    frame_path: Optional[str] = None
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    approved_at: Optional[datetime] = None
    streamed_at: Optional[datetime] = None

    class Config:
        use_enum_values = True


class NewsItemCreate(BaseModel):
    channel_id: int = 1
    title: str
    original_title: str
    summary: str
    source_name: str
    source_url: str


class NewsItemUpdate(BaseModel):
    title: Optional[str] = None
    summary: Optional[str] = None
    status: Optional[NewsStatus] = None
    frame_path: Optional[str] = None
