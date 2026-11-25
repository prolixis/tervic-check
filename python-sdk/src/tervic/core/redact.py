"""Secret and PII redaction applied to every snapshot before it is stored or exported.

Matches are replaced with ``[REDACTED:<type>:<first 8 hex of sha256>]`` so equal secrets still
compare equal and detectors can tell a secret was present without seeing it.
"""

from __future__ import annotations

import functools
import hashlib
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from tervic.core.serialize import MARK

REDACTED_RE = re.compile(r"\[REDACTED:([a-z0-9_-]+):[0-9a-f]{8}\]")

PII_TYPES = frozenset({"email", "phone", "card"})

_PLACEHOLDER = re.compile(
    r"^(?:your[_\- ]?|<|\$\{|\{\{|x{3,}|\*{3,}|replace[_\-]?me|change[_\-]?me|example|placeholder|"
    r"dummy|redacted|test[_\-]?key|none$|null$|true$|false$|\.\.\.)",
    re.IGNORECASE,
)

_SENSITIVE_KEY = re.compile(
    r"^(?:.*[_\-])?(?:password|passwd|pwd|secret|api[_\-]?key|apikey|access[_\-]?token|"
    r"refresh[_\-]?token|auth[_\-]?token|token|client[_\-]?secret|private[_\-]?key|authorization|"
    r"bearer|credentials?)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _Pattern:
    kind: str
    regex: re.Pattern[str]
    group: int = 0


SECRET_PATTERNS: tuple[_Pattern, ...] = (
    _Pattern(
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ),
    _Pattern("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    _Pattern("bearer", re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._~+/=-]{16,})"), 1),
    _Pattern("api_key", re.compile(r"\bsk-(?:proj-|ant-|live-|test-)?[A-Za-z0-9_-]{20,}")),
    _Pattern("api_key", re.compile(r"\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}")),
    _Pattern("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    _Pattern("github_token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})")),
    _Pattern("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    _Pattern("google_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    _Pattern(
        "connection_string",
        re.compile(r"\b[a-z][a-z0-9+.\-]{1,20}://[^\s:/@'\"]+:([^\s@/'\"]{3,})@[^\s'\"]+", re.IGNORECASE),
        1,
    ),
    _Pattern(
        "kv_secret",
        re.compile(
            r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|client[_-]?secret|token)"
            r"\b[\"']?\s*[:=]\s*[\"']?([^\s\"'&,;}]{4,})"
        ),
        1,
    ),
)

PII_PATTERNS: tuple[_Pattern, ...] = (
    _Pattern("email", re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")),
    _Pattern("card", re.compile(r"\b(?:\d[ -]?){12,18}\d\b")),
    _Pattern(
        "phone", re.compile(r"(?<![\w])\+?\d{1,3}?[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}(?![\w])")
    ),
)

_HIGH_ENTROPY = re.compile(r"(?<![A-Za-z0-9+/_=\-])[A-Za-z0-9+/_=\-]{32,}(?![A-Za-z0-9+/_=\-])")
_HEX_ONLY = re.compile(r"^[0-9a-fA-F]+$")
_ULID = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def _tag(kind: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:8]
    return f"[REDACTED:{kind}:{digest}]"


def is_placeholder(value: str) -> bool:
    stripped = value.strip().strip("\"'")
    return stripped == "" or bool(_PLACEHOLDER.match(stripped)) or REDACTED_RE.fullmatch(stripped) is not None


def shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts: dict[str, int] = {}
    for ch in value:
        counts[ch] = counts.get(ch, 0) + 1
    total = len(value)
    return -sum((c / total) * math.log2(c / total) for c in counts.values())


def _looks_high_entropy(token: str) -> bool:
    if _HEX_ONLY.match(token) or _ULID.match(token) or is_placeholder(token):
        return False
    has_letter = any(c.isalpha() for c in token)
    has_digit = any(c.isdigit() for c in token)
    if not (has_letter and has_digit):
        return False
    if "/" in token and token.count("/") > 2:  # paths / URLs
        return False
    return shannon_entropy(token) >= 4.2


def _luhn_ok(digits: str) -> bool:
    total = 0
    for index, ch in enumerate(reversed(digits)):
        n = int(ch)
        if index % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def find_secrets(text: str, *, pii: bool = False) -> list[tuple[str, str]]:
    """Return ``(kind, value)`` for every secret (and PII if requested) found in ``text``."""
    found: list[tuple[str, str]] = []
    spans: list[tuple[int, int]] = []

    def taken(start: int, end: int) -> bool:
        return any(start < e and end > s for s, e in spans)

    for pattern in SECRET_PATTERNS:
        for match in pattern.regex.finditer(text):
            value = match.group(pattern.group)
            start, end = match.span(pattern.group)
            if (
                value
                and not is_placeholder(value)
                and not REDACTED_RE.search(value)
                and not taken(start, end)
            ):
                found.append((pattern.kind, value))
                spans.append((start, end))
    for match in _HIGH_ENTROPY.finditer(text):
        start, end = match.span()
        if not taken(start, end) and _looks_high_entropy(match.group(0)):
            found.append(("high_entropy", match.group(0)))
            spans.append((start, end))
    if pii:
        for pattern in PII_PATTERNS:
            for match in pattern.regex.finditer(text):
                value = match.group(0)
                start, end = match.span()
                if taken(start, end):
                    continue
                digits = re.sub(r"\D", "", value)
                if pattern.kind == "card" and not (13 <= len(digits) <= 19 and _luhn_ok(digits)):
                    continue
                if pattern.kind == "phone" and not (10 <= len(digits) <= 15):
                    continue
                found.append((pattern.kind, value))
                spans.append((start, end))
    return found


_SHORTEST_SECRET = 8  # nothing shorter than this can match any pattern above


def redact_text(text: str, *, pii: bool = False) -> str:
    if len(text) < _SHORTEST_SECRET:
        return text
    if len(text) <= 4096:
        # Agent state repeats the same strings step after step; scanning each once is enough.
        return _redact_cached(text, pii)
    return _redact(text, pii)


def _redact(text: str, pii: bool) -> str:
    for kind, value in sorted(find_secrets(text, pii=pii), key=lambda kv: -len(kv[1])):
        text = text.replace(value, _tag(kind, value))
    return text


@functools.lru_cache(maxsize=8192)
def _redact_cached(text: str, pii: bool) -> str:
    return _redact(text, pii)


class Redactor:
    """Applies built-in redaction plus an optional user hook to a snapshot."""

    def __init__(
        self,
        enabled: bool = True,
        pii: bool = False,
        hook: Callable[[Any], Any] | None = None,
    ) -> None:
        self.enabled = enabled
        self.pii = pii
        self.hook = hook

    def __call__(self, value: Any) -> Any:
        if self.enabled:
            value = self._walk(value, None)
        if self.hook is not None:
            value = self.hook(value)
        return value

    def _walk(self, value: Any, key: str | None) -> Any:
        if isinstance(value, str):
            if key is not None and _SENSITIVE_KEY.match(key) and not is_placeholder(value):
                return _tag("kv_secret", value)
            return redact_text(value, pii=self.pii)
        if isinstance(value, dict):
            if value.get(MARK) == "str":
                copy = dict(value)
                copy["head"] = self._walk(str(value.get("head", "")), key)
                return copy
            return {k: self._walk(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [self._walk(item, None) for item in value]
        return value


def redacted_kinds(text: str) -> list[str]:
    """Kinds of redaction markers present in already-redacted text."""
    return REDACTED_RE.findall(text)
