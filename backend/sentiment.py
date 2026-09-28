"""Standalone FinBERT sentiment-analysis layer (Milestone B, Step 3).

FinBERT (``ProsusAI/finbert``) is a financial-text sentiment model: given a
headline it predicts the *tone of the text* — positive, negative, or neutral.
The signed score reported here is ``P(positive) - P(negative)``.

What this is NOT (methodology boundary, do not blur it):

- A positive result means positive financial-news *tone*. It does NOT mean
  "the market will rise".
- It is NOT a market-direction or price-direction signal.
- It is NOT causal evidence: a sentiment-scored headline near an anomaly
  does not mean the headline caused the move.
- A missing sentiment (``None``) is different from neutral sentiment and
  must never be converted into a fake neutral score.

Crypto headlines may be somewhat out-of-domain: FinBERT was trained on
general financial text (e.g. analyst reports, earnings coverage) rather
than specifically crypto news, so tone predictions on crypto headlines
should be treated as noisy text-tone estimates.

Standalone by design: this module never imports ``news``, ``attribute``,
``state``, ``main``, or FastAPI, and it never downloads or loads the model
at import time. ``transformers`` / ``torch`` are imported lazily inside the
private loader only. A later Milestone B step wires ``score_texts`` into
the attribution pipeline; nothing here calls it.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from typing import Literal

from config import Settings

log = logging.getLogger("whyline.sentiment")

Label = Literal["positive", "negative", "neutral"]
Reason = Literal["disabled", "model_unavailable", "inference_error"]

_REQUIRED_LABELS = frozenset({"positive", "negative", "neutral"})

__all__ = [
    "SentimentResult",
    "SentimentBatch",
    "get_classifier",
    "score_texts",
]


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SentimentResult:
    """Tone of one headline: winning label, signed score, confidence."""

    label: Label
    score: float  # P(positive) - P(negative), clamped to [-1, 1], 4dp
    confidence: float  # probability of `label`, 4dp


@dataclass(frozen=True)
class SentimentBatch:
    """1:1-aligned results for a score_texts call.

    `reason` is None on success (including empty/all-invalid input),
    otherwise one of "disabled" | "model_unavailable" | "inference_error".
    """

    results: list[SentimentResult | None]
    reason: Reason | None


# ---------------------------------------------------------------------------
# Lazy, cached model loading (transformers/torch stay unimported until here)
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_classifier: object | None = None
_classifier_model: str | None = None
_load_failed_for: str | None = None


def _default_loader(model_name: str) -> object:
    """Build the Hugging Face text-classification pipeline for FinBERT.

    Private: imports transformers/torch lazily so ``import sentiment``
    never touches them. Verified against transformers 5.17.0, where
    ``top_k=None`` returns every class per input and ``truncation`` is
    accepted as a tokenizer pass-through kwarg.
    """
    import transformers

    return transformers.pipeline(
        "text-classification",
        model=model_name,
        top_k=None,
        device=-1,
    )


def get_classifier(settings: Settings) -> object | None:
    """Return the cached FinBERT pipeline, loading it lazily on first use.

    Cached by model name under a lock so concurrent callers never trigger
    duplicate loads. A permanently failed model name is remembered and
    never retried; returns None when the model is unavailable (callers map
    that to reason "model_unavailable").
    """
    global _classifier, _classifier_model, _load_failed_for
    with _lock:
        if _classifier is not None and _classifier_model == settings.sentiment_model:
            return _classifier
        if _load_failed_for == settings.sentiment_model:
            return None
        try:
            clf = _default_loader(settings.sentiment_model)
        except Exception as exc:
            _load_failed_for = settings.sentiment_model
            log.warning(
                "FinBERT model %r unavailable (%s)",
                settings.sentiment_model,
                type(exc).__name__,
            )
            return None
        _classifier = clf
        _classifier_model = settings.sentiment_model
        return _classifier


# ---------------------------------------------------------------------------
# Output parsing (by label string, never by position)
# ---------------------------------------------------------------------------


def _round4(value: float) -> float:
    rounded = round(value, 4)
    return 0.0 if rounded == 0 else rounded  # normalise -0.0


def _parse_single(entries: object) -> SentimentResult | None:
    """Parse one headline's class list into a SentimentResult, or None."""
    if not isinstance(entries, (list, tuple)):
        return None
    probs: dict[str, float] = {}
    for item in entries:
        if not isinstance(item, dict):
            return None
        raw_label = item.get("label")
        raw_score = item.get("score")
        if not isinstance(raw_label, str):
            return None
        if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
            return None
        if not math.isfinite(raw_score):
            return None
        label = raw_label.strip().lower()
        if label not in _REQUIRED_LABELS:
            # Unknown labels (e.g. "LABEL_0") are never valid FinBERT output.
            return None
        probs[label] = float(raw_score)
    if set(probs) != _REQUIRED_LABELS:
        # A missing class (or duplicate-collapsed set) is malformed.
        return None
    best = max(probs, key=lambda k: probs[k])
    score = min(1.0, max(-1.0, probs["positive"] - probs["negative"]))
    return SentimentResult(
        label=best,  # type: ignore[typeddict-item]
        score=_round4(score),
        confidence=_round4(probs[best]),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_texts(
    texts: list[str],
    settings: Settings,
    *,
    classifier: object | None = None,
) -> SentimentBatch:
    """Score headline tones; never raises, never invents sentiment.

    Invalid inputs (non-string, empty, whitespace-only) yield None at their
    position and are never sent to the classifier. Failures yield None
    results — a missing sentiment is not neutral sentiment — with a reason
    instead of an exception.
    """
    n = len(texts)
    if not settings.sentiment_enabled:
        return SentimentBatch(results=[None] * n, reason="disabled")

    valid_idx: list[int] = []
    valid_texts: list[str] = []
    for i, text in enumerate(texts):
        if isinstance(text, str) and text.strip():
            valid_idx.append(i)
            valid_texts.append(text)

    results: list[SentimentResult | None] = [None] * n
    if not valid_texts:
        return SentimentBatch(results=results, reason=None)

    clf = classifier if classifier is not None else get_classifier(settings)
    if clf is None:
        return SentimentBatch(results=results, reason="model_unavailable")

    try:
        try:
            raw = clf(valid_texts, truncation=True)  # type: ignore[operator]
        except TypeError:
            # Older pipeline API without the truncation kwarg.
            raw = clf(valid_texts)  # type: ignore[operator]
    except Exception as exc:
        log.warning("FinBERT inference failed (%s)", type(exc).__name__)
        return SentimentBatch(results=results, reason="inference_error")

    if not isinstance(raw, (list, tuple)) or len(raw) != len(valid_texts):
        return SentimentBatch(results=results, reason="inference_error")
    parsed = [_parse_single(entries) for entries in raw]
    if any(p is None for p in parsed):
        return SentimentBatch(results=[None] * n, reason="inference_error")
    for i, p in zip(valid_idx, parsed):
        results[i] = p
    return SentimentBatch(results=results, reason=None)
