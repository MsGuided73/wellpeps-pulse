"""Apify collectors: public posts gathered by Apify Store actors (Phase 9).

``apify_reddit`` runs ``trudax/reddit-scraper-lite`` (pay per result, about
$0.004 per post on the Free plan plus a small start fee) over a short list of
Reddit searches and maps each post to a Mention.

Cost controls, in this order:
- ``max_items`` per run (Apify stops saving results there);
- ``max_charge_usd`` per run (Apify's own hard cap, ``maxTotalChargeUsd``);
- the collector's monthly budget (harvey/collect.py stops scheduling a
  collector whose runs this month already cost ``monthly_budget_usd``);
- the Apify account's own monthly usage limit.

Privacy (harvey/collectors/__init__.py rules): public posts only; the author
is kept as a public handle ("u/name") and nothing else about the person; the
raw actor output is never stored, only the allow-listed Mention fields.
"""

import asyncio
import html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

import httpx
from pydantic import ValidationError

from harvey.collectors.base import Collector, register
from harvey.config import env_setting
from harvey.models import Mention, Platform

logger = logging.getLogger("harvey.collectors.apify")

API_BASE = "https://api.apify.com/v2"
TOKEN_ENV = "APIFY_TOKEN"
REDDIT_ACTOR = "trudax~reddit-scraper-lite"
TERMINAL = frozenset({"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"})
_WS_RX = re.compile(r"[ \t\r\f\v]+")


class ApifyError(RuntimeError):
    pass


