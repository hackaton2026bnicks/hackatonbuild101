"""Remove secrets from text before it is stored or shown to teammates.

Known formats are matched by pattern; anything else that looks like a random token
(long, mixed letters and digits, high entropy) is redacted too.
"""

import math
import re
from collections import Counter

TOKEN = re.compile(r"[A-Za-z0-9+=_-]{32,}")
# Bits per character: hex digests and UUIDs stay below this, base64 secrets go above
ENTROPY_THRESHOLD = 4.3

PATTERNS = [
    (re.compile(r"(?i)(authorization:\s*bearer\s+)[^\s'\"]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}"), "Bearer [REDACTED]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), "[REDACTED:jwt]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), "[REDACTED:key]"),
    (re.compile(r"\bb101_[A-Za-z0-9_-]{16,}"), "[REDACTED:key]"),
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "[REDACTED:aws]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "[REDACTED:github]"),
    (
        re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)(\s*[=:]\s*)[^\s'\"&]+"),
        r"\1\2[REDACTED]",
    ),
    # Credentials embedded in URLs: https://user:pass@host
    (re.compile(r"(://[^:/\s@]+:)[^@/\s]+@"), r"\1[REDACTED]@"),
]


def entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(n / len(text) * math.log2(n / len(text)) for n in counts.values())


def looks_random(token: str) -> bool:
    has_letters = any(c.isalpha() for c in token)
    has_digits = any(c.isdigit() for c in token)
    return has_letters and has_digits and entropy(token) >= ENTROPY_THRESHOLD


def scrub(text: str, limit: int | None = None) -> str:
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    text = TOKEN.sub(lambda m: "[REDACTED:secret]" if looks_random(m.group()) else m.group(), text)
    if limit is not None and len(text) > limit:
        text = text[: limit - 1] + "…"
    return text
