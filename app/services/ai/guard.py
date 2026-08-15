"""Enforcing the AI reliability rules — §15, §21.

§15 is a list of twelve prohibitions, and a prompt asking a model politely to obey them
is not enforcement. This module checks the *output* against the source material it was
allowed to use, mechanically, before anything is stored or shown.

Three of §15's rules can be checked exactly, and those are the three that do the most
damage when broken:

  - **Never invent statistics.** Every number in the output must appear in the sources.
  - **Never invent quotations.** Any quoted span must appear in the sources.
  - **Never invent facts** (partial). Capitalized names in the output that appear
    nowhere in the sources are flagged — an outlet the story does not have, a place
    nobody reported.

The rest of §15 ("never change the meaning", "never present speculation as fact") are
judgements a checker cannot make, and pretending otherwise would be its own kind of
dishonesty. Those stay the provider prompt's job, backed by the review queue.

A failed check does not silently drop the result. It is recorded with its warnings and
routed to a human (§27.2), because a model that just tried to invent a casualty figure
is exactly the thing an editor should see.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Numbers, including 50,000 / 7.7 / 1980s. Ordinals and small counts are excluded
#: below, because "three children" is idiomatic paraphrase, not a fabricated statistic.
_NUMBER = re.compile(r"\d[\d,.]*")

#: Quoted spans, straight or curly, at least a few words long.
_QUOTED = re.compile(r"[\"“]([^\"”]{12,})[\"”]")

#: A capitalized run of two or more words ("Duke Energy") anywhere in the text. Two
#: capitalized words in a row is a name even at the start of a sentence, where a single
#: capitalized word is just sentence case.
_PROPER_RUN = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})+)")

#: A single capitalized word, but only mid-sentence, where capitalization means something.
_MID_SENTENCE_PROPER = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]{3,})\b")

#: Number words a summary may legitimately use for a figure written as digits.
_NUMBER_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four", "5": "five",
    "6": "six", "7": "seven", "8": "eight", "9": "nine", "10": "ten", "11": "eleven",
    "12": "twelve", "20": "twenty", "30": "thirty", "40": "forty", "50": "fifty",
    "100": "hundred", "1000": "thousand",
}

#: Words that are capitalized in ordinary prose without naming anything.
_COMMON_CAPS = frozenset(
    """
    The A An And But Or If Then When Where While What Which Who This That These Those
    It He She They We You I There Here His Her Its Their Our Monday Tuesday Wednesday
    Thursday Friday Saturday Sunday January February March April May June July August
    September October November December AI US UK EU UN
    """.split()
)


@dataclass(frozen=True)
class GuardResult:
    """Whether generated text is safe to publish, and why not."""

    ok: bool
    warnings: list[str]
    #: Confidence penalty to apply, 0.0-1.0 — a soft signal for §27.1's thresholds.
    penalty: float = 0.0

    @property
    def summary(self) -> str:
        return "; ".join(self.warnings) if self.warnings else "no issues"


def _normalize_number(token: str) -> str:
    return token.strip(".,").replace(",", "")


def _numbers_in(text: str) -> set[str]:
    return {_normalize_number(m.group(0)) for m in _NUMBER.finditer(text)} - {""}


def check(text: str, corpus: str, *, max_chars: int | None = None) -> GuardResult:
    """Check generated `text` against the `corpus` it was allowed to use.

    The corpus is the concatenated source reporting — nothing else is permitted as
    evidence, which is the whole point.
    """
    warnings: list[str] = []
    penalty = 0.0

    if not text or not text.strip():
        return GuardResult(False, ["generated text was empty"], 1.0)

    corpus_lower = corpus.lower()
    corpus_numbers = _numbers_in(corpus)

    # --- §15: never invent statistics ---------------------------------------
    invented_numbers = set()
    for token in _numbers_in(text):
        if token in corpus_numbers:
            continue
        # A digit written as a word in the source, or vice versa, is not invention.
        word = _NUMBER_WORDS.get(token)
        if word and word in corpus_lower:
            continue
        invented_numbers.add(token)
    if invented_numbers:
        warnings.append(
            f"contains figures absent from the sources: {', '.join(sorted(invented_numbers))}"
        )
        penalty += 0.6

    # --- §15: never invent quotations ---------------------------------------
    for match in _QUOTED.finditer(text):
        quoted = match.group(1).strip()
        if quoted.lower() not in corpus_lower:
            warnings.append(f'contains a quotation not found in the sources: "{quoted[:60]}"')
            penalty += 0.6
            break

    # --- §15: never invent sources or events (weak check) --------------------
    invented_names = set()
    candidates = [m.group(1) for m in _PROPER_RUN.finditer(text)]
    candidates += [m.group(1) for m in _MID_SENTENCE_PROPER.finditer(text)]
    for name in candidates:
        name = name.strip()
        if name in _COMMON_CAPS or len(name) < 4:
            continue
        if name.lower() not in corpus_lower:
            invented_names.add(name)
    if invented_names:
        warnings.append(
            f"names not present in the sources: {', '.join(sorted(invented_names)[:4])}"
        )
        penalty += 0.3

    # --- length --------------------------------------------------------------
    if max_chars and len(text) > max_chars:
        warnings.append(f"longer than the configured limit ({len(text)} > {max_chars} chars)")
        penalty += 0.1

    # Numbers and quotations are hard failures: those are the two ways a summary can
    # state something false with total confidence. A stray name is a warning.
    hard_failure = bool(invented_numbers) or any("quotation" in w for w in warnings)
    return GuardResult(not hard_failure, warnings, min(penalty, 1.0))


def adjust_confidence(base: float | None, result: GuardResult) -> float:
    """Lower a provider's confidence by whatever the guard found (§27.1)."""
    start = 0.8 if base is None else base
    return round(max(0.0, min(1.0, start * (1.0 - result.penalty))), 3)
