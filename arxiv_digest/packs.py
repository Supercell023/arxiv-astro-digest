"""领域包：把"某个学科怎么算做得扎实"从代码里搬到 YAML 里。

一个包 = 一组 arXiv 分类 + 一套评价标准（focus / red_flags）+ 该领域的源。
这样加一个学科只需要加一个 YAML 文件，不用改 Python。

分派规则刻意做成可解释的三级强度（见 `_match_strength`）：
主分类命中 > 包内任一分类命中 > 前缀命中，最后才落到 generic 兜底包。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

LOGGER = logging.getLogger(__name__)

DEFAULT_PACKS_DIR = "config/packs"
GENERIC_PACK_ID = "generic"

# 匹配强度：数值越大越优先
_MATCH_PRIMARY = 3
_MATCH_ANY = 2
_MATCH_PREFIX = 1
_MATCH_NONE = 0


def _text(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return " ".join(value.split())
    return default


def _str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(text for item in value if (text := _text(item)))


def _capability(value: Any) -> str:
    text = _text(value, "low").lower()
    return text if text in {"high", "medium", "low"} else "low"


def _topic_tuple(value: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """解析 `pulse.topics`：`- {label: 弱透镜, aliases: [weak lensing, ...]}`。

    也接受 `- [标签, [别名...]]` 的简写，方便手写。
    """

    if not isinstance(value, (list, tuple)):
        return ()
    topics: list[tuple[str, tuple[str, ...]]] = []
    for entry in value:
        label = ""
        aliases: tuple[str, ...] = ()
        if isinstance(entry, dict):
            label = _text(entry.get("label"))
            aliases = _str_tuple(entry.get("aliases") or entry.get("alias"))
        elif isinstance(entry, (list, tuple)) and len(entry) >= 2:
            label = _text(entry[0])
            aliases = _str_tuple(entry[1])
        if label and aliases:
            topics.append((label, tuple(alias.lower() for alias in aliases)))
    return tuple(topics)


@dataclass(frozen=True)
class Pack:
    id: str
    name: str = ""
    capability: str = "low"
    categories: tuple[str, ...] = ()
    focus: tuple[str, ...] = ()
    red_flags: tuple[str, ...] = ()
    title_hint: str = ""
    reader_context: str = ""
    sources_secondary: tuple[str, ...] = ()
    sources_events: tuple[str, ...] = ()
    topics: tuple[tuple[str, tuple[str, ...]], ...] = ()
    source_path: str = ""

    @property
    def is_generic(self) -> bool:
        return self.id == GENERIC_PACK_ID

    @property
    def display_name(self) -> str:
        return self.name or self.id

    def render_prompt_block(self) -> str:
        """渲染成 prompt 里的「领域评价标准」段落。"""
        lines = [f"领域：{self.display_name}（能力等级 {self.capability}）"]
        if self.reader_context:
            lines.append(f"读者处境：{self.reader_context}")
        if self.focus:
            lines.append("做到这些才算扎实：")
            lines.extend(f"  - {item}" for item in self.focus)
        if self.red_flags:
            lines.append("出现这些就该扣分（写进 caveat）：")
            lines.extend(f"  - {item}" for item in self.red_flags)
        return "\n".join(lines)


def _parse_pack(raw: dict[str, Any], *, source_path: str = "") -> Pack | None:
    pack_id = _text(raw.get("id"))
    if not pack_id:
        LOGGER.warning("领域包缺少 id（%s），已跳过。", source_path or "?" )
        return None

    evaluation = raw.get("evaluation") if isinstance(raw.get("evaluation"), dict) else {}
    sources = raw.get("sources") if isinstance(raw.get("sources"), dict) else {}
    pulse = raw.get("pulse") if isinstance(raw.get("pulse"), dict) else {}

    return Pack(
        id=pack_id,
        name=_text(raw.get("name"), pack_id),
        capability=_capability(raw.get("capability")),
        categories=_str_tuple(raw.get("arxiv_categories")),
        focus=_str_tuple(evaluation.get("focus")),
        red_flags=_str_tuple(evaluation.get("red_flags")),
        title_hint=_text(raw.get("title_hint")),
        reader_context=_text(raw.get("reader_context")),
        sources_secondary=_str_tuple(sources.get("secondary")),
        sources_events=_str_tuple(sources.get("events")),
        topics=_topic_tuple(pulse.get("topics")),
        source_path=source_path,
    )


def _resolve_dir(directory: str | Path | None) -> Path | None:
    if directory is None:
        return None
    resolved = Path(directory)
    if resolved.is_absolute():
        return resolved if resolved.is_dir() else None
    for base in (Path.cwd(), Path(__file__).resolve().parent.parent):
        candidate = base / resolved
        if candidate.is_dir():
            return candidate
    return None


def load_packs(directory: str | Path | None = DEFAULT_PACKS_DIR) -> tuple[Pack, ...]:
    """读取目录下所有 *.yaml 领域包；任何失败都返回空元组，不抛异常。"""
    resolved = _resolve_dir(directory)
    if resolved is None:
        LOGGER.warning("领域包目录不存在（%s），将只使用 generic 逻辑。", directory)
        return ()

    try:
        import yaml
    except ImportError:
        LOGGER.warning("pyyaml 未安装，无法读取领域包。")
        return ()

    packs: list[Pack] = []
    for path in sorted(resolved.glob("*.yaml")) + sorted(resolved.glob("*.yml")):
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except Exception as exc:
            LOGGER.warning("读取领域包失败（%s）：%s", path, exc)
            continue
        if not isinstance(raw, dict):
            continue
        pack = _parse_pack(raw, source_path=str(path))
        if pack is not None:
            packs.append(pack)

    # 分类多的包更具体？不。这里只保证输出稳定：非 generic 在前，再按 id 排序。
    packs.sort(key=lambda pack: (pack.is_generic, pack.id))
    return tuple(packs)


def select_packs(packs: Sequence[Pack], fields: Iterable[str] | None = None) -> tuple[Pack, ...]:
    """按 `FIELDS=galaxy_cosmology,galaxy_formation` 勾选。

    - fields 为空 → 返回全部非 generic 包（全部学科）；
    - fields 里有未知 id → 记 warning 并忽略；
    - 勾了但一个都没匹配上 → 返回全部非 generic 包（宁可多，不可空）。
    """
    wanted = [item.strip() for item in (fields or ()) if item and item.strip()]
    if not wanted:
        return tuple(pack for pack in packs if not pack.is_generic)

    by_id = {pack.id: pack for pack in packs}
    unknown = [field_name for field_name in wanted if field_name not in by_id]
    if unknown:
        LOGGER.warning("FIELDS 里有未知领域包：%s（可用：%s）", ", ".join(unknown), ", ".join(sorted(by_id)))

    selected = tuple(by_id[name] for name in wanted if name in by_id)
    if selected:
        return selected
    return tuple(pack for pack in packs if not pack.is_generic)


def _match_strength(pack: Pack, categories: Sequence[str]) -> int:
    if not pack.categories or not categories:
        return _MATCH_NONE
    primary = pack.categories[0]
    if categories[0] == primary:
        return _MATCH_PRIMARY
    if primary in categories:
        return _MATCH_ANY
    for category in categories:
        for pack_category in pack.categories:
            if category == pack_category:
                return _MATCH_ANY
            if category.startswith(pack_category + ".") or pack_category.startswith(category + "."):
                return _MATCH_PREFIX
    return _MATCH_NONE


def dispatch(packs: Sequence[Pack], categories: Sequence[str]) -> Pack | None:
    """给一篇论文（按它自己的分类）挑一个最合适的包。"""
    best: Pack | None = None
    best_strength = _MATCH_NONE
    for pack in packs:
        if pack.is_generic:
            continue
        strength = _match_strength(pack, categories)
        if strength > best_strength:
            best, best_strength = pack, strength
    if best is not None:
        return best
    for pack in packs:
        if pack.is_generic:
            return pack
    return None


def collect_categories(packs: Sequence[Pack], limit: int = 0) -> tuple[str, ...]:
    """把勾选包的分类取并集，保持书写顺序；用于拼 ARXIV_CATEGORIES。"""
    seen: dict[str, None] = {}
    for pack in packs:
        for category in pack.categories:
            seen.setdefault(category, None)
    categories = list(seen)
    if limit > 0:
        categories = categories[:limit]
    return tuple(categories)


def collect_sources(packs: Sequence[Pack], kind: str) -> tuple[str, ...]:
    """把勾选包的某类源取并集：kind ∈ {secondary, events}。"""
    seen: dict[str, None] = {}
    for pack in packs:
        items = pack.sources_secondary if kind == "secondary" else pack.sources_events
        for item in items:
            seen.setdefault(item, None)
    return tuple(seen)
