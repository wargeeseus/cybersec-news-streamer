import httpx
import logging
from typing import Dict, Any, Optional

from ..config import settings

logger = logging.getLogger(__name__)

SUMMARIZE_PROMPT = """You are a cybersecurity news anchor. Summarize the following news article in 2-3 sentences for a live stream audience.
Be concise, factual, and highlight the key security implications.
Also provide a shorter, punchy headline (max 80 characters).

Article Title: {title}

Article Content: {description}

Respond in this exact format:
HEADLINE: <your headline here>
SUMMARY: <your 2-3 sentence summary here>
"""


async def summarize_news(title: str, description: str) -> Optional[Dict[str, str]]:
    """
    Summarize a news article using Ollama.

    Returns:
        Dict with 'headline' and 'summary' keys, or None if failed.
    """
    prompt = SUMMARIZE_PROMPT.format(title=title, description=description)

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{settings.ollama_url}/api/generate",
                json={
                    "model": settings.ollama_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {
                        "temperature": 0.7,
                        "num_predict": 300,
                    }
                }
            )
            response.raise_for_status()

        result = response.json()
        text = result.get("response", "")

        # Parse the response
        return _parse_summary_response(text, title)

    except httpx.ConnectError:
        logger.error(f"Cannot connect to Ollama at {settings.ollama_url}")
        return _fallback_summary(title, description)
    except Exception as e:
        logger.error(f"Error summarizing news: {e}")
        return _fallback_summary(title, description)


def _parse_summary_response(text: str, original_title: str) -> Dict[str, str]:
    """Parse the LLM response to extract headline and summary."""
    headline = original_title
    summary = ""

    lines = text.strip().split('\n')

    for line in lines:
        line = line.strip()
        if line.upper().startswith("HEADLINE:"):
            headline = line[9:].strip()
        elif line.upper().startswith("SUMMARY:"):
            summary = line[8:].strip()

    # If summary spans multiple lines after SUMMARY:
    if not summary:
        in_summary = False
        summary_lines = []
        for line in lines:
            if line.upper().startswith("SUMMARY:"):
                in_summary = True
                rest = line[8:].strip()
                if rest:
                    summary_lines.append(rest)
            elif in_summary:
                summary_lines.append(line.strip())
        summary = ' '.join(summary_lines)

    # Fallback if parsing failed
    if not summary:
        summary = text[:500] if text else "No summary available."

    return {
        "headline": headline[:100],
        "summary": summary[:500],
    }


def _clean_summary_prefix(text: str) -> str:
    """Remove common summary prefixes from text."""
    import re

    # Patterns to remove at the start (case-insensitive)
    prefixes = [
        r'^executive\s+summary\s*[:\-]?\s*',
        r'^summary\s*[:\-]?\s*',
        r'^overview\s*[:\-]?\s*',
        r'^abstract\s*[:\-]?\s*',
        r'^description\s*[:\-]?\s*',
    ]

    cleaned = text.strip()
    for pattern in prefixes:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)

    return cleaned.strip()


def _fallback_summary(title: str, description: str) -> Dict[str, str]:
    """Provide a fallback summary when LLM is unavailable."""
    # Clean up any summary prefixes
    cleaned_desc = _clean_summary_prefix(description)

    # Use first 2 sentences of description as summary
    sentences = cleaned_desc.split('.')
    summary = '. '.join(sentences[:2]).strip()
    if summary and not summary.endswith('.'):
        summary += '.'

    return {
        "headline": title[:100],
        "summary": summary[:500] if summary else "Summary unavailable - see source for details.",
    }


async def check_ollama_health() -> bool:
    """Check if Ollama is accessible."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{settings.ollama_url}/api/tags")
            return response.status_code == 200
    except Exception:
        return False
