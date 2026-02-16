# CyberSec News Streamer

A web portal for reviewing and streaming cybersecurity news to YouTube Live.

## Features

- **Automated News Fetching**: Pulls from top cybersecurity RSS feeds
- **AI Summarization**: Uses Ollama (llama3:8b) to create concise summaries
- **Manual Approval Workflow**: Review, edit, approve/reject news before streaming
- **Video Frame Generation**: Creates professional-looking frames with QR codes
- **YouTube Live Streaming**: Streams approved news items via FFmpeg

## Quick Start

### 1. Install Ollama (on host machine)

```bash
./scripts/setup_ollama.sh
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your YouTube stream key
```

### 3. Start Services

```bash
docker-compose up
```

### 4. Access Portal

Open http://localhost:8080

## Architecture

```
┌─────────────────────────────────────────────────────┐
│ MacBook Pro (Native)                                │
│   └── Ollama (llama3:8b) ← HTTP localhost:11434     │
└─────────────────────────────────────────────────────┘
                         ↑
┌─────────────────────────────────────────────────────┐
│ Docker Compose                                      │
│   ├── web-portal (FastAPI + HTMX) :8080             │
│   ├── worker (RSS fetcher + summarizer)             │
│   └── streamer (FFmpeg → YouTube)                   │
└─────────────────────────────────────────────────────┘
```

## Workflow

1. **Fetch**: Worker pulls news from RSS feeds
2. **Summarize**: Ollama creates headlines + summaries
3. **Review**: Items appear as "pending" in portal
4. **Approve/Edit**: Manual review before streaming
5. **Stream**: Approved items sent to YouTube Live

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `YOUTUBE_STREAM_KEY` | Your YouTube stream key | required |
| `OLLAMA_HOST` | Ollama server host | host.docker.internal |
| `OLLAMA_MODEL` | LLM model to use | llama3:8b |
| `NEWS_DISPLAY_SECONDS` | Seconds per news item | 30 |
| `NEWS_FETCH_INTERVAL_MINUTES` | Fetch interval | 5 |

## RSS Sources

- BleepingComputer
- The Hacker News
- Krebs on Security
- Dark Reading
- Threatpost
- SecurityWeek
- Naked Security (Sophos)
- CISA Alerts

## Optional Assets

Place these files in the `assets/` directory:

- `fonts/JetBrainsMono.ttf` - Custom font for frames
- `backgrounds/dark_cyber.png` - 1920x1080 background image
- `music/lofi_ambient.mp3` - Background music for stream

## Development

```bash
# Install dependencies locally
pip install -r requirements.txt

# Run web portal
uvicorn src.web.app:app --reload --port 8080

# Run worker
python -m src.worker

# Run streamer
python -m src.streamer
```

## License

MIT
