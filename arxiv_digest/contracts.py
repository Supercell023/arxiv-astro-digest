"""Versioned, lossless paper snapshots used by export, replay and feedback."""
from __future__ import annotations

from dataclasses import asdict, fields
from datetime import datetime, timezone
from typing import Any, Mapping

from .models import Analysis, Concept, Fact, KeyNumber, Paper, RedFlag, ScoreBreakdown

DIGEST_VERSION = 3


def _time(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime(1970, 1, 1, tzinfo=timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def analysis_from_dict(raw: Mapping[str, Any] | None) -> Analysis | None:
    if not isinstance(raw, Mapping):
        return None
    values = {f.name: raw[f.name] for f in fields(Analysis) if f.name in raw}
    for name, cls in (("numbers", KeyNumber), ("red_flags", RedFlag), ("concepts", Concept)):
        allowed = {f.name for f in fields(cls)}
        values[name] = tuple(cls(**{k: v for k, v in item.items() if k in allowed})
                             for item in raw.get(name, ()) if isinstance(item, dict))
    for name in ("evidence", "chain", "figures", "actions"):
        values[name] = tuple(str(item) for item in raw.get(name, ()) if isinstance(item, str))
    facts = raw.get("facts") or {}
    values["facts"] = {str(k): Fact(value=v.get("value"), quote=v.get("quote"))
                       for k, v in facts.items() if isinstance(v, dict)} if isinstance(facts, dict) else {}
    return Analysis(**values)


def paper_to_dict(paper: Paper) -> dict[str, Any]:
    raw = asdict(paper)
    raw["published"] = _time(paper.published).isoformat()
    raw["updated"] = _time(paper.updated).isoformat()
    raw["score_breakdown"] = raw.pop("breakdown")
    return raw


def paper_from_dict(raw: Mapping[str, Any]) -> Paper:
    values = {f.name: raw[f.name] for f in fields(Paper) if f.name in raw}
    for key in ("id", "title", "summary", "pdf_url", "abs_url"):
        values[key] = str(raw.get(key) or "")
    values["published"] = _time(raw.get("published"))
    values["updated"] = _time(raw.get("updated") or raw.get("published"))
    values["authors"] = list(raw.get("authors") or [])
    values["categories"] = list(raw.get("categories") or [])
    values["chinese_summary"] = str(raw.get("chinese_summary") or raw.get("summary_zh") or "")
    values["analysis"] = analysis_from_dict(raw.get("analysis"))
    breakdown = raw.get("score_breakdown") or raw.get("breakdown") or {}
    values["breakdown"] = ScoreBreakdown(**{f.name: float(breakdown.get(f.name) or 0)
                                            for f in fields(ScoreBreakdown)})
    return Paper(**values)


def papers_from_payload(payload: Mapping[str, Any]) -> list[Paper]:
    return [paper_from_dict(raw) for raw in payload.get("items", payload.get("papers", []))
            if isinstance(raw, dict) and raw.get("id")]
