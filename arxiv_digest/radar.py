"""邻域雷达：别的方向这周在动什么。

设计目标只有一个——**让你能在 60 秒内决定"要不要多花 5 分钟"**。所以：

1. 每个方向收集 2-3 条，但**默认只展开 1 条**，其余折叠在「再看 N 条」后面；
2. 每条都有一句 80-110 字的中文解读（来自标题+摘要，模型写；无模型时用规则句）；
3. 摘要原文附在解读下面，折叠着——想核对再点开；
4. 已经看过的（state.json 里记过）不再出现，避免"每周都是同一篇"。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .models import Paper
from .identity import canonical_id
from .evidence import numbers_supported
from .llm_client import LLMOptions, call_json

LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_PER_FIELD = 3
DEFAULT_MAX_FIELDS = 5
ABSTRACT_PREVIEW = 320

_SIGNAL_WORDS: tuple[tuple[str, int], ...] = (
    ("first detection", 4),
    ("first light", 4),
    ("first results", 4),
    ("breakthrough", 5),
    ("discovery", 3),
    ("discover", 2),
    ("detected", 2),
    ("unprecedented", 4),
    ("new class", 4),
    ("reveals", 2),
    ("reveal", 2),
    ("surprise", 3),
    ("unexpected", 3),
    ("challenge", 2),
    ("tension", 2),
    ("anomalous", 3),
    ("survey of", 1),
    ("catalog", 1),
    ("puzzle", 2),
)

# 每个信号词换成"它意味着什么"，用来拼那句 100 字解读
_RULE_HINTS: tuple[tuple[str, str], ...] = (
    ("first detection", "作者在宣称首次探测到某类现象，值得看它用什么数据把这步坐实"),
    ("first light", "装置或巡天首次出光，通常随后几周会有配套的科学论文出来"),
    ("first results", "首批科学结果，往往是后面一年引用的基准"),
    ("discovery", "标题自带「发现」，先看样本量和显著性，再看有没有独立样本复核"),
    ("unprecedented", "作者用了「前所未有」这类措辞，值得核对它到底新在哪、旧结果差多远"),
    ("new class", "宣称新类别，先看它和已有分类怎么区分、能否被同一套模型解释"),
    ("tension", "带张力的结果，注意是统计显著还是口径/标定差异"),
    ("unexpected", "与预期不符，通常是下一篇论文的起点"),
    ("anomalous", "异常样本，可能是一条系统误差，也可能是一条新物理"),
    ("survey of", "综述/巡天类工作，注意选择函数与完备性"),
    ("catalog", "数据或星表发布，可以直接拿来构造样本"),
)

_UNIT_WORDS = ("%", "km/s", "Mpc", "kpc", "dex", "arcmin", "deg", "sigma", "σ", "z =")
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


@dataclass(frozen=True)
class Neighborhood:
    """一个"邻域"：一组分类 + 一句说明。"""

    id: str
    name: str
    categories: tuple[str, ...]
    blurb: str = ""
    max_items: int = DEFAULT_MAX_PER_FIELD
    enabled: bool = True

    @property
    def label(self) -> str:
        return self.name or self.id


@dataclass(frozen=True)
class RadarHit:
    neighborhood: str
    title: str
    url: str
    category: str
    published: str
    why: str
    reason: str = ""
    summary: str = ""
    abstract: str = ""
    authors: tuple[str, ...] = ()
    id: str = ""

    @property
    def display_summary(self) -> str:
        return self.summary or self.why or self.reason

    @property
    def author_line(self) -> str:
        if not self.authors:
            return ""
        if len(self.authors) <= 2:
            return "、".join(self.authors)
        return f"{self.authors[0]} 等 {len(self.authors)} 人"

    def render_line(self) -> str:
        return f"[{self.neighborhood}] {self.title} — {self.display_summary}"


@dataclass(frozen=True)
class RadarBlock:
    """一个方向：头条一条 + 折叠的其余。"""

    neighborhood: str
    headline: RadarHit | None = None
    rest: tuple[RadarHit, ...] = ()
    total: int = 0

    @property
    def folded_count(self) -> int:
        return len(self.rest)


@dataclass(frozen=True)
class Radar:
    blocks: tuple[RadarBlock, ...] = ()
    counts: tuple[tuple[str, int], ...] = ()
    basis: str = "rule"
    quiet: str = ""
    # 一段话的本周综述：邮件只给这一段，条目列表折叠在下面。
    overview: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.blocks

    @property
    def hits(self) -> tuple[RadarHit, ...]:
        out: list[RadarHit] = []
        for block in self.blocks:
            if block.headline is not None:
                out.append(block.headline)
            out.extend(block.rest)
        return tuple(out)


def _coerce_text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _coerce_categories(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
    elif isinstance(value, Iterable):
        parts = [str(part).strip() for part in value]
    else:
        parts = []
    return tuple(part for part in parts if part)


@dataclass
class _RawNeighborhood:
    id: str = ""
    name: str = ""
    categories: tuple[str, ...] = ()
    blurb: str = ""
    max_items: int = DEFAULT_MAX_PER_FIELD
    enabled: bool = True


def parse_radar(raw: Any) -> list[Neighborhood]:
    """把 YAML 内容解析成邻域列表；坏条目跳过，不抛。"""

    if isinstance(raw, dict):
        blocks = raw.get("neighborhoods") or raw.get("fields") or []
    elif isinstance(raw, list):
        blocks = raw
    else:
        blocks = []
    if not isinstance(blocks, list):
        return []

    result: list[Neighborhood] = []
    for item in blocks:
        if not isinstance(item, dict):
            continue
        name = _coerce_text(item.get("name") or item.get("id"))
        categories = _coerce_categories(item.get("categories") or item.get("arxiv_categories"))
        if not name or not categories:
            continue
        try:
            max_items = int(item.get("max_items") or DEFAULT_MAX_PER_FIELD)
        except (TypeError, ValueError):
            max_items = DEFAULT_MAX_PER_FIELD
        result.append(
            Neighborhood(
                id=_coerce_text(item.get("id")) or name,
                name=name,
                categories=categories,
                blurb=_coerce_text(item.get("blurb")),
                max_items=max(1, max_items),
                enabled=bool(item.get("enabled", True)),
            )
        )
    return result


def _resolve(path: str | Path, fallback_dir: str | Path | None) -> Path | None:
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate if candidate.is_file() else None
    local = Path.cwd() / candidate
    if local.is_file():
        return local
    if fallback_dir:
        alt = Path(fallback_dir).parent / candidate
        if alt.is_file():
            return alt
    return None


def load_radar(path: str | Path, *, fallback_dir: str | Path | None = None) -> list[Neighborhood]:
    target = _resolve(path, fallback_dir)
    if target is None:
        LOGGER.warning("找不到雷达配置：%s", path)
        return []
    try:
        import yaml

        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - 配置坏了不该让整封信挂掉
        LOGGER.warning("读取雷达配置失败（%s）：%s", path, exc)
        return []
    return parse_radar(raw)


def all_categories(neighborhoods: Sequence[Neighborhood]) -> list[str]:
    seen: dict[str, None] = {}
    for neighborhood in neighborhoods:
        if not neighborhood.enabled:
            continue
        for category in neighborhood.categories:
            seen.setdefault(category, None)
    return list(seen)


def _signal_score(title: str) -> int:
    lowered = title.lower()
    return sum(weight for word, weight in _SIGNAL_WORDS if word in lowered)


def _rule_reason(title: str) -> str:
    lowered = title.lower()
    for word, hint in _RULE_HINTS:
        if word in lowered:
            return hint
    return ""


def _fallback_reason(neighborhood: Neighborhood) -> str:
    return f"{neighborhood.label}本周值得留意的一篇"


def _rule_overview(blocks: Sequence[RadarBlock], counts: Sequence[tuple[str, int]]) -> str:
    """没有模型时也要有一段话，而不是一串"扫一眼即可"。"""

    talking = [(name, count) for name, count in counts if count]
    if not blocks:
        return ""
    parts: list[str] = []
    if talking:
        parts.append("本期各方向新投稿：" + "、".join(f"{name} {count} 篇" for name, count in talking))
    heads = [
        f"{block.neighborhood}《{block.headline.title.rstrip('.')}》"
        for block in blocks
        if block.headline is not None
    ]
    if heads:
        parts.append("每个方向挑一条代表工作：" + "；".join(heads))
    parts.append("完整清单折叠在下方，不占精读篇幅")
    return "。".join(parts) + "。"


def _primary_category(paper: Paper) -> str:
    return paper.categories[0] if paper.categories else ""


def _rule_summary(paper: Paper, neighborhood: Neighborhood, reason: str) -> str:
    """无模型时也要给出一句"为什么值得看"，而不是空话。"""

    lead = reason or _fallback_reason(neighborhood)
    body = (paper.summary or "").replace("\n", " ").strip()
    facts: list[str] = []
    for match in _NUMBER_RE.finditer(body):
        window = body[max(0, match.start() - 30) : match.end() + 20]
        if any(word in window for word in (_UNIT_WORDS)):
            facts.append(match.group(0))
        if len(facts) >= 2:
            break
    numbers = f"摘要里给出的关键数字是 {'、'.join(facts)}，核对一下口径。" if facts else ""
    return f"{paper.title.rstrip('.')}：{lead}。{numbers}".strip()


def _abstract_preview(text: str, *, limit: int = ABSTRACT_PREVIEW) -> str:
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[:limit]
    for sep in (". ", "; "):
        index = cut.rfind(sep)
        if index > limit * 0.6:
            return cut[:index].strip() + "…"
    return cut.strip() + "…"


def select_blocks(
    neighborhoods: Sequence[Neighborhood],
    papers: Sequence[Paper],
    *,
    per_field: int = DEFAULT_MAX_PER_FIELD,
    max_fields: int = DEFAULT_MAX_FIELDS,
    skip_ids: Sequence[str] = (),
) -> Radar:
    """每个邻域挑 per_field 条（第一条展示，其余折叠）。"""

    if not neighborhoods or not papers:
        return Radar()

    skip = {canonical_id(value) for value in skip_ids}
    by_category: dict[str, list[Paper]] = {}
    for paper in papers:
        if canonical_id(paper.id) in skip:
            continue
        for category in paper.categories:
            by_category.setdefault(category, []).append(paper)

    blocks: list[RadarBlock] = []
    counts: list[tuple[str, int]] = []
    used_ids: set[str] = set()

    for neighborhood in neighborhoods:
        if not neighborhood.enabled:
            continue
        bucket: list[Paper] = []
        matched: dict[str, str] = {}
        seen_ids: set[str] = set()
        for category in neighborhood.categories:
            for paper in by_category.get(category, []):
                if paper.id in seen_ids:
                    continue
                seen_ids.add(paper.id)
                matched[paper.id] = category
                bucket.append(paper)
        counts.append((neighborhood.label, len(bucket)))
        if not bucket:
            continue
        bucket.sort(key=lambda item: (_signal_score(item.title), item.published), reverse=True)

        picked: list[RadarHit] = []
        limit = max(1, per_field or neighborhood.max_items)
        for paper in bucket:
            # 一条只上一次版面：分类会重叠，重复出现等于骗读者
            if paper.id in used_ids:
                continue
            used_ids.add(paper.id)
            reason = _rule_reason(paper.title) or _fallback_reason(neighborhood)
            picked.append(
                RadarHit(
                    id=paper.id,
                    neighborhood=neighborhood.label,
                    title=paper.title,
                    url=paper.abs_url or paper.pdf_url,
                    category=matched.get(paper.id) or _primary_category(paper),
                    published=str(paper.published)[:10],
                    why=reason,
                    reason=reason,
                    summary=_rule_summary(paper, neighborhood, reason),
                    abstract=_abstract_preview(paper.summary),
                    authors=tuple(paper.authors[:3]),
                )
            )
            if len(picked) >= limit:
                break
        if not picked:
            continue
        blocks.append(
            RadarBlock(
                neighborhood=neighborhood.label,
                headline=picked[0],
                rest=tuple(picked[1:]),
                total=len(bucket),
            )
        )
        if len(blocks) >= max_fields:
            break

    return Radar(
        blocks=tuple(blocks),
        counts=tuple(counts),
        basis="rule",
        overview=_rule_overview(blocks, counts),
    )


def select_hits(
    neighborhoods: Sequence[Neighborhood],
    papers: Sequence[Paper],
    *,
    max_fields: int = DEFAULT_MAX_FIELDS,
    per_field: int = DEFAULT_MAX_PER_FIELD,
    skip_ids: Sequence[str] = (),
) -> Radar:
    return select_blocks(
        neighborhoods,
        papers,
        per_field=per_field,
        max_fields=max_fields,
        skip_ids=skip_ids,
    )


def build_radar_prompt(radar: Radar) -> str:
    blocks = radar.blocks
    lines = [
        "你在帮一位研究星系团与星系宇宙学的博士生，快速判断「别的方向这周发生了什么」。",
        "下面是若干方向的论文，每篇都有标题和摘要开头。",
        "",
    ]
    counter = 0
    for block in blocks:
        lines.append(f"## {block.neighborhood}（本周 {block.total} 篇新投稿）")
        for hit in ([block.headline] if block.headline else []) + list(block.rest):
            counter += 1
            lines.append(f"[{counter}] 标题：{hit.title}")
            if hit.abstract:
                lines.append(f"    摘要：{hit.abstract[:260]}")
        lines.append("")
    lines.append("写一段中文综述（150-260 字），读者是研究星系团与星系宇宙学的博士生：")
    lines.append("1. 先给各方向本周的投稿量与整体动向；")
    lines.append("2. 再点名 2-3 篇代表工作，用标题里的关键词指代，说明为什么值得看；")
    lines.append("3. 不要逐篇罗列，不要写「扫一眼即可」这类空话。")
    lines.append("")
    lines.append("硬规则：")
    lines.append("1. 只用标题和摘要里出现的信息，不要编造数字、样本量或结论；")
    lines.append("2. 不要复述标题；")
    lines.append("3. 摘要不足以判断就直说「摘要看不出方法细节」，不要补空话。")
    lines.append("")
    lines.append('输出 JSON：{"overview": "…"}')
    return "\n".join(lines)


def _chat_json(
    client: Any,
    model: str,
    prompt: str,
    *,
    options: LLMOptions | None = None,
) -> dict[str, Any]:
    return call_json(
        client,
        model,
        prompt,
        options=options or LLMOptions(temperature=0.3, max_tokens=3000),
    )


def annotate_radar(
    client: Any,
    model: str,
    radar: Radar,
    *,
    timeout_hint: str = "",
    options: LLMOptions | None = None,
) -> Radar:
    """有模型时把规则解读改写成 80-110 字的人话；失败保留规则句。"""

    if radar.is_empty or client is None:
        return radar
    try:
        payload = _chat_json(client, model, build_radar_prompt(radar), options=options)
    except Exception as exc:  # noqa: BLE001 - 雷达是配菜，失败不该影响主食
        LOGGER.warning("邻域雷达改写失败，保留规则解读：%s", exc)
        return radar

    raw_items = payload.get("items")
    mapping: dict[int, str] = {}
    for raw in raw_items if isinstance(raw_items, list) else []:
        if not isinstance(raw, dict):
            continue
        try:
            index = int(raw.get("index"))
        except (TypeError, ValueError):
            continue
        text = _coerce_text(raw.get("summary"))
        if text:
            mapping[index] = text

    source = " ".join(
        f"{block.neighborhood} {block.total} 篇 "
        + " ".join(f"{hit.title} {hit.abstract}" for hit in ([block.headline] if block.headline else []) + list(block.rest))
        for block in radar.blocks
    )
    raw_overview = _coerce_text(payload.get("overview"))
    overview = radar.overview
    if raw_overview and numbers_supported(raw_overview, source):
        overview = raw_overview

    counter = 0
    updated: list[RadarBlock] = []
    for block in radar.blocks:
        headline = block.headline
        if headline is not None:
            counter += 1
            text = mapping.get(counter, "")
            if text:
                headline = _replace_hit(headline, summary=text)
        rest: list[RadarHit] = []
        for hit in block.rest:
            counter += 1
            text = mapping.get(counter, "")
            rest.append(_replace_hit(hit, summary=text) if text else hit)
        updated.append(
            RadarBlock(
                neighborhood=block.neighborhood,
                headline=headline,
                rest=tuple(rest),
                total=block.total,
            )
        )
    return Radar(blocks=tuple(updated), counts=radar.counts, basis="model", quiet=radar.quiet, overview=overview)


def _replace_hit(hit: RadarHit, *, summary: str) -> RadarHit:
    return RadarHit(
        neighborhood=hit.neighborhood,
        title=hit.title,
        url=hit.url,
        category=hit.category,
        published=hit.published,
        why=hit.why,
        reason=hit.reason,
        summary=summary,
        abstract=hit.abstract,
        authors=hit.authors,
        id=hit.id,
    )


def format_plain_text(radar: Radar) -> str:
    if radar.is_empty:
        return "（本周没有其它方向的条目）"
    lines: list[str] = []
    for block in radar.blocks:
        if block.headline is None:
            continue
        lines.append(f"[{block.neighborhood}] {block.headline.title}（{block.headline.published}）")
        lines.append(f"    {block.headline.display_summary}")
        for hit in block.rest:
            lines.append(f"    · {hit.title}（{hit.published}）")
    if radar.counts:
        lines.append("各方向投稿量：" + "，".join(f"{name} {count}" for name, count in radar.counts))
    return "\n".join(lines)
