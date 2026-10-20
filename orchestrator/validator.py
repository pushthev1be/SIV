"""Grounding validator: deterministic checks on agent text before it can air.

    violations = validate_text(text, fact_packet, incident_state="reviewing")
    if violations: retry once naming them, then fall back to the template

1. Every number in the text must come from the fact packet the agent was given, allowing only the
   unit conversions a broadcaster would make (m -> cm / in / ft, km/h -> mph, 0.77 -> 77%) and
   rounding to the precision written.
2. Every event, incident and player ID in the text must appear in the packet.
3. While an incident is open or under review, no margin, distance measurement or verdict may appear,
   and nothing may say the check is complete (requirement N4).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

UNRESOLVED_STATES = {"open", "reviewing"}

# Conversions from a packet value to what may be written, by the unit that follows the number.
CONVERSIONS: dict[str, tuple[float, ...]] = {
    "cm": (100.0, 1.0),
    "mm": (1000.0,),
    "in": (1 / 0.0254,),
    "ft": (1 / 0.3048,),
    "m": (1.0,),
    "km": (1.0, 0.001),
    "km/h": (1.0,),
    "mph": (0.621371, 1.0),
    "%": (1.0, 100.0),
    "/100": (100.0, 1.0),
    "s": (1.0,),
    "": (1.0, 100.0),  # a bare number: as given, or a 0..1 index written out of 100
}
UNIT_ALIASES = {
    "centimetres": "cm", "centimeters": "cm", "centimetre": "cm", "centimeter": "cm",
    "millimetres": "mm", "millimeters": "mm",
    "inches": "in", "inch": "in", "feet": "ft", "foot": "ft",
    "metres": "m", "meters": "m", "metre": "m", "meter": "m",
    "kmh": "km/h", "kph": "km/h", "km per hour": "km/h",
    "percent": "%", "per cent": "%",
    "sec": "s", "secs": "s", "seconds": "s", "second": "s",
}  # fmt: skip
DISTANCE_UNITS = {"cm", "mm", "in", "ft", "m"}

ID_RE = re.compile(r"\b(?:inc-m\d{2}-\d+|m\d{2}-e\d+|[A-Z]{3}-\d{1,2})\b")
CLOCK_RE = re.compile(r"\b(\d{1,3}):([0-5]\d)\b|\b(\d{1,3})\+(\d{1,2})'|\b(\d{1,3})'")
# "Law 11", "Laws 12 and 14": references to the Laws of the Game, not match numbers
LAW_REF_RE = re.compile(r"\bLaws?\s+\d{1,2}(?:\s*(?:and|&|,)\s*\d{1,2})*\b", re.IGNORECASE)
SCORE_RE = re.compile(r"\b(\d{1,2})\s*[-–]\s*(\d{1,2})\b")
_UNIT_PATTERN = "|".join(re.escape(u) for u in sorted({*CONVERSIONS, *UNIT_ALIASES} - {""}, key=len, reverse=True))
NUMBER_RE = re.compile(rf"(?<![\w.])(\d+(?:\.\d+)?)(?:\s*({_UNIT_PATTERN})(?![a-z]))?", re.IGNORECASE)

# Verdict language that may not appear before the decision. "possible offside" and "checking for a
# penalty" are fine; saying what the answer is, is not.
VERDICT_RE = re.compile(
    r"\b(?:was|is|were|not|clearly|just|marginally)\s+(?:off|on)side\b"
    r"|\bonside\b|\bno\s+goal\b|\bgoal\s+(?:stands|disallowed|ruled\s+out|given|chalked\s+off)\b"
    r"|\bruled\s+out\b|\bdisallowed\b|\bpenalty\s+(?:awarded|given)\b|\bno\s+penalty\b"
    r"|\b(?:upheld|overturned|downgraded|rescinded)\b|\bthe\s+(?:decision|verdict)\s+is\b"
    r"|\bcheck\s+(?:is\s+)?(?:complete|completed|over|done)\b",
    re.IGNORECASE,
)
CHECK_COMPLETE_RE = re.compile(r"\bcheck\s+(?:is\s+)?(?:complete|completed|over|done)\b", re.IGNORECASE)
# "checking whether he was offside" asks the question; it does not answer it
# "a player is offside if ..." is the rule, not a verdict
RULE_FOLLOWS_RE = re.compile(r"\s+(?:if|when)\b", re.IGNORECASE)
QUESTION_RE = re.compile(r"\b(?:whether|if)\b[^.!?;:]*$", re.IGNORECASE)


@dataclass(frozen=True)
class Violation:
    kind: str  # unknown_number | unknown_id | premature_reveal | check_complete
    token: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}: '{self.token}' ({self.detail})"


def _flatten(packet: Any) -> tuple[list[float], set[str]]:
    numbers: list[float] = []
    strings: set[str] = set()

    def walk(v: Any) -> None:
        if isinstance(v, bool):
            return
        if isinstance(v, int | float):
            numbers.append(float(v))
        elif isinstance(v, str):
            strings.add(v)
            strings.update(ID_RE.findall(v))
        elif isinstance(v, dict):
            for k, x in v.items():
                strings.add(str(k))
                walk(x)
        elif isinstance(v, list | tuple | set):
            for x in v:
                walk(x)

    walk(packet)
    return numbers, strings


def _decimals(token: str) -> int:
    return len(token.split(".")[1]) if "." in token else 0


def _grounded(value: float, decimals: int, unit: str, numbers: list[float]) -> bool:
    tol = 0.5 * 10 ** (-decimals) + 1e-9
    for p in numbers:
        for f in CONVERSIONS.get(unit, CONVERSIONS[""]):
            if abs(abs(p) * f - value) <= tol:
                return True
    return False


def _clock_grounded(seconds: float, numbers: list[float], within: float) -> bool:
    return any(abs(p - seconds) <= within for p in numbers)


def validate_text(text: str, packet: Any, incident_state: str | None = None) -> list[Violation]:
    numbers, strings = _flatten(packet)
    out: list[Violation] = []

    # IDs first, then blank them so their digits are not read as numbers.
    for tok in ID_RE.findall(text):
        if tok not in strings:
            out.append(Violation("unknown_id", tok, "not in the fact packet"))
    scrubbed = LAW_REF_RE.sub(" ", ID_RE.sub(" ", text))

    # Match clocks: 78:01, 45+2', 78'
    for m in CLOCK_RE.finditer(scrubbed):
        if m.group(1) is not None:
            secs, within, tok = int(m.group(1)) * 60 + int(m.group(2)), 1.5, m.group(0)
        elif m.group(3) is not None:
            base = int(m.group(3)) * 60
            secs, within, tok = base + (int(m.group(4)) - 0.5) * 60, 60.0, m.group(0)
        else:
            secs, within, tok = int(m.group(5)) * 60 + 30, 60.0, m.group(0)
        if not _clock_grounded(secs, numbers, within):
            out.append(Violation("unknown_number", tok, "no clock in the packet at that time"))
    scrubbed = CLOCK_RE.sub(" ", scrubbed)

    # Scores: 2-2, 3–1 (each side must be in the packet)
    for m in SCORE_RE.finditer(scrubbed):
        for side in m.groups():
            if not _grounded(float(side), 0, "", numbers):
                out.append(Violation("unknown_number", m.group(0), "score not in the packet"))
                break
    scrubbed = SCORE_RE.sub(" ", scrubbed)

    unresolved = incident_state in UNRESOLVED_STATES
    for m in NUMBER_RE.finditer(scrubbed):
        token, unit = m.group(1), (m.group(2) or "").lower()
        unit = UNIT_ALIASES.get(unit, unit)
        value = float(token)
        if unresolved and unit in DISTANCE_UNITS:
            out.append(Violation("premature_reveal", m.group(0).strip(), "a measurement before the decision"))
            continue
        if not _grounded(value, _decimals(token), unit, numbers):
            out.append(Violation("unknown_number", m.group(0).strip(), "not in the fact packet"))

    if unresolved:
        for m in VERDICT_RE.finditer(text):
            if QUESTION_RE.search(text[max(0, m.start() - 60) : m.start()]):
                continue
            if RULE_FOLLOWS_RE.match(text, m.end()):  # "a player is offside if ..." states the rule
                continue
            kind = "check_complete" if CHECK_COMPLETE_RE.fullmatch(m.group(0)) else "premature_reveal"
            out.append(Violation(kind, m.group(0), "a verdict before the decision"))
    return out


def violation_note(violations: list[Violation]) -> str:
    """One line to hand back to the agent on its retry."""
    return "; ".join(str(v) for v in violations)
