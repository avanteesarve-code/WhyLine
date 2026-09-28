"""Offline tests for the standalone FinBERT sentiment layer (Milestone B Step 3).

Nothing here downloads FinBERT, touches Hugging Face, uses the network, or
loads the real model: every test runs against a fake classifier returning
FinBERT-shaped output, and an autouse fixture blocks the real loader.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest

import sentiment
from config import Settings
from models import NewsItem
from sentiment import SentimentBatch, get_classifier, score_texts

BACKEND_DIR = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def probs(pos, neg, neu, order=("positive", "negative", "neutral")):
    """One headline of FinBERT-shaped output: a list of {label, score}."""
    table = {"positive": pos, "negative": neg, "neutral": neu}
    return [{"label": label, "score": table[label]} for label in order]


class FakeClassifier:
    """FinBERT-shaped stub: records calls, replays canned outputs."""

    def __init__(self, outputs=None, side_effect=None):
        self.outputs = outputs
        self.side_effect = side_effect
        self.calls: list[dict] = []

    def __call__(self, texts, truncation=True, **kwargs):
        self.calls.append({"texts": list(texts), "truncation": truncation})
        if self.side_effect is not None:
            raise self.side_effect
        if callable(self.outputs):
            return self.outputs(list(texts))
        return self.outputs


@pytest.fixture(autouse=True)
def _isolate_loader(monkeypatch):
    """Reset loader caches and forbid the real model loader in every test."""
    monkeypatch.setattr(sentiment, "_classifier", None)
    monkeypatch.setattr(sentiment, "_classifier_model", None)
    monkeypatch.setattr(sentiment, "_load_failed_for", None)

    def _boom(model_name):
        raise AssertionError("real loader must not run in tests")

    monkeypatch.setattr(sentiment, "_default_loader", _boom)


@pytest.fixture()
def settings():
    return Settings()


# ---------------------------------------------------------------------------
# 1-3. Basic tone mapping
# ---------------------------------------------------------------------------


def test_positive_sentiment(settings):
    fake = FakeClassifier(outputs=[probs(0.8, 0.05, 0.15)])
    batch = score_texts(["Stocks rally on strong earnings"], settings, classifier=fake)
    assert batch.reason is None
    (res,) = batch.results
    assert res.label == "positive"
    assert res.score == pytest.approx(0.75)
    assert res.confidence == pytest.approx(0.8)


def test_negative_sentiment(settings):
    fake = FakeClassifier(outputs=[probs(0.05, 0.85, 0.10)])
    batch = score_texts(["Stocks plunge on rate fears"], settings, classifier=fake)
    assert batch.reason is None
    (res,) = batch.results
    assert res.label == "negative"
    assert res.score == pytest.approx(-0.8)
    assert res.score < 0
    assert res.confidence == pytest.approx(0.85)


def test_neutral_sentiment_near_zero(settings):
    fake = FakeClassifier(outputs=[probs(0.30, 0.31, 0.39)])
    batch = score_texts(["Company holds annual meeting"], settings, classifier=fake)
    assert batch.reason is None
    (res,) = batch.results
    assert res.label == "neutral"
    assert abs(res.score) < 0.05
    assert res.confidence == pytest.approx(0.39)


# ---------------------------------------------------------------------------
# 4. Label normalisation
# ---------------------------------------------------------------------------


def test_label_normalisation(settings):
    fake = FakeClassifier(
        outputs=[
            [
                {"label": " Positive ", "score": 0.7},
                {"label": " NEGATIVE ", "score": 0.1},
                {"label": "Neutral", "score": 0.2},
            ]
        ]
    )
    batch = score_texts(["Some headline"], settings, classifier=fake)
    assert batch.reason is None
    (res,) = batch.results
    assert res.label == "positive"
    assert res.score == pytest.approx(0.6)
    assert res.confidence == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# 5. Score clamping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (probs(1.2, -0.3, 0.0), 1.0),  # diff 1.5 -> clamped
        (probs(-0.2, 1.4, 0.0), -1.0),  # diff -1.6 -> clamped
    ],
)
def test_score_clamping(settings, output, expected):
    fake = FakeClassifier(outputs=[output])
    batch = score_texts(["Degenerate model output"], settings, classifier=fake)
    assert batch.reason is None
    (res,) = batch.results
    assert res.score == expected
    assert -1.0 <= res.score <= 1.0


# ---------------------------------------------------------------------------
# 6. Four-decimal rounding
# ---------------------------------------------------------------------------


def test_four_decimal_rounding(settings):
    fake = FakeClassifier(outputs=[probs(0.512345, 0.100001, 0.387654)])
    batch = score_texts(["Rounding headline"], settings, classifier=fake)
    assert batch.reason is None
    (res,) = batch.results
    assert res.score == round(0.512345 - 0.100001, 4) == 0.4123
    assert res.confidence == 0.5123


# ---------------------------------------------------------------------------
# 7-9. Batch alignment, empty input, all-invalid input
# ---------------------------------------------------------------------------


def test_mixed_batch_alignment(settings):
    texts = [
        "Markets rally on strong earnings",  # valid
        "",  # invalid
        "   ",  # invalid
        123,  # invalid
        None,  # invalid
        "Stocks plunge on rate fears",  # valid
    ]
    fake = FakeClassifier(
        outputs=[probs(0.8, 0.05, 0.15), probs(0.05, 0.85, 0.10)]
    )
    batch = score_texts(texts, settings, classifier=fake)
    assert batch.reason is None
    assert len(batch.results) == 6
    assert batch.results[1] is None
    assert batch.results[2] is None
    assert batch.results[3] is None
    assert batch.results[4] is None
    assert batch.results[0].label == "positive"
    assert batch.results[5].label == "negative"
    # Classifier sees only the valid texts, in one single call.
    assert len(fake.calls) == 1
    assert fake.calls[0]["texts"] == [texts[0], texts[5]]
    assert fake.calls[0]["truncation"] is True


def test_empty_input_list(settings):
    fake = FakeClassifier(outputs=[])
    batch = score_texts([], settings, classifier=fake)
    assert isinstance(batch, SentimentBatch)
    assert batch.results == []
    assert batch.reason is None
    assert fake.calls == []


def test_empty_input_never_loads_model(settings):
    batch = score_texts([], settings)  # no injected classifier; boom on load
    assert batch.results == []
    assert batch.reason is None


def test_all_invalid_input(settings):
    fake = FakeClassifier(outputs=[])
    batch = score_texts(["", "   ", 42, None], settings, classifier=fake)
    assert batch.results == [None, None, None, None]
    assert batch.reason is None
    assert fake.calls == []


def test_all_invalid_never_loads_model(settings):
    batch = score_texts(["", "  ", 7], settings)
    assert batch.results == [None, None, None]
    assert batch.reason is None


# ---------------------------------------------------------------------------
# 10. Disabled sentiment
# ---------------------------------------------------------------------------


def test_disabled_sentiment_never_loads_model():
    off = Settings(sentiment_enabled=False)
    batch = score_texts(["Markets rally"], off)  # boom if the loader runs
    assert batch.results == [None]
    assert batch.reason == "disabled"


def test_disabled_sentiment_ignores_injected_classifier():
    off = Settings(sentiment_enabled=False)
    fake = FakeClassifier(outputs=[probs(0.8, 0.05, 0.15)])
    batch = score_texts(["a", "b"], off, classifier=fake)
    assert batch.results == [None, None]
    assert batch.reason == "disabled"
    assert fake.calls == []


# ---------------------------------------------------------------------------
# 11-13. Lazy loading, caching, concurrent loading
# ---------------------------------------------------------------------------


def _run_fresh_subprocess(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=BACKEND_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_lazy_loading_no_model_on_import():
    proc = _run_fresh_subprocess(
        "import sentiment;"
        "assert sentiment._classifier is None,"
        " 'import must not load the model';"
        "print('lazy-ok')"
    )
    assert proc.returncode == 0, proc.stderr
    assert "lazy-ok" in proc.stdout


def test_caching_reuses_classifier(settings, monkeypatch):
    loads: list[str] = []
    sentinel = object()

    def _counting_loader(model_name):
        loads.append(model_name)
        return sentinel

    monkeypatch.setattr(sentiment, "_default_loader", _counting_loader)
    first = get_classifier(settings)
    second = get_classifier(settings)
    assert first is second is sentinel
    assert loads == [settings.sentiment_model]


def test_concurrent_loading_runs_once(settings, monkeypatch):
    loads: list[str] = []
    sentinel = object()
    barrier = threading.Barrier(8)

    def _slow_loader(model_name):
        import time

        loads.append(model_name)
        time.sleep(0.05)
        return sentinel

    monkeypatch.setattr(sentiment, "_default_loader", _slow_loader)
    results: list[object] = []

    def _worker():
        barrier.wait()
        results.append(get_classifier(settings))

    threads = [threading.Thread(target=_worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [sentinel] * 8
    assert loads == [settings.sentiment_model]


# ---------------------------------------------------------------------------
# 14-15. Loader failure and classifier failure
# ---------------------------------------------------------------------------


def test_model_loader_failure(settings, monkeypatch):
    calls: list[str] = []

    def _failing_loader(model_name):
        calls.append(model_name)
        raise RuntimeError("no network")

    monkeypatch.setattr(sentiment, "_default_loader", _failing_loader)
    first = score_texts(["Markets rally"], settings)
    assert first.results == [None]
    assert first.reason == "model_unavailable"
    # A permanently failed load is not retried on every call.
    second = score_texts(["Markets rally"], settings)
    assert second.results == [None]
    assert second.reason == "model_unavailable"
    assert calls == [settings.sentiment_model]


def test_classifier_failure(settings):
    fake = FakeClassifier(side_effect=RuntimeError("inference blew up"))
    batch = score_texts(["a", "b"], settings, classifier=fake)
    assert batch.results == [None, None]
    assert batch.reason == "inference_error"


# ---------------------------------------------------------------------------
# 16. Malformed output
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        # Wrong top-level nesting: not a per-text list.
        {"label": "positive", "score": 0.9},
        # Wrong nesting: per-text entry is a dict, not a list of dicts.
        [{"label": "positive", "score": 0.5}],
        # Inner entries missing the label key.
        [[{"score": 0.5}, {"label": "negative", "score": 0.3}, {"label": "neutral", "score": 0.2}]],
        # Inner entries missing the score key.
        [[{"label": "positive"}, {"label": "negative", "score": 0.3}, {"label": "neutral", "score": 0.2}]],
        # Missing one of the required classes (only two entries).
        [[{"label": "positive", "score": 0.6}, {"label": "negative", "score": 0.4}]],
        # Unknown LABEL_0-style labels are never valid FinBERT output.
        [[{"label": "LABEL_0", "score": 0.5}, {"label": "LABEL_1", "score": 0.3}, {"label": "LABEL_2", "score": 0.2}]],
        # Non-numeric score.
        [[{"label": "positive", "score": "high"}, {"label": "negative", "score": 0.1}, {"label": "neutral", "score": 0.2}]],
        # Wrong length: two outputs for one valid input.
        [probs(0.8, 0.05, 0.15), probs(0.05, 0.85, 0.10)],
    ],
    ids=[
        "top-level-dict",
        "per-text-dict",
        "missing-label",
        "missing-score",
        "missing-class",
        "label-indices",
        "non-numeric-score",
        "length-mismatch",
    ],
)
def test_malformed_output(settings, raw):
    fake = FakeClassifier(outputs=raw)
    batch = score_texts(["Some headline"], settings, classifier=fake)
    # Failed inference is missing sentiment, never fake neutral sentiment.
    assert batch.results == [None]
    assert batch.reason == "inference_error"


# ---------------------------------------------------------------------------
# 17. Label mapping is by name, not position
# ---------------------------------------------------------------------------


def test_label_mapping_by_name_not_position(settings):
    fake = FakeClassifier(
        outputs=[
            [
                {"label": "positive", "score": 0.10},
                {"label": "neutral", "score": 0.20},
                {"label": "negative", "score": 0.70},
            ]
        ]
    )
    batch = score_texts(["Shuffled headline"], settings, classifier=fake)
    assert batch.reason is None
    (res,) = batch.results
    # A position-based mapping would have reported "positive" here.
    assert res.label == "negative"
    assert res.score == pytest.approx(0.10 - 0.70)
    assert res.confidence == pytest.approx(0.70)


def test_shuffled_winning_order(settings):
    fake = FakeClassifier(outputs=[probs(0.70, 0.20, 0.10, order=("neutral", "negative", "positive"))])
    batch = score_texts(["Shuffled headline"], settings, classifier=fake)
    assert batch.reason is None
    (res,) = batch.results
    assert res.label == "positive"
    assert res.score == pytest.approx(0.50)


# ---------------------------------------------------------------------------
# 18. Import hygiene
# ---------------------------------------------------------------------------


def test_import_does_not_pull_heavy_deps():
    proc = _run_fresh_subprocess(
        "import sys;"
        "import sentiment;"
        "assert 'transformers' not in sys.modules, 'transformers loaded on import';"
        "assert 'torch' not in sys.modules, 'torch loaded on import';"
        "print('hygiene-ok')"
    )
    assert proc.returncode == 0, proc.stderr
    assert "hygiene-ok" in proc.stdout


# ---------------------------------------------------------------------------
# 19. Settings parsing
# ---------------------------------------------------------------------------


def test_settings_defaults():
    s = Settings()
    assert s.sentiment_enabled is True
    assert s.sentiment_model == "ProsusAI/finbert"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("1", True),
        ("yes", True),
        ("on", True),
        (" TRUE ", True),
        ("false", False),
        ("0", False),
        ("no", False),
        ("off", False),
    ],
)
def test_sentiment_enabled_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("SENTIMENT_ENABLED", raw)
    assert Settings.from_env().sentiment_enabled is expected


def test_sentiment_enabled_default(monkeypatch):
    monkeypatch.delenv("SENTIMENT_ENABLED", raising=False)
    assert Settings.from_env().sentiment_enabled is True


@pytest.mark.parametrize("raw", ["", "   "])
def test_sentiment_model_blank_uses_default(monkeypatch, raw):
    monkeypatch.setenv("SENTIMENT_MODEL", raw)
    assert Settings.from_env().sentiment_model == "ProsusAI/finbert"


def test_sentiment_model_custom(monkeypatch):
    monkeypatch.setenv("SENTIMENT_MODEL", "  cardiffnlp/twitter-roberta-base-sentiment  ")
    assert Settings.from_env().sentiment_model == "cardiffnlp/twitter-roberta-base-sentiment"


# ---------------------------------------------------------------------------
# 20. NewsItem contract compatibility
# ---------------------------------------------------------------------------


def test_contract_compatibility_with_news_item(settings):
    fake = FakeClassifier(outputs=[probs(0.8, 0.05, 0.15)])
    batch = score_texts(["Stocks rally"], settings, classifier=fake)
    (res,) = batch.results
    item = NewsItem(
        headline="Stocks rally",
        sentiment_label=res.label,
        sentiment_score=res.score,
    )
    assert item.sentiment_label == "positive"
    assert item.sentiment_score == pytest.approx(0.75)


def test_contract_compatibility_missing_sentiment():
    item = NewsItem(headline="No sentiment yet")
    assert item.sentiment_label is None
    assert item.sentiment_score is None
