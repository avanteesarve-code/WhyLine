"""Milestone B — semantic attribution (news fetch + FinBERT sentiment).

When ``detect.py`` flags a LIVE anomaly, ``main.py`` broadcasts it
immediately with ``attribution.status == "pending"`` and starts
``run_attribution`` in the background. That coroutine fetches headlines
around the anomaly timestamp, scores their tone with FinBERT, builds the
``Attribution`` model, attaches it to the same anomaly object, and
broadcasts the ``attribution`` envelope. The live Binance feed never waits
for any of this.

Methodology boundary (read before treating results as explanations):

- Attribution is temporally associated news plus headline tone. It does
  NOT establish causality: a headline near an anomaly does not mean the
  news brought about the move.
- Sentiment scores describe the tone of the headline text. They do NOT
  mean the sentiment predicted the price movement, and positive tone
  does NOT mean the price will rise.
- A missing sentiment (``None``) is different from neutral sentiment and
  is never converted into a neutral score.

Standalone by design: nothing here touches FastAPI, ``state``, or
``main``. The ``broadcast`` callable is injected, and ``news`` /
``sentiment`` are imported as modules so tests can monkeypatch them.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Awaitable, Callable

import news
import sentiment
from config import Settings
from models import Anomaly, Attribution, attribution_message

log = logging.getLogger("whyline.attribute")

# Step 3's lock guards model *loading* only; inference itself is not
# serialized there. Concurrent attribution tasks share one FinBERT
# pipeline, so sentiment scoring is serialized here, inside the worker
# thread (the event loop is never blocked by this lock).
_inference_lock = threading.Lock()

_INTERNAL_ERROR = Attribution(
    status="error", is_fallback=False, items=[], error="internal_error"
)

__all__ = ["build_attribution", "attribute_anomaly", "run_attribution"]


# ---------------------------------------------------------------------------
# Pure builder: NewsResult + SentimentBatch -> Attribution
# ---------------------------------------------------------------------------


def _sentiment_error_part(reason: str | None, *, mismatch: bool) -> str | None:
    """Map a sentiment outcome onto its Attribution error fragment."""
    if mismatch:
        return "sentiment_inference_error"
    if reason == "disabled":
        return "sentiment_disabled"
    if reason == "model_unavailable":
        return "sentiment_model_unavailable"
    if reason == "inference_error":
        return "sentiment_inference_error"
    return None


def build_attribution(
    news_result: news.NewsResult,
    sentiment_batch: sentiment.SentimentBatch,
) -> Attribution:
    """Combine fetched news with headline tones into an Attribution.

    Pure: no network, no model loading, no state, no I/O. Never raises —
    unexpected input yields ``status="error"`` / ``error="internal_error"``
    without exposing exception details.
    """
    try:
        items = news_result.items
        if not items:
            # No headlines at all: sentiment is irrelevant, do not score.
            if news_result.reason == "no_match":
                return Attribution(status="no_news", items=[], error=None)
            # Provider/configuration failure is an error, not "no news".
            reason = news_result.reason
            return Attribution(
                status="error",
                is_fallback=False,
                items=[],
                error=f"news_{reason}" if reason else "news_unknown",
            )

        parts: list[str] = []
        if news_result.reason:
            # Fallback (or real) news carrying its fetch reason stays
            # explicitly identifiable, e.g. error="news_no_api_key".
            parts.append(f"news_{news_result.reason}")

        mismatch = len(sentiment_batch.results) != len(items)
        part = _sentiment_error_part(sentiment_batch.reason, mismatch=mismatch)
        if part is not None:
            parts.append(part)

        enriched = []
        for item, sent in zip(
            items,
            sentiment_batch.results if not mismatch else [],
        ):
            if sent is None:
                # Missing tone stays missing; never invented as neutral.
                enriched.append(item.model_copy(update={}))
            else:
                enriched.append(
                    item.model_copy(
                        update={
                            "sentiment_label": sent.label,
                            "sentiment_score": sent.score,
                        }
                    )
                )
        if mismatch:
            # Length mismatch: no headline can be trusted to its tone.
            enriched = [item.model_copy(update={}) for item in items]

        return Attribution(
            status="ok",
            is_fallback=bool(news_result.is_fallback),
            items=enriched,
            error="; ".join(parts) if parts else None,
        )
    except Exception as exc:
        log.warning("attribution build failed (%s)", type(exc).__name__)
        return _INTERNAL_ERROR


# ---------------------------------------------------------------------------
# Orchestration: fetch news, score tones off the event loop
# ---------------------------------------------------------------------------


async def attribute_anomaly(
    anomaly: Anomaly,
    settings: Settings,
    *,
    news_client: object | None = None,
    classifier: object | None = None,
) -> Attribution:
    """Fetch news and score headline tones for one anomaly. Never raises.

    News runs on the event loop (async HTTP); FinBERT scoring runs in a
    worker thread via ``asyncio.to_thread`` so the live feed never stalls.
    Unexpected exceptions map to ``internal_error`` (exception type only
    is logged — never payloads, keys, or URLs).
    """
    try:
        news_result = await news.fetch_news(
            anomaly.symbol,
            anomaly.time,
            settings,
            client=news_client,  # type: ignore[arg-type]
        )
    except Exception as exc:
        log.warning(
            "attribution fetch for %s failed (%s)",
            anomaly.symbol,
            type(exc).__name__,
        )
        return _INTERNAL_ERROR

    try:
        if not news_result.items:
            return build_attribution(
                news_result, sentiment.SentimentBatch(results=[], reason=None)
            )
        headlines = [item.headline for item in news_result.items]

        def _score() -> sentiment.SentimentBatch:
            with _inference_lock:
                return sentiment.score_texts(
                    headlines, settings, classifier=classifier
                )

        sentiment_batch = await asyncio.to_thread(_score)
        return build_attribution(news_result, sentiment_batch)
    except Exception as exc:
        log.warning(
            "attribution scoring for %s failed (%s)",
            anomaly.symbol,
            type(exc).__name__,
        )
        return _INTERNAL_ERROR


async def run_attribution(
    anomaly: Anomaly,
    settings: Settings,
    broadcast: Callable[[dict], Awaitable[None]],
    *,
    news_client: object | None = None,
    classifier: object | None = None,
) -> None:
    """Attach the final Attribution and broadcast it. Never crashes callers.

    The anomaly is mutated on the event-loop thread (never in the worker
    thread) and published with the existing ``attribution_message`` helper.
    ``asyncio.CancelledError`` is re-raised so shutdown keeps working.
    """
    start = time.perf_counter()
    try:
        result = await attribute_anomaly(
            anomaly, settings, news_client=news_client, classifier=classifier
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # defensive: attribute_anomaly never raises
        log.warning("attribution run for %s failed (%s)", anomaly.id, type(exc).__name__)
        result = _INTERNAL_ERROR

    anomaly.attribution = result
    try:
        await broadcast(attribution_message(anomaly.id, anomaly.symbol, result))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning(
            "attribution broadcast for %s failed (%s)",
            anomaly.id,
            type(exc).__name__,
        )
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    log.info(
        "attribution %s: status=%s items=%d fallback=%s (%.0fms)",
        anomaly.id,
        result.status,
        len(result.items),
        result.is_fallback,
        elapsed_ms,
    )
