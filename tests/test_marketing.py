"""
tests/test_marketing.py
========================
Campaign marketing: relevance filter, draft tagging / attachment, X length limit.
"""

import json
from types import SimpleNamespace

import pytest

from config import settings
from marketing.content_generator import ContentGenerator, post_length
from marketing.discovery import PostFinder


@pytest.fixture
def campaign():
    with open(settings.MARKETING_CAMPAIGNS_FILE, encoding="utf-8") as fh:
        return json.load(fh)["frog"]


def _tweet(tweet_id, text, likes, author_id=1, sensitive=False):
    return SimpleNamespace(
        id=tweet_id, text=text, author_id=author_id, possibly_sensitive=sensitive, created_at=None,
        public_metrics={"like_count": likes, "retweet_count": 0, "quote_count": 0, "reply_count": 0},
    )


class TestDiscovery:

    @pytest.mark.asyncio
    async def test_relevance_filter(self, campaign):
        finder = object.__new__(PostFinder)
        response = SimpleNamespace(
            data=[
                _tweet(1, "Did you dissect a frog in high school?", 500),
                _tweet(2, "frog meme but low reach", 5),
                _tweet(3, "New frog coin $FROGX launching now", 900),
                _tweet(4, "frog giveaway, follow + RT", 900),
                _tweet(5, "frog pic", 900, sensitive=True),
                _tweet(6, "the girl from high school", 900),
                _tweet(7, "Froggy energy all week", 2_000),
            ],
            includes={"users": [SimpleNamespace(id=1, username="creator")]},
        )
        finder._client = SimpleNamespace(search_recent_tweets=lambda **_: response)

        posts = await finder.find(campaign, exclude_ids={"999"})
        assert [p["id"] for p in posts] == ["7", "1"]
        assert posts[0]["url"] == "https://x.com/creator/status/7"

    @pytest.mark.asyncio
    async def test_seen_posts_excluded(self, campaign):
        finder = object.__new__(PostFinder)
        response = SimpleNamespace(data=[_tweet(1, "frog meme", 500)],
                                   includes={"users": [SimpleNamespace(id=1, username="creator")]})
        finder._client = SimpleNamespace(search_recent_tweets=lambda **_: response)
        assert await finder.find(campaign, exclude_ids={"1"}) == []


class TestDrafts:

    POST = {"id": "7", "url": "https://x.com/creator/status/7", "author": "creator", "text": "frog", "engagement": 1}

    @pytest.mark.asyncio
    async def test_quote_draft_tags_creator(self, campaign):
        text = await ContentGenerator().draft({**campaign, "tag_accounts": ["@kevin"]}, self.POST)
        assert "@creator" in text and "@kevin" in text
        assert self.POST["url"] not in text  # quoted, not linked
        assert text.split("\n")[0] in campaign["hooks"]

    @pytest.mark.asyncio
    async def test_media_draft_links_source(self, campaign):
        text = await ContentGenerator().draft({**campaign, "media": "frog.png"}, self.POST)
        assert self.POST["url"] in text

    @pytest.mark.asyncio
    async def test_hashtags_dropped_when_too_long(self, campaign):
        long_hook = "x" * 260
        text = await ContentGenerator().draft({**campaign, "hooks": [long_hook]}, self.POST)
        assert "#frog" not in text
        assert post_length(text) <= 280

    @pytest.mark.asyncio
    async def test_too_long_is_skipped(self, campaign):
        assert await ContentGenerator().draft({**campaign, "hooks": ["x" * 300]}, self.POST) is None

    def test_post_length_counts_links_as_23(self):
        assert post_length("hi https://example.com/a/very/long/path/that/is/long") == 3 + 23
