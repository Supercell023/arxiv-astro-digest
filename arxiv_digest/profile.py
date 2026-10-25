"""读者画像：把 `config/profile.yaml` 读成一个强类型对象。

画像不是"我喜欢什么"的形容词列表，而是一份**输出契约**：
它规定评论按什么顺序回答哪些问题、什么情况下允许说"不知道"。
所以它落在 YAML 里、由这里校验，而不是散在 prompt 的一段散文里。

本模块只做两件事：读文件、渲染成 prompt 片段。
**任何异常都不向上抛** —— 画像坏了，这封信应该降级为"通用摘要"，
而不是整封不发（和 calendar.py 一样的降级立场）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

DEFAULT_PROFILE_PATH = "config/profile.yaml"

# 画像缺失时的兜底问题顺序：与 docs/PRODUCT_PLAN.md §5 一致。
_FALLBACK_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("data", "用了什么数据？样本量、红移区间、质量或光度区间"),
    ("processing", "怎么筛选和处理的？关键 cut 与完备性"),
    ("systematics", "怎么校正误差、提取信号的？统计误差与系统误差分别怎么处理"),
    ("method", "用什么方法、研究什么问题？估计量、建模、拟合设定、协方差"),
    ("result", "得到什么新结果？与既有约束是否一致，误差棒从哪来"),
)

_FALLBACK_FIELDS: tuple[tuple[str, str], ...] = (
    ("data", "数据集类型、样本量、红移与质量区间"),
    ("processing", "关键 cut 与处理步骤"),
    ("systematics", "系统误差来源与校正手段"),
    ("method", "估计量 + 建模 + 拟合设定 + 协方差"),
    ("result", "新结果与误差棒来源"),
    ("caveat", "作者自己承认的局限"),
    ("for_you", "一句话：这条能怎么用到我手上的工作里"),
)


def _text(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        return " ".join(value.split())
    return default


def _int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return default
    return default


def _str_tuple(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(text for item in value if (text := _text(item)))


def _pairs(value: Any, *, key_name: str, value_name: str) -> tuple[tuple[str, str], ...]:
    """把 `[{key: ..., ask: ...}, ...]` 读成 `((key, ask), ...)`。"""
    if not isinstance(value, (list, tuple)):
        return ()
    pairs: list[tuple[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        key = _text(item.get(key_name))
        text = _text(item.get(value_name))
        if key and text:
            pairs.append((key, text))
    return tuple(pairs)


def _mapping(value: Any) -> tuple[tuple[str, str], ...]:
    """把 `{key: 描述}` 读成有序 pairs（保持 YAML 里的书写顺序）。"""
    if not isinstance(value, dict):
        return ()
    return tuple((str(key), _text(text)) for key, text in value.items() if _text(text))


@dataclass(frozen=True)
class Profile:
    """读者画像。所有字段都有默认值，坏文件也能构造出一个可用的画像。"""

    role: str = "researcher"
    discipline: str = "天文学"
    background: str = "研究生级背景"
    tone: str = "直接、具体"
    titles: str = "zh_with_original"
    commentary: str = "zh"
    keep_terms: tuple[str, ...] = ()
    minutes_per_day: int = 20
    deep_papers: int = 4
    brief_papers: int = 10
    data: tuple[str, ...] = ()
    probes_primary: tuple[str, ...] = ()
    probes_secondary: tuple[str, ...] = ()
    methods: tuple[str, ...] = ()
    questions: tuple[tuple[str, str], ...] = field(default=_FALLBACK_QUESTIONS)
    never: tuple[str, ...] = ()
    analysis_fields: tuple[tuple[str, str], ...] = field(default=_FALLBACK_FIELDS)
    depth_deep: str = "每项 40-90 字，凡是数字必须来自原文"
    depth_brief: str = "只给 result 与 for_you，各不超过 40 字"
    relevance_kinds: tuple[tuple[str, str], ...] = ()
    version: int = 1
    source_path: str = ""

    # —— 给下游用的派生属性 ——
    @property
    def question_keys(self) -> tuple[str, ...]:
        return tuple(key for key, _ in self.questions)

    @property
    def relevance_kind_keys(self) -> tuple[str, ...]:
        return tuple(key for key, _ in self.relevance_kinds)

    @property
    def is_empty(self) -> bool:
        """没有 source_path 说明读文件失败，用的是内置兜底。"""
        return not self.source_path

    def analysis_field_keys(self) -> tuple[str, ...]:
        return tuple(key for key, _ in self.analysis_fields)

    def render_prompt_block(self) -> str:
        """渲染成 prompt 里的「读者画像」段落。

        刻意不包含 minutes_per_day / deep_papers 这类排版参数：
        模型不负责决定邮件长短，那是渲染层的事。
        """
        lines: list[str] = []
        lines.append(f"身份：{self.role}")
        lines.append(f"方向：{self.discipline}")
        if self.background:
            lines.append(f"背景（这条以下不用解释）：{self.background}")
        if self.tone:
            lines.append(f"语气：{self.tone}")
        if self.keep_terms:
            lines.append("保留英文不翻译的术语：" + "、".join(self.keep_terms))
        if self.data:
            lines.append("手上的数据：" + "；".join(self.data))
        if self.probes_primary or self.probes_secondary:
            primary = "、".join(self.probes_primary) or "（未设定）"
            secondary = "、".join(self.probes_secondary) or "（未设定）"
            lines.append(f"主探针：{primary}；交叉关注：{secondary}")
        if self.methods:
            lines.append("常用方法：" + "；".join(self.methods))
        if self.questions:
            lines.append("读一篇论文时，必须按这个顺序回答：")
            for index, (key, ask) in enumerate(self.questions, start=1):
                lines.append(f"  {index}. [{key}] {ask}")
        if self.never:
            lines.append("不要给出：" + "；".join(self.never))
        if self.relevance_kinds:
            lines.append("相关类型只能取这些值：")
            for key, desc in self.relevance_kinds:
                lines.append(f"  - {key}：{desc}")
        return "\n".join(lines)


def empty_profile() -> Profile:
    return Profile()


def load_profile(path: str | Path | None = DEFAULT_PROFILE_PATH) -> Profile:
    """读画像文件；任何失败都退化成 `empty_profile()` 并记一条 warning。"""
    if path is None:
        return empty_profile()

    resolved = Path(path)
    if not resolved.is_absolute():
        for base in (Path.cwd(), Path(__file__).resolve().parent.parent):
            candidate = base / resolved
            if candidate.exists():
                resolved = candidate
                break

    try:
        import yaml
    except ImportError:
        LOGGER.warning("pyyaml 未安装，读者画像退化为内置默认值。")
        return empty_profile()

    try:
        raw = yaml.safe_load(resolved.read_text(encoding="utf-8"))
    except Exception as exc:
        LOGGER.warning("读取读者画像失败（%s）：%s，退化为默认值。", resolved, exc)
        return empty_profile()

    if not isinstance(raw, dict):
        LOGGER.warning("读者画像不是 mapping（%s），退化为默认值。", resolved)
        return empty_profile()

    return parse_profile(raw, source_path=str(resolved))


def parse_profile(raw: dict[str, Any], *, source_path: str = "") -> Profile:
    """把 YAML mapping 解析成 Profile（独立出来后可以直接写单测）。"""
    identity = raw.get("identity") if isinstance(raw.get("identity"), dict) else {}
    language = raw.get("language") if isinstance(raw.get("language"), dict) else {}
    reading = raw.get("reading") if isinstance(raw.get("reading"), dict) else {}
    work = raw.get("work") if isinstance(raw.get("work"), dict) else {}
    probes = work.get("probes") if isinstance(work.get("probes"), dict) else {}
    analysis = raw.get("analysis") if isinstance(raw.get("analysis"), dict) else {}
    depth = analysis.get("depth") if isinstance(analysis.get("depth"), dict) else {}

    questions = _pairs(raw.get("questions"), key_name="key", value_name="ask") or _FALLBACK_QUESTIONS
    fields = _mapping(analysis.get("fields")) or _FALLBACK_FIELDS

    return Profile(
        role=_text(identity.get("role"), "researcher"),
        discipline=_text(identity.get("field"), "天文学"),
        background=_text(identity.get("background"), "研究生级背景"),
        tone=_text(identity.get("tone"), "直接、具体"),
        titles=_text(language.get("titles"), "zh_with_original"),
        commentary=_text(language.get("commentary"), "zh"),
        keep_terms=_str_tuple(language.get("keep_terms")),
        minutes_per_day=_int(reading.get("minutes_per_day"), 20),
        deep_papers=_int(reading.get("deep_papers"), 4),
        brief_papers=_int(reading.get("brief_papers"), 10),
        data=_str_tuple(work.get("data")),
        probes_primary=_str_tuple(probes.get("primary")),
        probes_secondary=_str_tuple(probes.get("secondary")),
        methods=_str_tuple(work.get("methods")),
        questions=questions,
        never=_str_tuple(raw.get("never")),
        analysis_fields=fields,
        depth_deep=_text(depth.get("deep"), "每项 40-90 字，凡是数字必须来自原文"),
        depth_brief=_text(depth.get("brief"), "只给 result 与 for_you，各不超过 40 字"),
        relevance_kinds=_mapping(analysis.get("relevance_kind") or raw.get("relevance_kind")),
        version=_int(raw.get("version"), 1),
        source_path=source_path,
    )
