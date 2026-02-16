from enum import Enum
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class NewsStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    STREAMED = "streamed"


class NewsItem(BaseModel):
    id: Optional[int] = None
    title: str
    original_title: str
    summary: str
    source_name: str
    source_url: str
    status: NewsStatus = NewsStatus.PENDING
    frame_path: Optional[str] = None
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    approved_at: Optional[datetime] = None
    streamed_at: Optional[datetime] = None

    class Config:
        use_enum_values = True


class NewsItemCreate(BaseModel):
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
