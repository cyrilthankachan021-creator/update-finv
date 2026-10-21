"""Normalisation helpers used by duplicate detection, matching and ingestion.

Normalisation removes formatting noise (case, separators, leading zeros inside numeric
groups) but keeps the meaningful digits and letters, so 'INV-001' == 'inv001' while
'INV-001' != 'INV-0010' and '2024/1' != '20241'... see tests for the exact contract.
"""
from __future__ import annotations

import hashlib
import re

_TOKEN = re.compile(r"[A-Z]+|\d+")


def norm_doc_number(value: str | None) -> str | None:
    if value is None:
        return None
    s = str(value).upper().strip()
    if not s:
        return None
    # tokens split on any separator; digit groups lose leading zeros; token boundaries kept
    tokens = []
    for t in _TOKEN.findall(s):
        tokens.append(str(int(t)) if t.isdigit() else t)
    if not tokens:
        return None
    # adjacent alpha+digit tokens are glued ('INV-001' and 'INV001' both -> 'INV1'),
    # but two adjacent digit groups keep a separator ('2024/01' -> '2024.1').
    out = tokens[0]
    for prev, t in zip(tokens, tokens[1:]):
        out += ("." if prev.isdigit() and t.isdigit() else "") + t
    return out


_PARTY_NOISE = re.compile(
    r"\b(M/S|MS|MESSRS|PVT|PRIVATE|LTD|LIMITED|LLP|CO|COMPANY|INC|THE|AND|&)\b|[^A-Z0-9 ]"
)


def norm_party(name: str | None) -> str | None:
    if not name:
        return None
    s = _PARTY_NOISE.sub(" ", name.upper())
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def norm_text(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9 ]", " ", text.upper())).strip()


def tokens(text: str | None, min_len: int = 3) -> set[str]:
    return {t for t in norm_text(text).split() if len(t) >= min_len}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fingerprint(*parts) -> str:
    joined = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(joined.encode()).hexdigest()


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]
