"""Web scraper: extract clean text from a URL using requests + BeautifulSoup."""
from __future__ import annotations

import re
from typing import Optional
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def scrape_url(url: str, timeout: int = 15) -> str:
    """Return visible text content from a URL (best-effort)."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Remove noise
        for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "svg"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)
        # Collapse whitespace
        text = re.sub(r"\s{2,}", " ", text)
        return text[:8000]  # cap at 8k chars to keep prompts manageable
    except Exception as e:
        return f"[scrape failed: {e}]"


def extract_emails(text: str) -> list[str]:
    """Extract email addresses from a block of text."""
    pattern = r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
    found = re.findall(pattern, text)
    # Filter out common non-human addresses
    blacklist = {"noreply", "no-reply", "support", "info@example"}
    return list({e.lower() for e in found if not any(b in e.lower() for b in blacklist)})


def extract_linkedin_url(text: str) -> Optional[str]:
    """Find a LinkedIn company or personal URL in text."""
    match = re.search(r"https?://(?:www\.)?linkedin\.com/(?:company|in)/[\w\-]+", text)
    return match.group(0) if match else None


def extract_instagram_handle(text: str) -> Optional[str]:
    """Find an Instagram handle or URL in text."""
    match = re.search(r"(?:instagram\.com/|@)([\w.]+)", text)
    return match.group(1) if match else None
