"""
marketing/content_generator.py
================================
Drafts a campaign post that joins the conversation around a discovered X post.

Fields available in the campaign "template":
  {hook}       — one-liner: written by the LLM (OPENAI_API_KEY) from the source post,
                 otherwise a random campaign "hooks" line
  {author}     — @username of the source post (tags the original creator)
  {tags}       — campaign "tag_accounts"
  {link}       — campaign "link" (e.g. the meme's pump.fun page)
  {source_url} — link to the source post when the campaign attaches its own media
                 (X can't attach media and quote at once; without media the source is quoted)
  {hashtags}   — campaign "hashtags" (dropped if the post would exceed 280 characters)
"""

import random
import re

import httpx

from config import settings
from logging_config.logger import get_logger

log = get_logger(__name__)

_MAX_POST_LENGTH = 280
_URL_LENGTH = 23  # X counts every link as 23 characters
_URL_RE = re.compile(r"https?://\S+")


def post_length(text: str) -> int:
    """X weighted length: links count 23, wide characters (emoji, CJK) count 2."""
    urls = _URL_RE.findall(text)
    rest = _URL_RE.sub("", text)
    return len(urls) * _URL_LENGTH + sum(2 if ord(ch) > 0x10FF else 1 for ch in rest)


def _render(template: str, fields: dict) -> str:
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in template.format(**fields).splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


class ContentGenerator:
    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None

    async def initialize(self) -> None:
        if settings.OPENAI_API_KEY:
            self._http = httpx.AsyncClient(
                base_url=settings.OPENAI_BASE_URL,
                headers={"Authorization": f"Bearer {settings.OPENAI_API_KEY}"},
                timeout=30.0,
            )
        log.info("content_generator_ready", mode="llm" if self._http else "template")

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()

    async def draft(self, campaign: dict, post: dict) -> str | None:
        hook = await self._llm_hook(campaign, post) or random.choice(campaign["hooks"])
        fields = {
            "hook": hook,
            "author": f"@{post['author']}",
            "tags": " ".join(campaign.get("tag_accounts", [])),
            "link": campaign.get("link", ""),
            "source_url": post["url"] if campaign.get("media") else "",
            "hashtags": " ".join(campaign.get("hashtags", [])),
        }
        text = _render(campaign["template"], fields)
        if post_length(text) > _MAX_POST_LENGTH:
            text = _render(campaign["template"], {**fields, "hashtags": ""})
        if post_length(text) > _MAX_POST_LENGTH:
            log.warning("draft_too_long", source=post["url"], length=post_length(text))
            return None
        return text

    async def _llm_hook(self, campaign: dict, post: dict) -> str | None:
        if not self._http:
            return None
        try:
            response = await self._http.post("/chat/completions", json={
                "model": settings.OPENAI_MODEL,
                "messages": [
                    {"role": "system", "content": (
                        f"You write one short, witty line (max 120 characters) for this campaign: {campaign['brief']}. "
                        "It replies to the trend in the given post. No hashtags, no links, no @mentions, no quotes."
                    )},
                    {"role": "user", "content": post["text"]},
                ],
                "max_tokens": 60,
                "temperature": 0.8,
            })
            response.raise_for_status()
            hook = response.json()["choices"][0]["message"]["content"].strip().strip('"')
            return hook[:120] or None
        except Exception as exc:
            log.warning("llm_fallback", reason=str(exc))
            return None
