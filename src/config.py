from pydantic_settings import BaseSettings
from pydantic import Field
from pathlib import Path


class Settings(BaseSettings):
    # YouTube
    youtube_stream_key: str = Field(default="", env="YOUTUBE_STREAM_KEY")

    # Ollama
    ollama_host: str = Field(default="localhost", env="OLLAMA_HOST")
    ollama_port: int = Field(default=11434, env="OLLAMA_PORT")
    ollama_model: str = Field(default="llama3:8b", env="OLLAMA_MODEL")

    # Stream Settings
    frame_width: int = Field(default=1920, env="FRAME_WIDTH")
    frame_height: int = Field(default=1080, env="FRAME_HEIGHT")
    news_display_seconds: int = Field(default=30, env="NEWS_DISPLAY_SECONDS")
    news_fetch_interval_minutes: int = Field(default=5, env="NEWS_FETCH_INTERVAL_MINUTES")

    # Web Portal
    portal_port: int = Field(default=8080, env="PORTAL_PORT")
    secret_key: str = Field(default="change-this-secret-key-in-production", env="SECRET_KEY")

    # Database
    database_path: str = Field(default="/app/data/news.db", env="DATABASE_PATH")

    # Assets
    assets_path: Path = Path("/app/assets")

    # Data directory (for Railway volume)
    data_path: Path = Path("/app/data")

    @property
    def ollama_url(self) -> str:
        return f"http://{self.ollama_host}:{self.ollama_port}"

    @property
    def youtube_rtmp_url(self) -> str:
        return f"rtmp://a.rtmp.youtube.com/live2/{self.youtube_stream_key}"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
