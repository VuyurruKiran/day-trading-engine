from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import quote

from day_trading_engine.context.models import ContextRecord
from day_trading_engine.providers._json_http import get_json

JsonFetcher = Callable[..., dict]
_CASHTAG = re.compile(r"(?<![A-Z0-9])\$([A-Z][A-Z0-9.-]{0,9})\b", re.IGNORECASE)


class RedditProvider:
    name = "reddit"

    def __init__(
        self,
        subreddit: str,
        *,
        allowed_symbols: tuple[str, ...],
        limit: int = 50,
        engagement_cap: int = 10_000,
        fetch_json: JsonFetcher = get_json,
    ) -> None:
        """Configure Reddit collection with explicit symbols and bounded limits."""
        if not subreddit.strip() or not allowed_symbols:
            raise ValueError("subreddit and allowed_symbols are required")
        if not 1 <= limit <= 100 or engagement_cap < 1:
            raise ValueError("invalid Reddit collection limits")
        self._subreddit = subreddit.strip()
        self._allowed = {symbol.upper() for symbol in allowed_symbols}
        self._limit = limit
        self._engagement_cap = engagement_cap
        self._fetch_json = fetch_json

    def fetch(self, received_at: datetime) -> list[ContextRecord]:
        """Fetch and normalize qualifying cashtag posts as context records."""
        url = f"https://www.reddit.com/r/{quote(self._subreddit)}/new.json?limit={self._limit}"
        payload = self._fetch_json(url, headers={"User-Agent": "day-trading-engine/0.1"})
        children = payload.get("data", {}).get("children", [])
        records: list[ContextRecord] = []
        for child in children:
            post = child.get("data", {}) if isinstance(child, dict) else {}
            title = str(post.get("title") or "").strip()
            post_id = str(post.get("id") or "").strip()
            if not title or not post_id or post.get("removed_by_category"):
                continue
            symbols = tuple(
                dict.fromkeys(
                    symbol.upper()
                    for symbol in _CASHTAG.findall(title)
                    if symbol.upper() in self._allowed
                )
            )
            if not symbols:
                continue
            created = datetime.fromtimestamp(float(post.get("created_utc") or 0), tz=UTC)
            if created > received_at:
                continue
            score = min(max(int(post.get("score") or 0), 0), self._engagement_cap)
            comments = min(max(int(post.get("num_comments") or 0), 0), self._engagement_cap)
            records.append(
                ContextRecord(
                    kind="social",
                    provider=self.name,
                    external_id=post_id,
                    title=title,
                    source_at=created,
                    received_at=received_at,
                    symbols=symbols,
                    url=f"https://www.reddit.com{post.get('permalink', '')}",
                    payload={
                        "score": score,
                        "num_comments": comments,
                        "upvote_ratio": post.get("upvote_ratio"),
                        "subreddit": self._subreddit,
                    },
                )
            )
        return records
