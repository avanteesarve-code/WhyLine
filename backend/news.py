"""Crypto-news fetch for Milestone B semantic attribution.

Given an anomaly's symbol and UNIX-seconds timestamp, returns potentially
relevant crypto headlines as NewsItem models.

Honesty notes (read before treating results as explanations):

- Results are potentially relevant context, NOT causal attribution. A
  headline near an anomaly does not mean it caused the move.
- The provider is Finnhub market news (GET {base}/news?category=crypto),
  a latest-headlines feed: it returns recent crypto headlines with no
  server-side time-range or symbol filter, so results are filtered locally
  against the anomaly time window and per-asset keywords.
- Sentiment fields on returned NewsItems are always None here; a later
  FinBERT step fills them in.
- When the provider is unavailable (or unconfigured), clearly marked
  sample headlines from data/news_fallback.json are served instead.

Standalone by design: nothing here touches detection, state, the API
layer, or the frontend. A later step wires fetch_news into attribution.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import httpx

from config import Settings
from models import NewsItem

log = logging.getLogger("whyline.news")

NEWS_PATH = "/news"  # Finnhub market-news endpoint, category=crypto
NEWS_CATEGORY = "crypto"


# ---------------------------------------------------------------------------
# Symbol -> asset keywords (pure, unit-tested)
# ---------------------------------------------------------------------------

# Curated per-symbol keywords matched case-insensitively with word
# boundaries against headline + summary. Short tickers rely on the boundary
# so "sol" never matches "solution"; LINKUSDT deliberately omits the bare
# word "link" (it would match prose like "click the link") and matches only
# "chainlink".
_ASSET_KEYWORDS: dict[str, tuple[str, ...]] = {
    "BTCUSDT": ("bitcoin", "btc"),
    "ETHUSDT": ("ethereum", "eth"),
    "SOLUSDT": ("solana", "sol"),
    "XRPUSDT": ("xrp", "ripple"),
    "BNBUSDT": ("bnb", "binance"),
    "DOGEUSDT": ("dogecoin", "doge"),
    "ADAUSDT": ("cardano", "ada"),
    "AVAXUSDT": ("avalanche", "avax"),
    "LINKUSDT": ("chainlink",),
    "LTCUSDT": ("litecoin", "ltc"),
}


def asset_keywords(symbol: str) -> tuple[str, ...]:
    """Keywords identifying `symbol` in headline/summary text.

    Unknown symbols fall back to their base ticker (trailing "USDT"
    stripped) as the only keyword.
    """
    sym = symbol.strip().upper()
    if sym in _ASSET_KEYWORDS:
        return _ASSET_KEYWORDS[sym]
    base = sym[:-4] if sym.endswith("USDT") else sym
    return (base.lower(),) if base else ()


def _keyword_matches(text: str, keywords: tuple[str, ...]) -> bool:
    return any(
        re.search(r"\b" + re.escape(kw) + r"\b", text, re.IGNORECASE)
        for kw in keywords
    )


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NewsResult:
    """Outcome of a news fetch: items plus how they were obtained.

    `reason` is None on a real provider hit, otherwise one of
    "no_api_key" | "http_error" | "timeout" | "malformed" | "no_match".
    """

    items: list[NewsItem]
    is_fallback: bool
    reason: str | None


# ---------------------------------------------------------------------------
# Parsing / filtering (pure, unit-tested)
# ---------------------------------------------------------------------------


def parse_provider_item(raw: object) -> NewsItem | None:
    """Convert one Finnhub news object into a NewsItem, or None to skip it.

    Maps datetime -> published_at (int), headline -> headline,
    source -> source, url -> url. Sentiment fields stay None (a later
    FinBERT step fills them). Items with a missing/blank headline, a
    missing datetime, or a non-integer datetime are skipped.
    """
    if not isinstance(raw, dict):
        return None
    headline = raw.get("headline")
    if not isinstance(headline, str) or not headline.strip():
        return None
    published = raw.get("datetime")
    if isinstance(published, bool) or not isinstance(published, int):
        return None
    source = raw.get("source")
    url = raw.get("url")
    return NewsItem(
        headline=headline,
        source=source if isinstance(source, str) else None,
        url=url if isinstance(url, str) else None,
        published_at=int(published),
    )


def parse_provider_response(payload: object) -> list[NewsItem] | None:
    """Convert a full provider payload, or None when the shape is malformed.

    A non-list payload is malformed; individually malformed items are
    skipped rather than failing the whole response.
    """
    if not isinstance(payload, list):
        return None
    items: list[NewsItem] = []
    for raw in payload:
        item = parse_provider_item(raw)
        if item is not None:
            items.append(item)
    return items


def filter_news(
    candidates: list[tuple[NewsItem, str]],
    *,
    symbol: str,
    anomaly_time: int,
    before: int,
    after: int,
    max_items: int,
) -> list[NewsItem]:
    """Keep candidates relevant to (`symbol`, `anomaly_time`).

    Each candidate is a (parsed item, provider summary) pair — the summary
    is match text only and is not stored on the NewsItem. An item is kept
    only when its published_at falls within
    [anomaly_time - before, anomaly_time + after] (inclusive) AND an asset
    keyword matches headline + summary (case-insensitive, word-boundary).
    Results are deduplicated (by URL, else headline), ordered by closest
    publication time first (stable provider-order tiebreak), and capped.
    """
    keywords = asset_keywords(symbol)
    if not keywords or max_items <= 0:
        return []
    lo, hi = anomaly_time - before, anomaly_time + after
    kept: list[NewsItem] = []
    for item, summary in candidates:
        ts = item.published_at
        if ts is None or ts < lo or ts > hi:
            continue
        text = item.headline if not summary else f"{item.headline} {summary}"
        if not _keyword_matches(text, keywords):
            continue
        kept.append(item)
    seen: set[tuple[str, str]] = set()
    unique: list[NewsItem] = []
    for item in kept:
        key = ("url", item.url) if item.url else ("headline", item.headline)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    unique.sort(key=lambda a: abs(a.published_at - anomaly_time))
    return unique[:max_items]


# ---------------------------------------------------------------------------
# Local fallback (sample headlines)
# ---------------------------------------------------------------------------

_FALLBACK_SOURCE = "WhyLine sample data"
_fallback_cache: dict[str, list[str]] | None = None


def _load_fallback_data() -> dict[str, list[str]]:
    """Read data/news_fallback.json once; keys are uppercased symbols."""
    global _fallback_cache
    if _fallback_cache is None:
        path = Path(__file__).parent / "data" / "news_fallback.json"
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("news fallback data unreadable (%s)", type(exc).__name__)
            raw = {}
        data: dict[str, list[str]] = {}
        if isinstance(raw, dict):
            for key, entries in raw.items():
                if not isinstance(key, str) or not isinstance(entries, list):
                    continue
                data[key.upper()] = [
                    e["headline"]
                    for e in entries
                    if isinstance(e, dict)
                    and isinstance(e.get("headline"), str)
                    and e["headline"].strip()
                ]
        _fallback_cache = data
    return _fallback_cache


def fallback_news(symbol: str, max_items: int) -> list[NewsItem]:
    """Sample headlines for `symbol`: asset entries first, then generic ones.

    Fallback items are honest placeholders: source is always the sample-data
    marker and url/published_at/sentiment stay None.
    """
    data = _load_fallback_data()
    headlines = data.get(symbol.strip().upper(), []) + data.get("CRYPTO", [])
    return [
        NewsItem(headline=h, source=_FALLBACK_SOURCE)
        for h in headlines[:max_items]
    ]


# ---------------------------------------------------------------------------
# Provider fetch
# ---------------------------------------------------------------------------


def _failed(symbol: str, settings: Settings, reason: str) -> NewsResult:
    """One concise warning per failure; fallback when enabled. No token logged."""
    log.warning("news fetch for %s failed (%s)", symbol, reason)
    if settings.news_fallback_enabled:
        return NewsResult(
            items=fallback_news(symbol, settings.news_max_items),
            is_fallback=True,
            reason=reason,
        )
    return NewsResult(items=[], is_fallback=False, reason=reason)


async def fetch_news(
    symbol: str,
    anomaly_time: int,
    settings: Settings,
    *,
    client: httpx.AsyncClient | None = None,
) -> NewsResult:
    """Fetch crypto headlines potentially relevant to an anomaly.

    Without an API key no HTTP client is created and no request is made:
    the fallback is returned directly with reason "no_api_key". Otherwise
    GETs {news_api_base}/news?category=crypto with the token sent only in
    the X-Finnhub-Token header (never in the URL), then filters locally by
    time window and asset keywords. Provider/network errors are mapped to
    reasons and never raised.
    """
    symbol = symbol.upper()
    if settings.news_api_key is None:
        log.info("news fetch for %s: no API key, serving fallback", symbol)
        return NewsResult(
            items=fallback_news(symbol, settings.news_max_items),
            is_fallback=True,
            reason="no_api_key",
        )

    own_client = client is None
    client = client or httpx.AsyncClient(timeout=settings.news_timeout)
    try:
        url = settings.news_api_base.rstrip("/") + NEWS_PATH
        try:
            resp = await client.get(
                url,
                params={"category": NEWS_CATEGORY},
                headers={"X-Finnhub-Token": settings.news_api_key},
                timeout=settings.news_timeout,
            )
            resp.raise_for_status()
        except httpx.TimeoutException:
            return _failed(symbol, settings, "timeout")
        except httpx.HTTPError:
            # Network errors and non-2xx (including 401/429).
            return _failed(symbol, settings, "http_error")
        try:
            payload = resp.json()
        except ValueError:
            return _failed(symbol, settings, "malformed")

        if not isinstance(payload, list):
            return _failed(symbol, settings, "malformed")
        # parse_provider_item skips individually malformed entries; the
        # summary travels alongside (match text only) so filter_news can use it.
        candidates: list[tuple[NewsItem, str]] = []
        for raw in payload:
            item = parse_provider_item(raw)
            if item is None:
                continue
            summary = raw.get("summary") if isinstance(raw, dict) else None
            candidates.append(
                (item, summary if isinstance(summary, str) else "")
            )
        kept = filter_news(
            candidates,
            symbol=symbol,
            anomaly_time=anomaly_time,
            before=settings.news_window_before,
            after=settings.news_window_after,
            max_items=settings.news_max_items,
        )
        if not kept:
            return _failed(symbol, settings, "no_match")
        return NewsResult(items=kept, is_fallback=False, reason=None)
    finally:
        if own_client:
            await client.aclose()