def _naive_utc(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


def _clean(value) -> str:
    """Reddit text arrives HTML-escaped ("&#39;"); unescape and tidy spaces."""
    if not isinstance(value, str):
        return ""
    return _WS_RX.sub(" ", html.unescape(value)).strip()


def reddit_item_to_mention(item: dict) -> Mention | None:
    """One actor result -> Mention (posts and comments); None for anything
    else (community or user pages) or a result without a permalink."""
    kind = item.get("dataType")
    if kind not in ("post", "comment"):
        return None
    url = item.get("url") if isinstance(item.get("url"), str) else ""
    if not url.startswith("https://www.reddit.com/"):
        return None
    handle = item.get("username") if isinstance(item.get("username"), str) else ""
    community = item.get("parsedCommunityName") or (item.get("communityName") or "").removeprefix("r/")
    engagement = {"community": community} if community else {}
    for key in ("upVotes", "numberOfComments"):
        if isinstance(item.get(key), int):
            engagement[key] = item[key]
    parent = (item.get("parentId") or item.get("postId") or "") if kind == "comment" else ""
    try:
        return Mention(
            platform=Platform.REDDIT,
            external_id=str(item.get("id") or ""),
            url=url,
            author_handle=f"u/{handle}" if handle and handle != "[deleted]" else "",
            parent_external_id=str(parent or ""),
            title=_clean(item.get("title")),
            text=_clean(item.get("body")),
            posted_at=_naive_utc(item.get("createdAt")),
            engagement=engagement,
        )
    except ValidationError:
        return None


class ApifyClient:
    """Just enough of the Apify API: start a run, wait, read its dataset."""

    def __init__(self, token: str, http: httpx.AsyncClient | None = None, wait_seconds: int = 900):
        if not token:
            raise ApifyError(f"{TOKEN_ENV} is not set (.env locally, a runtime variable when deployed)")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._http = http
        self.wait_seconds = wait_seconds

    async def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        if self._http is not None:
            response = await self._http.request(method, API_BASE + path, headers=self._headers, **kwargs)
        else:
            async with httpx.AsyncClient(timeout=self.wait_seconds + 60) as client:
                response = await client.request(method, API_BASE + path, headers=self._headers, **kwargs)
        if response.status_code >= 400:
            # Apify error bodies never echo the token; keep the message short.
            raise ApifyError(f"Apify {method} {path.split('?')[0]} -> HTTP {response.status_code}: "
                             f"{response.text[:200]}")
        return response

    async def run(self, actor: str, actor_input: dict, max_items: int, max_charge_usd: float) -> dict:
        """Start ``actor`` and wait for it to finish; returns the run object."""
        params = {"waitForFinish": "60", "maxItems": str(int(max_items)),
                  "maxTotalChargeUsd": f"{float(max_charge_usd):.2f}"}
        run = (await self._request("POST", f"/acts/{actor}/runs", params=params, json=actor_input)).json()["data"]
        waited = 60
        while run.get("status") not in TERMINAL and waited < self.wait_seconds:
            run = (await self._request("GET", f"/actor-runs/{run['id']}",
                                       params={"waitForFinish": "60"})).json()["data"]
            waited += 60
        if run.get("status") not in TERMINAL:
            # Too slow: stop it (no further charges) but keep what it already
            # saved; those results are paid for.
            await self._request("POST", f"/actor-runs/{run['id']}/abort")
            run = (await self._request("GET", f"/actor-runs/{run['id']}",
                                       params={"waitForFinish": "30"})).json()["data"]
            logger.warning(f"{actor} run {run['id']} did not finish in {self.wait_seconds}s; aborted, "
                           "keeping the results it saved")
        return run

    async def items(self, dataset_id: str, limit: int) -> list[dict]:
        response = await self._request("GET", f"/datasets/{dataset_id}/items",
                                       params={"clean": "true", "limit": str(int(limit))})
        data = response.json()
        return data if isinstance(data, list) else []


@register
class ApifyRedditCollector(Collector):
    name = "apify_reddit"
    platform_default = Platform.REDDIT
    cost_note = "Apify pay-per-result (trudax/reddit-scraper-lite, ~$0.004/post + start fee), capped per run"

    def __init__(self, searches: list[str] | None = None, max_items: int = 30, max_charge_usd: float = 0.25,
                 lookback_hours: int = 48, token: str | None = None, client: ApifyClient | None = None):
        self.searches = [s.strip() for s in (searches or []) if s and s.strip()]
        self.max_items = int(max_items)
        self.max_charge_usd = float(max_charge_usd)
        self.lookback_hours = int(lookback_hours)
        self._client = client or ApifyClient(token if token is not None else env_setting(TOKEN_ENV))
        self.skipped = 0
        self.cost_usd = 0.0

    def actor_input(self, since: datetime | None) -> dict:
        floor = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=self.lookback_hours)
        start = max(since, floor) if since else floor
        per_search = max(1, self.max_items // max(1, len(self.searches)))
        return {
            "searches": self.searches, "searchPosts": True, "searchComments": False,
            "searchCommunities": False, "searchUsers": False, "sort": "new",
            "skipComments": True, "skipUserPosts": True, "skipCommunity": True,
            "ignoreStartUrls": True, "includeNSFW": False,
            "maxItems": self.max_items, "maxPostCount": per_search,
            "postDateLimit": start.strftime("%Y-%m-%dT%H:%M:%S"),
            "proxy": {"useApifyProxy": True, "apifyProxyGroups": ["RESIDENTIAL"]},
        }

    async def collect(self, since: datetime | None = None) -> AsyncIterator[Mention]:
        self.skipped = 0
        self.cost_usd = 0.0
        if not self.searches:
            logger.warning("apify_reddit: no searches configured; nothing to collect")
            return
        run = await self._client.run(REDDIT_ACTOR, self.actor_input(since), self.max_items, self.max_charge_usd)
        self.cost_usd = float(run.get("usageTotalUsd") or 0.0)
        if run.get("status") != "SUCCEEDED":
            # A run stopped by the charge cap still has useful results.
            logger.warning(f"apify_reddit run {run.get('id')} ended {run.get('status')}; reading what it saved")
        seen: set[str] = set()
        for item in await self._client.items(run.get("defaultDatasetId", ""), self.max_items * 2):
            mention = reddit_item_to_mention(item) if isinstance(item, dict) else None
            if mention is None or mention.url in seen:
                self.skipped += 1
                continue
            seen.add(mention.url)
            yield mention
            await asyncio.sleep(0)
