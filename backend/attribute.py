"""Milestone B — semantic attribution (news fetch + FinBERT sentiment).

Planned flow: when detect.py flags an anomaly, fetch headlines around the
anomaly timestamp for the affected asset, score them with FinBERT
(ProsusAI/finbert via HuggingFace transformers, loaded once at startup),
and attach {headline, sentiment_label, sentiment_score} to the anomaly
event before it is broadcast.

Intentionally empty for Milestone A (the quantitative half).
"""
