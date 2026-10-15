"""
scripts/test_marketing.py
=========================
Read-only test of a campaign's X workflow — nothing is posted:
  1. Verifies the campaign's posting account tokens (if set)
  2. Runs discovery + relevance filter
  3. Prints the drafts exactly as they would be posted (text, tagging, attachment)

Usage:
  python scripts/test_marketing.py frog
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import asyncio
import json

from config import settings
from marketing.content_generator import ContentGenerator, post_length
from marketing.discovery import PostFinder
from marketing.publisher import XAccount


async def run(name: str, limit: int) -> None:
    with open(settings.MARKETING_CAMPAIGNS_FILE, encoding="utf-8") as fh:
        campaign = json.load(fh)[name]

    try:
        username = await XAccount(campaign["account"]).username()
        print(f"Posting account {campaign['account']}: @{username} (tokens OK)")
    except Exception as exc:
        print(f"Posting account {campaign['account']}: NOT READY — {exc}")
    media = campaign.get("media", "")
    if media and not os.path.exists(media):
        print(f"Media file missing: {media}")

    posts = await PostFinder().find(campaign, set())
    print(f"\nRelevant posts: {len(posts)}")
    writer = ContentGenerator()
    await writer.initialize()
    try:
        for post in posts[:limit]:
            text = await writer.draft(campaign, post)
            print("\n" + "-" * 60)
            print(f"Source: {post['url']} (engagement {post['engagement']:,}, {post['created_at']})")
            print(f"Source text: {post['text'][:200]!r}")
            print(f"Attach: {media or 'quote of the source post'}")
            if text:
                print(f"Draft ({post_length(text)}/280):\n{text}")
            else:
                print("Draft: skipped (too long)")
    finally:
        await writer.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Dry-run test of a marketing campaign")
    parser.add_argument("campaign", help="Campaign name in MARKETING_CAMPAIGNS_FILE")
    parser.add_argument("--limit", type=int, default=settings.MARKETING_MAX_POSTS, help="Drafts to show")
    args = parser.parse_args()
    asyncio.run(run(args.campaign, args.limit))


if __name__ == "__main__":
    main()
