from __future__ import annotations

import math
import re
from collections import Counter

from .models import QualityBand, QualitySignals

_CJK = re.compile(r"[\u3400-\u9fff]")
_VALID = re.compile(r"[\u3400-\u9fffA-Za-z0-9，。！？；：、“”‘’（）《》—…·,.!?;:'\"()\-\s]")
_SUSPICIOUS = re.compile(r"(?:[A-Za-z]{1,2}\s*){6,}|[□�]{1,}|[_|]{3,}|\s{8,}")


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _character_score(text: str) -> float:
    compact = text.replace("\n", "")
    if not compact:
        return 0.0
    valid = sum(1 for char in compact if _VALID.fullmatch(char))
    replacement_penalty = 4 * (compact.count("�") + compact.count("□"))
    return _clamp((valid - replacement_penalty) / len(compact))


def _language_score(text: str) -> float:
    tokens = re.findall(r"[\u3400-\u9fff]|[A-Za-z]+|\d+", text)
    if len(tokens) < 8:
        return 0.35 if tokens else 0.0
    single_latin = sum(1 for token in tokens if len(token) == 1 and token.isascii())
    repeated = Counter(tokens).most_common(1)[0][1] / len(tokens)
    cjk_ratio = len(_CJK.findall(text)) / max(1, len(text.replace("\n", "")))
    script_score = 1.0 if cjk_ratio >= 0.22 else 0.78
    return _clamp(script_score - single_latin / len(tokens) * 0.8 - max(0, repeated - 0.18))


def _layout_score(text: str) -> float:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return 0.0
    tiny = sum(1 for line in lines if len(line) <= 2) / len(lines)
    very_long = sum(1 for line in lines if len(line) > 180) / len(lines)
    return _clamp(1.0 - tiny * 0.7 - very_long * 0.35)


def candidate_agreement(primary: str, secondary: str | None) -> float:
    if not secondary:
        return 0.5
    left = set(re.findall(r"[\u3400-\u9fffA-Za-z0-9]{2,}", primary))
    right = set(re.findall(r"[\u3400-\u9fffA-Za-z0-9]{2,}", secondary))
    if not left and not right:
        return 0.0
    return len(left & right) / max(1, len(left | right))


def evaluate_text_quality(
    text: str,
    *,
    expected_coverage: float = 1.0,
    secondary_candidate: str | None = None,
) -> QualitySignals:
    suspicious = [match.group(0)[:80] for match in _SUSPICIOUS.finditer(text)][:12]
    character = _character_score(text)
    language = _language_score(text)
    layout = _layout_score(text)
    coverage = _clamp(expected_coverage)
    agreement = candidate_agreement(text, secondary_candidate)
    weights = (0.27, 0.25, 0.18, 0.18, 0.12)
    parts = (character, language, layout, coverage, agreement)
    geometric = math.prod(
        max(0.05, value) ** weight for value, weight in zip(parts, weights, strict=True)
    )
    penalty = min(0.25, len(suspicious) * 0.025)
    return QualitySignals(
        character_score=round(character, 4),
        language_score=round(language, 4),
        layout_score=round(layout, 4),
        coverage_score=round(coverage, 4),
        agreement_score=round(agreement, 4),
        overall_score=round(_clamp(geometric - penalty), 4),
        suspicious_fragments=suspicious,
    )


def quality_band(score: float, *, accept: float, review: float) -> QualityBand:
    if score >= accept:
        return QualityBand.ACCEPTED
    if score >= review:
        return QualityBand.REVIEW
    return QualityBand.RESCUE
