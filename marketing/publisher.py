"""
marketing/publisher.py
========================
Campaign marketing bot (meme side only — no trading).

Per enabled campaign in MARKETING_CAMPAIGNS_FILE, every MARKETING_POLL_INTERVAL:
  PostFinder (relevant trending X posts) → ContentGenerator (draft tagging the creator)
  → MARKETING_DRY_RUN=true : Telegram preview only, nothing is posted
  → MARKETING_DRY_RUN=false: Telegram 1-click approval → post on the campaign's X account
                             (campaign media attached, otherwise the source post is quoted)

Posting accounts use OAuth 1.0a user tokens: X_<ACCOUNT>_ACCESS_TOKEN / _SECRET.
"""

import asyncio
import json
import mimetypes
import os

import requests
import tweepy

from config import settings
from integrations.telegram_client import TelegramClient
from logging_config.logger import get_logger
from marketing.content_generator import ContentGenerator
from marketing.discovery import PostFinder

log = get_logger(__name__)

_MEDIA_UPLOAD_URL = "https://api.x.com/2/media/upload"
_MEDIA_CATEGORIES = {"image/png": "tweet_image", "image/jpeg": "tweet_image", "image/webp": "tweet_image",
                     "image/gif": "tweet_gif"}
_MAX_SEEN = 5_000


def load_campaigns() -> dict[str, dict]:
    with open(settings.MARKETING_CAMPAIGNS_FILE, encoding="utf-8") as fh:
        campaigns = json.load(fh)
    return {name: campaign for name, campaign in campaigns.items() if campaign.get("enabled")}


class XAccount:
    """A posting account (OAuth 1.0a user context)."""

    def __init__(self, name: str) -> None:
        token, secret = settings.x_account_tokens(name)
        if not (settings.X_API_KEY and settings.X_API_SECRET and token and secret):
            raise ValueError(f"X_API_KEY / X_API_SECRET / X_{name.upper()}_ACCESS_TOKEN[_SECRET] not set")
        self._client = tweepy.Client(
            consumer_key=settings.X_API_KEY, consumer_secret=settings.X_API_SECRET,
            access_token=token, access_token_secret=secret,
        )
        self._auth = tweepy.OAuth1UserHandler(settings.X_API_KEY, settings.X_API_SECRET, token, secret).apply_auth()

    async def username(self) -> str:
        """Verifies the tokens."""
        me = await asyncio.to_thread(self._client.get_me, user_auth=True)
        return me.data.username

    def _upload(self, path: str) -> str:
        category = _MEDIA_CATEGORIES.get(mimetypes.guess_type(path)[0] or "")
        if not category:
            raise ValueError(f"unsupported media type: {path} (png, jpg, webp, gif)")
        with open(path, "rb") as fh:
            response = requests.post(
                _MEDIA_UPLOAD_URL, auth=self._auth, timeout=60,
                files={"media": fh}, data={"media_category": category},
            )
        response.raise_for_status()
        return response.json()["data"]["id"]

    async def publish(self, text: str, media_path: str, quote_id: str) -> str:
        """Returns the new post ID."""
        media_ids = [await asyncio.to_thread(self._upload, media_path)] if media_path else None
        response = await asyncio.to_thread(
            self._client.create_tweet, text=text, media_ids=media_ids,
            quote_tweet_id=None if media_ids else quote_id, user_auth=True,
        )
        return str(response.data["id"])


class MarketingBot:
    def __init__(self, telegram: TelegramClient) -> None:
        self._telegram = telegram
        self._campaigns = load_campaigns()
        self._finder = PostFinder()
        self._writer = ContentGenerator()
        self._accounts: dict[str, XAccount] = {}
        self._seen: list[str] = []
        self._running = False
        if os.path.exists(settings.MARKETING_STATE_FILE):
            with open(settings.MARKETING_STATE_FILE, encoding="utf-8") as fh:
                self._seen = json.load(fh)

    async def start(self) -> None:
        await self._writer.initialize()
        if not settings.MARKETING_DRY_RUN:
            for name in {campaign["account"] for campaign in self._campaigns.values()}:
                account = XAccount(name)
                log.info("x_account_ready", account=name, username=await account.username())
                self._accounts[name] = account

        self._running = True
        log.info("marketing_bot_started", campaigns=list(self._campaigns), dry_run=settings.MARKETING_DRY_RUN)
        while self._running:
            for name, campaign in self._campaigns.items():
                try:
                    await self._run_campaign(name, campaign)
                except Exception as exc:
                    log.error("campaign_cycle_error", campaign=name, reason=str(exc))
            await asyncio.sleep(settings.MARKETING_POLL_INTERVAL)

    async def stop(self) -> None:
        self._running = False
        await self._writer.close()
        log.info("marketing_bot_stopped")

    def _mark_seen(self, post_id: str) -> None:
        self._seen = (self._seen + [post_id])[-_MAX_SEEN:]
        os.makedirs(os.path.dirname(settings.MARKETING_STATE_FILE) or ".", exist_ok=True)
        tmp = f"{settings.MARKETING_STATE_FILE}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self._seen, fh)
        os.replace(tmp, settings.MARKETING_STATE_FILE)

    async def _run_campaign(self, name: str, campaign: dict) -> None:
        posts = await self._finder.find(campaign, set(self._seen))
        for post in posts[:settings.MARKETING_MAX_POSTS]:
            self._mark_seen(post["id"])
            text = await self._writer.draft(campaign, post)
            if not text:
                continue
            media = campaign.get("media", "")
            preview = (
                f"Campaign: {name} | Account: {campaign['account']}\n"
                f"Source: {post['url']} (engagement {post['engagement']:,})\n"
                f"Attach: {media or 'quote of the source post'}\n\n{text}"
            )
            log.info("campaign_draft", campaign=name, source=post["url"], dry_run=settings.MARKETING_DRY_RUN)
            if settings.MARKETING_DRY_RUN:
                await self._telegram.send(f"🧪 DRY RUN — not posted\n{preview}")
            else:
                asyncio.create_task(self._approve_and_publish(name, campaign, post, text, preview))

    async def _approve_and_publish(self, name: str, campaign: dict, post: dict, text: str, preview: str) -> None:
        approved = await self._telegram.request_approval(
            f"📋 Approve post?\n{preview}",
            timeout_secs=settings.MARKETING_APPROVAL_TIMEOUT_MINUTES * 60,
        )
        if not approved:
            return
        try:
            post_id = await self._accounts[campaign["account"]].publish(text, campaign.get("media", ""), post["id"])
        except Exception as exc:
            log.error("campaign_publish_failed", campaign=name, reason=str(exc))
            await self._telegram.send(f"❌ Publish failed ({name}): {exc}")
            return
        log.info("campaign_published", campaign=name, post_id=post_id, source=post["url"])
        await self._telegram.send(f"✅ Posted ({name}): https://x.com/i/web/status/{post_id}")
