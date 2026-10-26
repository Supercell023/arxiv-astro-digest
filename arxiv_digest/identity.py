"""Stable identities shared by collection, delivery and feedback."""
from __future__ import annotations

import hashlib
import re


def canonical_id(value: str) -> str:
    value = str(value or "").strip()
    if value.lower().startswith("doi:"):
        return "doi:" + value[4:].lower()
    value = re.sub(r"^https?://(?:export\.)?arxiv\.org/(?:abs|pdf)/", "", value)
    value = re.sub(r"^arxiv:\s*", "", value, flags=re.I)
    value = re.sub(r"\.pdf$", "", value, flags=re.I)
    if re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z.-]+/\d{7})(?:v\d+)?", value, re.I):
        return re.sub(r"v\d+$", "", value)
    return value


def stable_id(kind: str, source: str, value: str) -> str:
    digest = hashlib.sha256(value.strip().encode("utf-8")).hexdigest()[:24]
    return f"{kind}:{source}:{digest}"
