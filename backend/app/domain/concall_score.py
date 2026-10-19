"""Concall follow-through score.

The language model only *classifies*: it lists commitments management made in the previous
quarter's call and, for each, a verdict on what the current quarter's call shows. This module does
the arithmetic and the evidence checks, so the score is reproducible from the stored verdicts.

Score (version `concall-v1`) = sum(importance weight x verdict credit) / sum(importance weight) x 100
- Credit: delivered 1.0, partially_delivered 0.5, in_progress 0.5, deferred 0.25, not_delivered 0,
  not_addressed 0 (silence on a prior commitment counts against follow-through).
- Importance weight: high 3, medium 2, low 1.
- An assessment whose quoted evidence is not found verbatim in the transcripts is excluded.
- Fewer than three grounded assessments gives no score.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

VERSION = "concall-v1"
CREDIT = {"delivered": 1.0, "partially_delivered": 0.5, "in_progress": 0.5, "deferred": 0.25, "not_delivered": 0.0,
          "not_addressed": 0.0}
WEIGHT = {"high": 3.0, "medium": 2.0, "low": 1.0}
MIN_COMMITMENTS = 3
MIN_QUOTE_CHARS = 20


@dataclass(frozen=True)
class Assessment:
    commitment: str
    verdict: str
    importance: str = "medium"
    evidence_grounded: bool = True


@dataclass(frozen=True)
class FollowThrough:
    score: float | None
    label: str
    status: str  # ok | insufficient_commitments
    assessed: int
    excluded_ungrounded: int
    counts: dict[str, int]
    confidence: str
    formula: str
    version: str = VERSION


FORMULA = "sum(importance weight x verdict credit) / sum(importance weight) x 100"


def label(score: float | None) -> str:
    if score is None:
        return "Insufficient data"
    return "Strong follow-through" if score >= 75 else "Mixed follow-through" if score >= 45 else "Weak follow-through"


def score(assessments: Sequence[Assessment]) -> FollowThrough:
    valid = [a for a in assessments if a.evidence_grounded and a.verdict in CREDIT]
    excluded = len(assessments) - len(valid)
    counts = dict(Counter(a.verdict for a in valid))
    if len(valid) < MIN_COMMITMENTS:
        return FollowThrough(None, label(None), "insufficient_commitments", len(valid), excluded, counts, "Low", FORMULA)
    total_w = sum(WEIGHT.get(a.importance, 2.0) for a in valid)
    value = sum(WEIGHT.get(a.importance, 2.0) * CREDIT[a.verdict] for a in valid) / total_w * 100
    if len(valid) >= 5 and excluded == 0:
        conf = "High"
    elif excluded <= len(valid) / 2:
        conf = "Medium"
    else:
        conf = "Low"
    return FollowThrough(round(value, 1), label(value), "ok", len(valid), excluded, counts, conf, FORMULA)


_PUNCT = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-", "\u2014": "-", "\u00ad": None})


def _norm(s: str) -> str:
    """Case, whitespace, ligature (PDF "ﬁ" -> "fi"), curly-quote and dash differences do not count as changes;
    a hyphen at a PDF line break keeps the hyphen ("AI-\nled" reads "AI-led")."""
    t = unicodedata.normalize("NFKC", s or "").translate(_PUNCT)
    t = re.sub(r"(\w)-\s*\n\s*(\w)", r"\1-\2", t)
    return re.sub(r"\s+", " ", t).strip().lower()


def quote_is_grounded(quote: str, text: str) -> bool:
    """True when `quote` appears verbatim in `text`, ignoring case and whitespace runs."""
    q = _norm(quote).strip("\"'“”‘’ .…")
    return len(q) >= MIN_QUOTE_CHARS and q in _norm(text)
