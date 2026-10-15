"""
marketing/discovery.py
=======================
Finds relevant, trending X posts for a campaign.

  X API v2 recent search (campaign "query", relevancy order)
  → relevance filter: must contain a campaign keyword, no excluded keyword,
    no $cashtag (other tokens' promotions are never referenced), not flagged
    sensitive, engagement ≥ campaign "min_engagement"
  → sorted by engagement, highest first
"""

import asyncio
import re

import tweepy

from config import settings
from logging_config.logger import get_logger

log = get_logger(__name__)

_SEARCH_RESULTS = 50
_CASHTAG_RE = re.compile(r"\$[a-z][a-z0-9]{1,9}\b")


def _contains(text: str, words: list[str]) -> bool:
    return any(re.search(rf"\b{re.escape(word.lower())}", text) for word in words)


class PostFinder:
    def __init__(self) -> None:
        if not settings.X_BEARER_TOKEN:
            raise ValueError("X_BEARER_TOKEN is not set")
        self._client = tweepy.Client(bearer_token=settings.X_BEARER_TOKEN)

    async def find(self, campaign: dict, exclude_ids: set[str]) -> list[dict]:
        try:
            response = await asyncio.to_thread(
                self._client.search_recent_tweets,
                query=campaign["query"],
                max_results=_SEARCH_RESULTS,
                sort_order="relevancy",
                tweet_fields=["public_metrics", "author_id", "created_at", "possibly_sensitive"],
                expansions=["author_id"],
                user_fields=["username"],
            )
        except tweepy.TooManyRequests:
            log.warning("rate_limited", platform="X", query=campaign["query"])
            return []

        users = {user.id: user.username for user in (response.includes or {}).get("users", [])}
        keywords = campaign.get("keywords", [])
        excluded = campaign.get("exclude_keywords", [])
        posts = []
        for tweet in response.data or []:
            text = (tweet.text or "").lower()
            author = users.get(tweet.author_id)
            if str(tweet.id) in exclude_ids or not author or tweet.possibly_sensitive:
                continue
            if (keywords and not _contains(text, keywords)) or _contains(text, excluded) or _CASHTAG_RE.search(text):
                continue
            metrics = tweet.public_metrics or {}
            engagement = (
                metrics.get("like_count", 0)
                + metrics.get("retweet_count", 0) * 2
                + metrics.get("quote_count", 0) * 2
                + metrics.get("reply_count", 0)
            )
            if engagement < campaign.get("min_engagement", 0):
                continue
            posts.append({
                "id": str(tweet.id),
                "url": f"https://x.com/{author}/status/{tweet.id}",
                "author": author,
                "text": tweet.text or "",
                "engagement": engagement,
                "created_at": tweet.created_at.isoformat() if tweet.created_at else "",
            })

        posts.sort(key=lambda post: post["engagement"], reverse=True)
        log.info("campaign_posts_found", query=campaign["query"], results=len(response.data or []), relevant=len(posts))
        return posts
