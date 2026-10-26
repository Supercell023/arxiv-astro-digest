"""离线自检：不需要联网、不需要 API key、不发邮件。

用途：每次改完代码，先跑这一条命令，确认"能导入、能解析、能渲染"，
再去跑真实的 run_pipeline。它替代了单元测试在这一阶段的部分作用。

    python -m arxiv_digest.selfcheck
    python -m arxiv_digest.selfcheck --write-preview preview.html
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from .config import load_config
from .emailer import render_email_html, render_error_email_html
from .models import Paper

PASS = "[ OK ]"
FAIL = "[FAIL]"

_checks: list[tuple[str, bool, str]] = []


def _check(name: str, func) -> None:
    try:
        detail = func() or ""
    except Exception:
        _checks.append((name, False, traceback.format_exc(limit=3)))
        return
    _checks.append((name, True, str(detail)))


def _fake_papers() -> list[Paper]:
    now = datetime(2026, 10, 24, 20, 0)
    return [
        Paper(
            id=f"2610.{index:05d}",
            title=f"Mock paper {index}: galaxy cluster mass calibration with weak lensing",
            authors=["A. Author", "B. Author", "C. Author"],
            summary="We measure the cluster mass-observable relation using a mock survey.",
            published=now,
            updated=now,
            categories=["astro-ph.CO", "astro-ph.GA"],
            pdf_url=f"https://arxiv.org/pdf/2610.{index:05d}",
            abs_url=f"https://arxiv.org/abs/2610.{index:05d}",
            score=6.0 - index * 0.1,
            rating=4 if index <= 4 else 3,
            score_reasons=["关键词命中：cluster"],
            chinese_summary="这是自检用的占位中文摘要，用来验证模板渲染路径。",
        )
        for index in range(14)
    ]


def _check_imports() -> str:
    from . import calendar, config, emailer, llm, models, recommender  # noqa: F401

    return "config/models/calendar/emailer/llm/recommender 均可导入"


def _check_third_party_deps() -> str:
    """缺依赖时给出明确指令，而不是一堆 ImportError 堆栈。"""
    missing: list[str] = []
    for module_name, package in (
        ("jinja2", "jinja2"),
        ("markupsafe", "jinja2"),
        ("dotenv", "python-dotenv"),
        ("yaml", "pyyaml"),
        ("openai", "openai"),
    ):
        try:
            __import__(module_name)
        except ImportError:
            missing.append(package)
    if missing:
        raise RuntimeError(f"缺少依赖：{', '.join(sorted(set(missing)))}；先跑 pip install -r requirements.txt")
    pipeline_ok = "未检查"
    try:
        from .pipeline import run_pipeline  # noqa: F401

        pipeline_ok = "pipeline 可导入"
    except Exception as exc:
        raise RuntimeError(f"pipeline 导入失败：{exc}") from exc
    return f"jinja2/pyyaml/openai/dotenv 齐全，{pipeline_ok}"


def _check_default_config() -> str:
    cfg = load_config()
    assert cfg.llm_batch_size >= 1, f"LLM_BATCH_SIZE 必须 >= 1，当前 {cfg.llm_batch_size}"
    assert cfg.max_papers >= 1
    note = ""
    if cfg.deep_papers + cfg.brief_papers > cfg.max_papers:
        note = f"（注意：精读+速览={cfg.deep_papers + cfg.brief_papers} > MAX_PAPERS={cfg.max_papers}，多的部分不会渲染）"
    return (
        f"max_papers={cfg.max_papers} 批大小={cfg.llm_batch_size} "
        f"精读={cfg.deep_papers} 速览={cfg.brief_papers}{note}"
    )


def _check_json_parsing() -> str:
    from .llm import _parse_json_object

    plain = '{"paper_summaries": {"2610.00001": "摘要"}}'
    fenced = '```json\n{"paper_summaries": {"2610.00001": "摘要"}}\n```'
    chatty = '好的，结果如下：\n{"paper_summaries": {"2610.00001": "摘要"}}\n希望有帮助。'
    for label, text in (("纯 JSON", plain), ("围栏", fenced), ("前后说明", chatty)):
        payload = _parse_json_object(text)
        assert payload.get("paper_summaries"), f"{label} 解析后缺少 paper_summaries"

    # 真正的坏输入必须抛错，而不是被静默吞掉
    try:
        _parse_json_object("这里根本没有 JSON")
    except ValueError:
        pass
    else:
        raise AssertionError("坏输入没有抛 ValueError")
    return "3 种容错格式通过，坏输入正确抛错"


def _check_batching() -> str:
    from .llm import _chunk

    papers = _fake_papers()
    for size in (1, 5, 14):
        batches = _chunk(papers, size)
        total = sum(len(batch) for batch in batches)
        assert total == len(papers), f"batch_size={size} 时总数不对"
        assert len(batches) == max(1, -(-len(papers) // size)), f"batch_size={size} 时批次数不对"
    assert _chunk(papers, 0)[0], "batch_size=0 应该被当成 1 处理"
    return "1/5/14 三种批大小均正确切分，0 退化安全"


def _check_fallback_path() -> str:
    from .llm import generate_digest_content

    papers = _fake_papers()
    summary, enriched = generate_digest_content(None, "unused-model", papers, max_llm_papers=14)
    assert isinstance(summary, str) and summary.strip()
    assert len(enriched) == len(papers)
    assert all(paper.chinese_summary for paper in enriched)
    return "无 API key 时 14 篇全部走降级且有中文占位"


def _check_calendar() -> str:
    from .calendar import format_plain_text, load_calendar

    cfg = load_config()
    bundle = load_calendar(
        cfg.calendar_events_path,
        cfg.calendar_conferences_path,
        horizon_days=cfg.calendar_horizon_days,
        recent_days=cfg.calendar_recent_days,
    )
    text = format_plain_text(bundle)
    assert isinstance(text, str)
    if bundle.is_empty:
        return "日历为空（未报错，降级路径正常）"
    return f"读到 {len(bundle.rows)} 条近期日程：\n" + "\n".join("  " + line for line in text.splitlines()[:6])


def _check_profile() -> str:
    from .profile import load_profile

    profile = load_profile("config/profile.yaml")
    assert not profile.is_empty, "画像文件没读到，已退化成内置兜底（检查 config/profile.yaml）"
    assert profile.questions, "读者问题清单为空"
    keys = profile.analysis_field_keys()
    expected = {"data", "processing", "systematics", "method", "result", "caveat", "for_you"}
    missing = expected - set(keys)
    assert not missing, f"输出字段缺：{sorted(missing)}"
    assert profile.relevance_kinds, "relevance_kind 为空"
    block = profile.render_prompt_block()
    assert "读一篇论文时" in block, "画像渲染块里没有问题清单"
    assert profile.role and profile.discipline
    return (
        f"角色={profile.role} 方向={profile.discipline} 问题={len(profile.questions)} 条 "
        f"输出字段={len(keys)} 个 相关类型={len(profile.relevance_kinds)} 种"
    )


def _check_packs() -> str:
    from .packs import collect_categories, dispatch, load_packs, select_packs

    packs = load_packs("config/packs")
    assert packs, "没有读到任何领域包"
    ids = {pack.id for pack in packs}
    spec = f"{len(packs)} 个包（{', '.join(sorted(ids))}）"

    co = dispatch(packs, ["astro-ph.CO", "astro-ph.GA"])
    assert co is not None and co.id == "galaxy_cosmology", f"astro-ph.CO 分派到了 {co.id if co else None}"
    ga = dispatch(packs, ["astro-ph.GA", "astro-ph.CO"])
    assert ga is not None and ga.id == "galaxy_formation", f"astro-ph.GA 分派到了 {ga.id if ga else None}"
    other = dispatch(packs, ["hep-th"])
    assert other is not None and other.is_generic, "未归类论文没有落到 generic 兜底包"

    selected = select_packs(packs, ["galaxy_cosmology"])
    assert [pack.id for pack in selected] == ["galaxy_cosmology"], "FIELDS 勾选没有精确命中"
    assert select_packs(packs, ["nonexistent"]) == tuple(p for p in packs if not p.is_generic), (
        "FIELDS 全都不认识时，应回退为全部非 generic 包"
    )

    categories = collect_categories(packs)
    for expected_cat in ("astro-ph.CO", "astro-ph.GA"):
        assert expected_cat in categories, f"并集里缺 {expected_cat}"
    return f"{spec}；主分类优先分派正确，未归类落到 generic；并集 {len(categories)} 个分类"


class _FakeCompletions:
    """假客户端：按顺序返回预设 JSON，用来在不联网的情况下跑通两阶段。"""

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)

        class _Message:
            content = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]

        class _Choice:
            message = _Message()

        class _Response:
            choices = [_Choice()]

        return _Response()


class _FakeClient:
    def __init__(self, responses: list[str]) -> None:
        self.chat = type("Chat", (), {"completions": _FakeCompletions(responses)})()


_EXTRACT_JSON = """{
  "data": {"value": "模拟星团样本，红移 0.05-0.3", "quote": "we use a mock cluster sample at 0.05 < z < 0.3"},
  "processing": {"value": "按 richness 选样", "quote": "clusters are selected by richness"},
  "systematics": null,
  "method": {"value": "弱透镜标定质量", "quote": "we calibrate mass with weak lensing"},
  "result": {"value": "质量偏差小于 10%", "quote": "the mass bias is below 10 per cent"},
  "caveat": null,
  "motivation": {"value": "richness 选样的标定仍不确定", "quote": "richness selection remains uncertain"},
  "numbers": [
    {"label": "样本量", "value": "12 万星系", "quote": "a sample of 120,000 galaxies"},
    {"label": "红移范围", "value": "0.05 < z < 0.3", "quote": "0.05 < z < 0.3"}
  ],
  "concepts": [{"term": "richness", "gloss": "星系团成员星系数的光学代理量"}]
}"""

_ASSESS_JSON = """{
  "relevance": 0.88,
  "relevance_kind": "direct",
  "relevance_reason": "直接用星系团做质量标定",
  "data": "mock 星团样本，0.05<z<0.3",
  "processing": "按 richness 选样",
  "systematics": "摘要未提",
  "method": "弱透镜质量标定",
  "result": "质量偏差 <10%",
  "caveat": "摘要未提",
  "for_you": "可以把它的 richness 选样流程接到你的 mock catalog 上",
  "confidence": "medium",
  "evidence": ["we use a mock cluster sample at 0.05 < z < 0.3"],
  "basis": "abstract",
  "verdict": "用 mock 星团检验弱透镜质量标定，偏差小于 10%",
  "read_advice": "read",
  "read_reason": "标定流程可直接复用",
  "background": "星系团质量标定的系统误差直接决定宇宙学参数的可信度，而 richness 选样是最大的不确定来源之一。",
  "method_detail": "先按 richness 从模拟星团表选样，再用弱透镜 ΔΣ 剖面拟合 NFW 质量，最后把测得质量与真实质量比较，并在不同 richness 阈值下重复整套流程。",
  "chain": ["mock 星团", "richness 选样", "弱透镜 ΔΣ", "质量标定", "偏差 10%"],
  "actions": ["把 richness 选样接到你的 mock catalog", "对比它的质量偏差曲线"],
  "red_flags": [{"flag": "未标定质量-观测关系就做宇宙学", "status": "discussed", "note": "作者做了标定"}],
  "concepts": [{"term": "richness", "gloss": "成员星系数的光学代理量"}]
}"""


def _check_fulltext() -> str:
    """全文处理链：断行修复、砍参考文献、按预算裁剪、pdf 链接推导。纯离线。"""
    from .fulltext import clean_text, condense, cut_references, pdf_url_for

    raw = "Some sam-\nple text.\n\n12\n\nMore sys-\ntematic errors here."
    cleaned = clean_text(raw)
    assert "sample" in cleaned, "连字符断行没修好"
    assert "systematic" in cleaned
    assert "\n12\n" not in cleaned, "孤立页码没去掉"

    body = "Introduction " + "x" * 300 + "\nReferences\n[1] someone 2020"
    assert "someone 2020" not in cut_references(body), "参考文献没砍掉"

    long_text = ("head sentence about sample selection. " * 40) + ("tail " * 200)
    trimmed = condense(long_text, max_chars=600)
    assert len(trimmed) <= 1200, "裁剪没生效"

    assert pdf_url_for("https://arxiv.org/abs/2610.26492").endswith("/pdf/2610.26492")
    assert pdf_url_for("https://arxiv.org/abs/1234", "https://x/y.pdf") == "https://x/y.pdf"
    return "断行修复 / 砍参考文献 / 预算裁剪 / PDF 链接推导 均正常"


def _check_analysis() -> str:
    from .analysis import analyze_papers, coerce_facts
    from .packs import load_packs
    from .profile import load_profile

    # 1) 事实解析：缺字段要变成 null，而不是报错
    facts = coerce_facts({"data": "纯字符串也要能接", "method": None})
    assert facts["data"].value == "纯字符串也要能接"
    assert facts["method"].is_missing and facts["systematics"].is_missing

    profile = load_profile("config/profile.yaml")
    packs = load_packs("config/packs")
    papers = _fake_papers()[:2]
    # The fixture must actually contain the quoted evidence; otherwise extraction is rejected.
    import json as _json
    from dataclasses import replace as _replace
    extraction = _json.loads(_EXTRACT_JSON)
    source = " ".join(value["quote"] for value in extraction.values()
                      if isinstance(value, dict) and value.get("quote"))
    source += " " + " ".join(item["quote"] for item in extraction["numbers"])
    papers = [_replace(paper, summary=source) for paper in papers]

    client = _FakeClient([_EXTRACT_JSON, _ASSESS_JSON, _EXTRACT_JSON, _ASSESS_JSON])
    analyzed = analyze_papers(
        client, papers, profile, packs, extract_model="fake-extract", assess_model="fake-assess"
    )
    first = analyzed[0]
    assert first.analysis is not None, "两阶段分析没有产出 analysis"
    assert "星团样本" in first.analysis.data, first.analysis.data
    assert first.analysis.systematics == "摘要未提", "null 事实没有降级成『摘要未提』"
    assert first.analysis.relevance == 0.88
    assert first.analysis.relevance_kind == "direct"
    assert first.analysis.for_you, "for_you 为空"
    assert first.analysis.prompt_version
    assert first.chinese_summary, "分析后中文摘要不应为空"
    # 第 5 批次：卡片上的其余分区
    assert first.analysis.verdict, "缺少一句话结论"
    assert first.analysis.read_advice == "read", "阅读建议没解析出来"
    assert "richness 选样" in first.analysis.background, "研究背景没解析出来"
    assert "弱透镜" in first.analysis.method_detail, "研究方法详解没解析出来"
    assert len(first.analysis.chain) == 5, "方法链没解析出来"
    assert len(first.analysis.numbers) == 2, "关键数字没从阶段 A 带过来"
    assert first.analysis.numbers[0].label == "样本量"
    assert first.analysis.actions, "今天能做什么为空"
    assert first.analysis.red_flags and first.analysis.red_flags[0].tone == "ok", "红旗状态没归一"
    assert first.analysis.concepts and first.analysis.concepts[0].term == "richness"

    # 2) prompt 里必须真的带上画像与领域包判据
    calls = client.chat.completions.calls
    assert len(calls) == 4, f"两篇论文应各调用两次（提取+评估），实际 {len(calls)} 次"
    assert "只抽取原文明确写出的内容" in calls[0]["messages"][1]["content"]
    assess_prompt = calls[1]["messages"][1]["content"]
    assert "读一篇论文时" in assess_prompt, "评估 prompt 没带读者画像"
    assert "做到这些才算扎实" in assess_prompt, "评估 prompt 没带领域包判据"
    assert "摘要未提" in assess_prompt, "评估 prompt 没写缺失约定"

    # 3) 客户端炸掉时，整封信不能跟着炸
    def _boom(self, **kwargs):
        raise RuntimeError("429 rate limited")

    broken_chat = type("Chat", (), {"completions": type("C", (), {"create": _boom})()})()
    broken = type("Client", (), {"chat": broken_chat})()
    survived = analyze_papers(broken, papers, profile, packs, extract_model="x", assess_model="y")
    assert len(survived) == len(papers)
    assert all(item.analysis is None for item in survived), "调用失败时不应伪造 analysis"

    return "抽事实→评估链路通；null 事实降级为『摘要未提』；prompt 带画像与包判据；客户端失败不致命"


def _check_attachment() -> str:
    from arxiv_digest.emailer import build_message

    html = "<html><body>digest</body></html>"
    plain = build_message(
        email_from="a@example.org",
        email_to="b@example.org",
        subject="s",
        html_body=html,
    )
    assert plain.get_content_type() == "multipart/alternative", plain.get_content_type()

    with_file = build_message(
        email_from="a@example.org",
        email_to="b@example.org",
        subject="s",
        html_body=html,
        attachment=("arxiv-digest-2026-10-25.html", html.encode("utf-8")),
    )
    assert with_file.is_multipart()
    assert with_file.get_content_type() == "multipart/mixed", with_file.get_content_type()
    names = [part.get_filename() for part in with_file.iter_attachments()]
    assert names == ["arxiv-digest-2026-10-25.html"], names
    payload = with_file.iter_attachments().__next__().get_payload(decode=True)
    assert payload and b"digest" in payload, "附件内容丢失"
    return "无附件=alternative、有附件=mixed、文件名与内容都在"


def _check_render(preview_path: Path | None) -> str:
    from .calendar import load_calendar

    cfg = load_config()
    bundle = load_calendar(
        cfg.calendar_events_path,
        cfg.calendar_conferences_path,
        horizon_days=cfg.calendar_horizon_days,
        recent_days=cfg.calendar_recent_days,
    )
    html = render_email_html(
        "自检占位总览",
        _fake_papers(),
        bundle,
        timezone_name=cfg.digest_timezone,
        deep_papers=4,
        brief_papers=10,
    )
    assert "<html" in html.lower()
    assert "精读" in html, "模板里找不到精读分区"
    assert "速览" in html, "模板里找不到速览分区"
    assert "近期日程" in html, "模板里找不到近期日程分区"
    assert "Mock paper 0" in html
    assert "Mock paper 13" in html, "第 14 篇（最后一条速览）没有渲染出来"
    # 速览必须中英并排：英文原句抄自摘要，中文来自 chinese_summary
    assert "We measure the cluster mass-observable relation" in html, "速览里没有英文摘要"
    assert "这是自检用的占位中文摘要" in html, "速览里没有中文摘要"

    # 带 analysis 的论文必须渲染成七问七答，而不是退回中文摘要
    from .analysis import coerce_analysis
    from .models import Paper
    from .profile import load_profile

    analysis = coerce_analysis(
        {
            "relevance": 0.9,
            "relevance_kind": "direct",
            "relevance_reason": "自检占位理由",
            "data": "数据集占位",
            "processing": "处理占位",
            "systematics": "系统误差占位",
            "method": "方法占位",
            "result": "结果占位",
            "caveat": "局限占位",
            "for_you": "动作占位",
            "confidence": "high",
            "background": "自检占位研究背景：为什么这个方向值得做。",
            "method_detail": "自检占位研究方法：具体到估计量、样本划分与拟合设定。",
        },
        profile=load_profile("config/profile.yaml"),
        model="fake",
    )
    from .models import ScoreBreakdown

    with_analysis = Paper(
        **{
            **_fake_papers()[0].__dict__,
            "analysis": analysis,
            "breakdown": ScoreBreakdown(keyword=2.4, zotero=1.1, category=0.4),
            "venue": "ApJ",
            "doi": "10.3847/1538-4357/ad0001",
        }
    )
    html2 = render_email_html(
        "自检占位总览",
        [with_analysis],
        None,
        timezone_name=cfg.digest_timezone,
        deep_papers=1,
        brief_papers=0,
        feedback_url="http://127.0.0.1:8765/log",
    )
    for label in ("数据", "处理", "系统误差", "方法", "结果", "作者局限", "对你有用"):
        assert label in html2, f"七问七答里缺「{label}」"
    assert "动作占位" in html2 and "0.90" in html2

    # ①分数构成：五项要能看到，且总分要等于构成之和
    assert "分数构成" in html2, "精读卡没有分数构成条"
    for label in ("关键词", "Zotero", "分类"):
        assert label in html2, f"分数构成里缺「{label}」"
    assert "+2.40" in html2 and "+1.10" in html2, "分数构成没有显示数值"

    # ②研究背景 / 研究方法：不再受 20 分钟预算限制
    assert "研究背景" in html2 and "自检占位研究背景" in html2, "缺少研究背景模块"
    assert "研究方法" in html2 and "自检占位研究方法" in html2, "缺少研究方法详解"
    assert "20 分钟" not in html2, "头部仍在写 20 分钟阅读限额"

    # ③反馈：两维六键，且不再出现旧的一维 v=up
    assert "相关性" in html2 and "有用性" in html2, "反馈区没有两个维度"
    assert "rel=2" in html2 and "use=0" in html2, "六键链接没有渲染出来"
    assert "v=up" not in html2, "仍在渲染旧的一维反馈链接"

    # ④期刊来源：venue 要作为来源标签出现
    assert "ApJ" in html2, "期刊来源标签没渲染"

    render_error_email_html("自检占位错误")

    # 预览文件用"前两篇带七问七答、其余是旧摘要"的混合稿，才看得出真实效果
    preview_papers = [
        Paper(**{**paper.__dict__, "analysis": analysis}) if index < 2 else paper
        for index, paper in enumerate(_fake_papers())
    ]
    if preview_path is not None:
        preview_html = render_email_html(
            "自检占位总览",
            preview_papers,
            bundle,
            timezone_name=cfg.digest_timezone,
            deep_papers=4,
            brief_papers=10,
        )
        preview_path.write_text(preview_html, encoding="utf-8")
        return f"渲染成功，已写入 {preview_path}（前两篇带七问七答，其余为旧中文摘要）"
    return "渲染成功（精读/速览/日程三个分区都在）"


def _check_sources() -> str:
    from arxiv_digest.sources import Item, dedupe, load_sources, merge_items, SourceResult
    from arxiv_digest.sources.base import plain_text
    from arxiv_digest.sources.indico import _contribution_to_item, _flatten, export_url

    assert plain_text("<p>Body &amp; text.</p>") == "Body & text.", "HTML 反转义失败"
    long_text = plain_text("A" * 500, limit=120)
    assert long_text.endswith("…") and len(long_text) <= 121, "长文本截断失败"

    config = load_sources("config/sources.yaml")
    assert config["feeds"], "sources.yaml 没读到 feeds"
    enabled = [feed for feed in config["feeds"]]
    assert all(feed.get("url") for feed in enabled), "有 feed 缺 url"

    assert (
        export_url("https://indico.cern.ch/event/1234/")
        == "https://indico.cern.ch/export/event/1234.json?detail=contributions"
    ), "Indico 导出地址推导错误"
    assert export_url("https://example.org/no-event/") == "", "非法 Indico 地址应返回空"

    # 导出的 results 有两种形态：直接是报告列表，或"事件对象 + 内嵌 contributions"。
    nested = [
        {
            "title": "整场会议",
            "url": "https://indico.example.org/event/9/",
            "contributions": [
                {"title": "报告 A", "startDate": {"date": "2026-05-22", "time": "09:00:00"}},
                {"title": "报告 B", "url": "/event/9/contributions/2/", "speakers": [{"fullName": "Li, Si"}]},
            ],
        }
    ]
    flat = _flatten(nested)
    assert [entry["title"] for entry in flat] == ["报告 A", "报告 B"], "内嵌 contributions 没有摊平"
    item = _contribution_to_item(flat[1], "示例会议", (), "https://indico.example.org/event/9/")
    assert item is not None and item.url == "https://indico.example.org/event/9/contributions/2/", "相对 url 没拼成绝对地址"
    assert item.meta.get("讲者") == "Li, Si", "讲者没解析出来"

    short = Item(id="x", kind="secondary", title="Test story: a colon", url="u1", source="s", text="short")
    longer = Item(id="y", kind="secondary", title="Test story - a colon", url="u2", source="s", text="a much longer body")
    result = SourceResult(name="fixture", items=[short, longer])
    merged = merge_items([result], kind="secondary")
    assert len(merged) == 1 and merged[0].text.startswith("a much longer"), "同标题去重没保留更长的正文"
    assert merge_items([SourceResult(name="broken", error="boom")], kind="secondary") == [], "失败源应产出空列表"
    return f"{len(config['feeds'])} 个 feed 配置可用；去重与 Indico 导出地址正确"


def _check_state() -> str:
    from datetime import date, timedelta

    from arxiv_digest.sources import Item
    from arxiv_digest.state import (
        State,
        filter_unseen,
        mark_seen,
        prune,
        record_day,
        weekly_trend,
    )

    state = State()
    items = [
        Item(id="a", kind="secondary", title="A", url="", source="s"),
        Item(id="b", kind="secondary", title="B", url="", source="s"),
    ]
    assert len(filter_unseen(state, items)) == 2, "空状态应全部可见"
    mark_seen(state, items, day="2026-10-01")
    assert filter_unseen(state, items) == [], "已推送条目应被过滤"

    today = date(2026, 10, 25)
    recent = today.isoformat()
    old = (today - timedelta(days=9)).isoformat()
    record_day(state, day=recent, categories=["astro-ph.CO"] * 5 + ["astro-ph.GA"])
    record_day(state, day=old, categories=["astro-ph.CO"] * 2)

    rows = weekly_trend(state, today=today)
    assert rows and rows[0].label == "astro-ph.CO", "趋势应把本周最多的分类排在最前"
    assert rows[0].this_week == 5 and rows[0].last_week == 2, f"周环比数字不对：{rows[0]}"
    assert rows[0].delta_pct == 150 and rows[0].arrow == "↑", "周环比百分比/箭头不对"

    prune(state, today=date(2026, 12, 31), keep_days=30)
    assert "a" not in state.seen, "过期状态应被清理"
    return f"已读过滤、周环比（{rows[0].label} {rows[0].last_week}→{rows[0].this_week}）与过期清理正常"


def _check_pulse() -> str:
    from arxiv_digest.models import Paper
    from arxiv_digest.packs import load_packs
    from arxiv_digest.pulse import (
        coerce_threads,
        count_topics,
        fallback_pulse,
        materials_from,
    )
    from arxiv_digest.sources import Item

    paper = Paper(
        id="p1",
        title="Weak lensing mass calibration for galaxy clusters with DESI",
        authors=["A"],
        summary="We find a 2 sigma tension with the Planck cosmology in the cluster mass scale.",
        published="2026-10-25",
        updated="2026-10-25",
        categories=["astro-ph.CO"],
        pdf_url="",
        abs_url="https://arxiv.org/abs/1",
        score=1.0,
        rating=3,
        score_reasons=[],
        chinese_summary="",
    )
    story = Item(
        id="s1",
        kind="secondary",
        title="Tension grows between cluster counts and CMB",
        url="https://example.org/story",
        source="AAS Nova",
        published="2026-10-24",
        text="The discrepancy between galaxy cluster abundance and Planck keeps widening.",
    )
    materials = materials_from([paper], [story], [])
    assert len(materials) == 2, "材料摊平数量不对"

    topics = count_topics(
        materials,
        [("星系团", ("galaxy cluster", "clusters")), ("弱透镜", ("weak lensing",))],
    )
    assert topics and topics[0].label == "星系团", f"主题排序不对：{[t.label for t in topics]}"
    assert topics[0].is_shared, "跨源共现没被识别"
    assert topics[0].tension_items, "张力措辞没被识别"

    valid = {material.id for material in materials}
    threads = coerce_threads(
        {
            "threads": [
                {"question": "群团质量标定还可靠吗？", "summary": "s", "evidence": ["p1", "s1"], "status": "debate"},
                {"question": "编造的一条", "summary": "s", "evidence": ["ghost"], "status": "new"},
            ]
        },
        valid,
        index={material.id: material for material in materials},
    )
    assert len(threads) == 1, "没有证据的判断应被丢弃"
    assert threads[0].status_label == "有争论", "状态标签映射错误"
    assert threads[0].links and threads[0].links[0][1].startswith("http"), "证据没转成链接"

    fallback = fallback_pulse(materials, topics)
    assert fallback.basis == "fallback" and fallback.headline, "无模型时应有诚实兜底"

    packs = {pack.id: pack for pack in load_packs("config/packs")}
    assert packs["galaxy_cosmology"].topics, "领域包缺 pulse.topics"
    return f"{len(topics)} 个主题命中；跨源识别、张力识别、无证据丢弃、兜底均正常"


def _check_radar() -> str:
    from arxiv_digest.radar import (
        Neighborhood,
        format_plain_text,
        parse_radar,
        select_blocks,
    )

    neighborhoods = parse_radar(
        {
            "neighborhoods": [
                {"id": "exoplanets", "name": "系外行星", "categories": ["astro-ph.EP"]},
                {"id": "off", "name": "关掉的", "categories": ["astro-ph.HE"], "enabled": False},
            ]
        }
    )
    assert len(neighborhoods) == 2, "雷达配置解析失败"
    assert [item.id for item in neighborhoods if item.enabled] == ["exoplanets"], "enabled: false 没生效"

    papers = [
        Paper(
            id="a",
            title="First detection of an atmosphere on a rocky planet",
            authors=["A"],
            summary="s",
            published="2026-10-20",
            updated="",
            categories=["astro-ph.EP"],
            pdf_url="",
            abs_url="https://example.org/a",
        ),
        Paper(
            id="b",
            title="Routine photometry of a quiet field",
            authors=["B"],
            summary="s",
            published="2026-10-21",
            updated="",
            categories=["astro-ph.EP"],
            pdf_url="",
            abs_url="https://example.org/b",
        ),
    ]
    radar = select_blocks(
        [Neighborhood("exoplanets", "系外行星", ("astro-ph.EP",))], papers, per_field=3
    )
    assert radar.blocks[0].headline.title.startswith("First detection"), "信号词没排在前面"
    assert radar.blocks[0].headline.summary, "每条都必须带解读，不能只有标题"
    assert radar.blocks[0].folded_count == 1, "同一方向的其余条目应折叠"
    assert len(radar.hits) == 2, "摊平后应包含全部命中"
    capped = select_blocks([Neighborhood("e", "系", ("astro-ph.EP",))], papers, per_field=1)
    assert len(capped.hits) == 1, "每方向条数上限没生效"
    skipped = select_blocks(
        [Neighborhood("e", "系", ("astro-ph.EP",))], papers, skip_ids=("a",)
    )
    assert skipped.hits[0].title.startswith("Routine"), "已读条目没有被跳过"
    assert "本周各方向投稿量" in format_plain_text(radar) or radar.counts, "投稿量没统计"

    empty = select_blocks([Neighborhood("x", "空", ("astro-ph.XX",))], papers)
    assert empty.is_empty, "没有命中时应为空"
    return f"{len(neighborhoods)} 个方向；信号词优先、每方向 1 条上版、其余折叠、已读跳过均正常"


def _check_translate() -> str:
    from arxiv_digest.sources import Item
    from arxiv_digest.translate import build_translate_prompt, translate_items

    items = [
        Item(
            id="rss-1",
            kind="secondary",
            title="Webb finds water in a rocky planet's air",
            url="https://example.org/x",
            source="AAS Nova",
            published="2026-10-20",
            text="Astronomers report a detection of water vapour.",
        )
    ]
    prompt = build_translate_prompt(items)
    assert "id=rss-1" in prompt and "不要换算" in prompt, "翻译提示词缺少约束"

    class _Boom:
        class chat:  # noqa: N801 - 模拟 openai 结构
            class completions:  # noqa: N801
                @staticmethod
                def create(**_: object) -> object:
                    raise RuntimeError("network down")

    fallback = translate_items(_Boom(), "m", items)
    assert fallback[0].title_zh == "" and fallback[0].title.startswith("Webb"), "失败时应保留英文原文"

    class _Good:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**_: object) -> object:
                    import json as _json

                    class _Msg:
                        content = _json.dumps({"items": [{"id": "rss-1", "title_zh": "韦布在岩石行星大气中发现水", "text_zh": "报告称探测到水汽。"}]})

                    class _Choice:
                        message = _Msg()

                    class _Resp:
                        choices = [_Choice()]

                    return _Resp()

    translated = translate_items(_Good(), "m", items)
    assert translated[0].title_zh.startswith("韦布"), "成功路径没写回中文标题"
    assert translated[0].title.startswith("Webb"), "英文原标题必须保留"

    many = [
        Item(
            id=f"n{i}",
            kind="secondary",
            title=f"Headline {i}",
            url=f"https://example.org/{i}",
            source="AAS Nova",
            published="2026-10-25",
            text=f"Body {i}",
        )
        for i in range(1, 21)
    ]

    class _Batch:
        def __init__(self) -> None:
            self.calls = 0
            self.sizes: list[int] = []
            self.chat = self
            self.completions = self

        def create(self, **kwargs: object) -> object:
            import json as _json

            self.calls += 1
            prompt = str(kwargs["messages"][0]["content"])  # type: ignore[index]
            ids = [
                line.split("id=")[1].strip()
                for line in prompt.splitlines()
                if "id=n" in line
            ]
            self.sizes.append(len(ids))
            if self.calls == 2:
                raise RuntimeError("第二批故意失败")

            class _Msg:
                content = _json.dumps(
                    {
                        "items": [
                            {"id": key, "title_zh": f"中文{key}", "text_zh": f"短文{key}"}
                            for key in ids
                        ]
                    },
                    ensure_ascii=False,
                )

            class _Choice:
                message = _Msg()

            class _Resp:
                choices = [_Choice()]

            return _Resp()

    batch_client = _Batch()
    out = translate_items(batch_client, "m", many, limit=20, batch_size=8)
    assert batch_client.calls == 3, f"20 条按 8 条一批应 3 次调用，实际 {batch_client.calls}"
    assert batch_client.sizes == [8, 8, 4], f"每批应为 8/8/4，实际 {batch_client.sizes}"
    assert len([item for item in out if item.title_zh]) == 12, "中间批失败，其余两批仍应译出"
    assert len(translate_items(batch_client, "m", many, limit=5, batch_size=8)) == 20, "超出 limit 的条目应原样保留"
    return "提示词约束、失败回退原文、分批 8/8/4、部分失败不拖垮其他批 均正常"


def _check_feedback() -> str:
    from pathlib import Path
    from tempfile import TemporaryDirectory

    from arxiv_digest.feedback import (
        FeedbackRecord,
        apply_feedback,
        format_plain_text,
        learned_weights,
        load_feedback,
        merge_csv,
        parse_feedback_csv,
        record_vote,
        save_feedback,
    )

    def _paper(item_id: str, category: str, author: str, score: float) -> Paper:
        return Paper(
            id=item_id,
            title=f"paper {item_id}",
            authors=[author],
            summary="s",
            published="2026-10-20",
            updated="",
            categories=[category],
            pdf_url="",
            abs_url="",
            score=score,
        )

    with TemporaryDirectory() as tmp:
        path = Path(tmp) / "feedback.json"
        store = load_feedback(path)
        assert store.is_empty, "缺文件应返回空 store"

        voted = _paper("2610.1v1", "astro-ph.CO", "Vitorelli, Andre", 8.0)
        assert record_vote(store, voted, relevance=2), "记相关性失败"
        assert record_vote(store, voted, usefulness=0), "记有用性失败"
        record = store.votes["2610.1v1"]
        assert (record.relevance, record.usefulness) == (2, 0), "只改一维时另一维被清掉了"
        assert not record_vote(store, voted), "两维都不给应被拒"
        assert not record_vote(store, voted, relevance=9), "越界取值应被拒"
        save_feedback(path, store)

        reloaded = load_feedback(path)
        record = reloaded.votes["2610.1v1"]
        assert (record.relevance, record.usefulness) == (2, 0), "落盘再读丢维度"
        categories, authors = learned_weights(reloaded)
        assert categories.get("astro-ph.CO", 0) > 0, "分类权重没学到"
        assert authors.get("vitorelli", 0) > 0, "作者权重没学到"

        papers = [
            _paper("new-a", "astro-ph.CO", "Someone, Else", 7.0),
            _paper("new-b", "hep-th", "Other, Person", 7.4),
        ]
        ranked = apply_feedback(reloaded, papers)
        assert ranked[0].id == "new-a", "偏好应该把同类论文提到前面"
        assert "分类偏好" in format_plain_text(reloaded), "统计输出缺分类偏好"

        # v1 旧文件：字符串票按 2/1/0 折算成两维
        legacy_path = Path(tmp) / "legacy.json"
        legacy_path.write_text(
            '{"version": 1, "votes": {"2610.2v1": "down"}, "history": {}}', encoding="utf-8"
        )
        legacy = load_feedback(legacy_path)
        legacy_record = legacy.votes["2610.2v1"]
        assert (legacy_record.relevance, legacy_record.usefulness) == (0, 0), "v1 旧票没折算"

        # 表格（Google Sheet 导出）导入：坏行跳过，不整份报错
        rows = parse_feedback_csv(
            "item,relevance,usefulness\n2610.1v1,1,2\n\nbad,row\n2610.3v1,0,\n"
        )
        assert rows == [("2610.1v1", 1, 2), ("2610.3v1", 0, None)], rows
        assert merge_csv(legacy, rows) == 2

    class _BoomStore:
        is_empty = False
        votes = {"x": FeedbackRecord(relevance=2, usefulness=2)}
        history: dict[str, dict[str, list[str]]] = {}

    assert apply_feedback(_BoomStore(), papers) is not None, "空偏好应安全返回"
    return "两维独立落盘 / 权重学习 / 重排 / v1 兼容 / CSV 导入 均正常"


def _check_breakdown() -> str:
    """分数构成：邮件里那几根色条必须和总分对得上。"""

    from .models import ScoreBreakdown
    from .recommender import rank_papers
    from .zotero_client import ZoteroItem

    breakdown = ScoreBreakdown(keyword=2.4, zotero=1.1, category=0.4)
    assert round(breakdown.total, 3) == 3.9, breakdown.total
    assert [label for label, _ in breakdown.parts] == ["关键词", "Zotero", "分类", "库内", "反馈"]
    assert len(breakdown.visible_parts) == 3

    base = _fake_papers()[0]
    crowded = Paper(
        **{
            **base.__dict__,
            "categories": ["astro-ph.CO", "astro-ph.GA", "astro-ph.IM", "astro-ph.SR"],
        }
    )
    item = rank_papers([crowded], ["nothing-matches-this"], [], 5)[0]
    assert item.breakdown.category == 0.4, "分类加成没有按最多 2 个分类封顶"
    assert round(item.score, 3) == round(item.breakdown.total, 3), "总分与构成之和不一致"

    library = [ZoteroItem(key="k", title="t", item_type="journalArticle", doi="10.1/x")]
    collected = Paper(**{**base.__dict__, "id": "doi:10.1/x", "doi": "10.1/x"})
    collected_item = rank_papers([collected], [], [], 5, library=library)[0]
    assert collected_item.in_library, "已在库里的论文没有标记"
    assert collected_item.breakdown.library < 0, "已在库里的论文没有扣分"
    return "五项构成 / 分类封顶 / 库内扣分 / 总分一致 均正常"


def _check_journals() -> str:
    """期刊来源：Crossref 的 JATS 摘要与条目解析，全部用离线样本。"""

    from .journals import _item_to_paper, clean_abstract, load_journals, merge_with_arxiv

    journals = load_journals("config/journals.yaml")
    ids = {journal.id for journal in journals}
    assert {"ApJ", "MNRAS", "NatAs"} <= ids, ids
    apj = next(journal for journal in journals if journal.id == "ApJ")
    assert apj.issn and apj.pack, "ApJ 缺 ISSN 或领域包"

    raw = (
        "<jats:title>Abstract</jats:title><jats:p>We study "
        "<jats:italic>richness</jats:italic>-selected clusters &amp; their bias.</jats:p>"
    )
    cleaned = clean_abstract(raw)
    for bad in ("<", "&amp;", "Abstract"):
        assert bad not in cleaned, cleaned
    assert "richness" in cleaned

    item = {
        "type": "journal-article",
        "DOI": "10.3847/1538-4357/AD1234",
        "title": ["A Cluster Mass Calibration"],
        "abstract": raw,
        "author": [{"given": "A.", "family": "Vitorelli"}],
        "published": {"date-parts": [[2026, 10, 20]]},
    }
    paper = _item_to_paper(item, apj)
    assert paper is not None, "期刊条目没解析出来"
    assert paper.venue == "ApJ" and (paper.doi or "").startswith("10.3847/")
    assert paper.categories == [] and paper.pack_hint == apj.pack
    assert paper.id.startswith("doi:"), paper.id
    assert _item_to_paper({**item, "type": "posted-content"}, apj) is None, "非期刊条目应被丢掉"

    arxiv_paper = _fake_papers()[0]
    duplicate = Paper(**{**paper.__dict__, "title": arxiv_paper.title})
    merged = merge_with_arxiv([arxiv_paper], [duplicate])
    assert len(merged) == 1 and merged[0].id == arxiv_paper.id, "同标题没有去重"
    assert merged[0].venue == "ApJ", "去重后没把期刊信息补给 arXiv 那条"
    return f"{len(journals)} 本刊（含 ApJ/MNRAS/NatAs）；JATS 清洗、条目解析、与 arXiv 去重均正常"


def _check_journal_gate() -> str:
    """期刊主题闸门：期刊什么都发，得挡住与本领域无关的。"""

    from .config import load_config
    from .pipeline import _journal_matches, _journal_topic_terms

    from datetime import datetime as _dt
    from datetime import timezone as _tz

    from dataclasses import replace
    terms = _journal_topic_terms(replace(load_config(), fields=["galaxy_cosmology", "galaxy_formation"], packs_dir="config/packs"))
    assert "weak lensing" in terms and "richness" in terms, terms[:10]
    assert "james webb" not in terms and "high-redshift" not in terms, "通用词没被挡掉"

    def fake(title: str, summary: str = "") -> Paper:
        return Paper(
            id="x",
            title=title,
            authors=[],
            summary=summary,
            published=_dt(2026, 10, 20, tzinfo=_tz.utc),
            updated=_dt(2026, 10, 20, tzinfo=_tz.utc),
            categories=[],
            pdf_url="",
            abs_url="",
        )

    assert _journal_matches(fake("Weak lensing mass calibration of clusters"), terms)
    assert _journal_matches(fake("A richness-selected cluster sample"), terms)
    assert not _journal_matches(fake("TESS transiting planets around bright stars"), terms)
    assert not _journal_matches(fake("Massive stars in the LMC"), terms), "单词不该放宽到 massive"
    assert _journal_matches(fake("anything"), ()), "没有词表时不该过滤"
    return "诊断性主题词才有闸门效果；行星/恒星类稿件被挡掉"


def _check_library() -> str:
    """Zotero 库：判断"这篇我是不是已经收过了"，并给出相似度理由。"""

    from .zotero_client import (
        _item_to_entry,
        library_match,
        preferences_from_items,
        zotero_similarity_score,
    )

    raw_items = [
        {
            "key": "A",
            "data": {
                "itemType": "journalArticle",
                "title": "Cluster mass calibration with weak lensing",
                "tags": [{"tag": "favorite"}],
                "DOI": "10.3847/1538-4357/abc",
                "archive": "arXiv",
                "archiveID": "arXiv:2610.12345",
            },
        },
        {
            "key": "B",
            "data": {
                "itemType": "journalArticle",
                "title": "Galaxy quenching in dense environments",
                "tags": [{"tag": "not-interesting"}],
            },
        },
        {"key": "C", "data": {"itemType": "attachment", "title": "Full Text PDF"}},
    ]
    items = [
        entry for entry in (_item_to_entry(raw) for raw in raw_items) if entry is not None
    ]
    assert len(items) == 2, "附件条目没有被跳过"
    assert items[0].arxiv_id == "2610.12345", items[0].arxiv_id
    assert items[0].doi == "10.3847/1538-4357/abc", items[0].doi
    preferences = preferences_from_items(items)
    assert len(preferences) == 2
    assert preferences[0].score > 0 and preferences[1].score < 0, "标签权重没折成偏好"

    base = _fake_papers()[0]
    by_doi = Paper(
        **{**base.__dict__, "id": "doi:10.3847/1538-4357/ABC", "doi": "10.3847/1538-4357/abc"}
    )
    assert library_match(by_doi, items) is not None, "DOI 匹配失败"
    by_arxiv = Paper(**{**base.__dict__, "id": "2610.12345v2"})
    assert library_match(by_arxiv, items) is not None, "arXiv id 匹配失败"
    by_title = Paper(
        **{**base.__dict__, "id": "x", "title": "Cluster mass calibration with weak lensing"}
    )
    assert library_match(by_title, items) is not None, "标题匹配失败"
    unrelated = Paper(
        **{**base.__dict__, "id": "y", "title": "Cosmic ray propagation in the Galactic halo"}
    )
    assert library_match(unrelated, items) is None, "无关论文被误判成库内"

    total, reasons = zotero_similarity_score(
        "Cluster mass calibration with weak lensing",
        "weak lensing mass calibration for clusters",
        preferences,
    )
    assert total > 0 and reasons, "相似度没有给出正权重与理由"
    return "DOI/arXiv/标题三级匹配、标签权重、相似度理由均正常"


def _check_theme() -> str:
    from .theme import DARK, LIGHT, available, normalize, palette
    from .emailer import render_email_html

    assert available() == ("dark", "light"), available()
    assert set(DARK) == set(LIGHT), set(DARK) ^ set(LIGHT)
    dark, light = palette("dark"), palette("LIGHT")
    assert dark["page"] != light["page"], "两套主题底色相同"
    assert normalize("浅色") == "light" and normalize("??") == "dark"
    html = render_email_html("测试", [], theme="light")
    assert "#f3f0e9" in html and "{{ P." not in html, "浅色主题未渲染或残留模板变量"
    html_dark = render_email_html("测试", [], theme="dark")
    assert "#05080f" in html_dark, "深色主题未生效"
    return "两套主题色值齐备（%d 个 token），模板无残留变量" % len(DARK)


def _check_math() -> str:
    """LaTeX→Unicode：邮件里不能跑 MathJax，只能靠这一步把公式写成能看的文本。"""

    from arxiv_digest.mathfmt import has_latex, to_unicode

    cases = [
        (r"$H_0 = 67.4 \pm 0.5$", "H₀ = 67.4 ± 0.5"),
        (r"$10^{-3}$", "10⁻³"),
        (r"$\Lambda$CDM", "ΛCDM"),
        (r"$\sigma_8$", "σ₈"),
        (r"$r_d$ 自由", "r_d 自由"),
        (r"$\frac{a}{b}$", "a/b"),
    ]
    for raw, want in cases:
        got = to_unicode(raw)
        if got != want:
            raise AssertionError(f"{raw!r} → {got!r}，期望 {want!r}")
    if to_unicode("没有公式") != "没有公式":
        raise AssertionError("纯文本被改动了")
    if has_latex("没有公式"):
        raise AssertionError("has_latex 误判")

    # 渲染层要能接住：模板里所有字符串都会过 finalize
    from arxiv_digest.emailer import _env

    rendered = _env.from_string("{{ text }}").render(text=r"$H_0$ 与 $\Omega_m$")
    if "H₀" not in rendered or "Ωₘ" not in rendered:
        raise AssertionError(f"模板渲染没有转换公式：{rendered!r}")
    return "6 个公式转换正确；模板 finalize 生效"


def _check_llm_client() -> str:
    """模型调用层：围栏容错、空 content 重试、response_format 降级、thinking 三态。"""

    from types import SimpleNamespace

    from arxiv_digest.llm_client import LLMOptions, call_json

    class _C:
        base_url = "https://api.deepseek.com"
        def __init__(self, script):
            self.script = list(script)
            self.calls: list[dict] = []
            outer = self

            class _Completions:
                def create(self, **kwargs):
                    outer.calls.append(kwargs)
                    step = outer.script.pop(0)
                    if isinstance(step, Exception):
                        raise step
                    message = SimpleNamespace(content=step)
                    return SimpleNamespace(choices=[SimpleNamespace(message=message)])

            self.chat = SimpleNamespace(completions=_Completions())

    # 空 content 之后重试成功
    client = _C(["", '```json\n{"ok": 1}\n```'])
    payload = call_json(client, "m", "p", options=LLMOptions(attempts=3))
    assert payload == {"ok": 1}, payload
    assert len(client.calls) == 2, len(client.calls)

    # 端点不认 response_format → 降级重试
    client = _C([ValueError("Unrecognized request argument supplied: response_format"), '{"ok": 2}'])
    assert call_json(client, "m", "p", options=LLMOptions(attempts=3)) == {"ok": 2}
    assert "response_format" in client.calls[0]
    assert "response_format" not in client.calls[1]

    # thinking 三态：False 显式发、True 省略 temperature、None 完全不发
    client = _C(['{"ok": 3}'])
    call_json(client, "m", "p", options=LLMOptions(thinking=False))
    assert client.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "temperature" in client.calls[0]

    client = _C(['{"ok": 4}'])
    call_json(client, "m", "p", options=LLMOptions(thinking=True))
    assert "temperature" not in client.calls[0]
    assert client.calls[0]["extra_body"] == {"thinking": {"type": "enabled"}}

    client = _C(['{"ok": 5}'])
    client.base_url = "https://api.openai.com"
    call_json(client, "m", "p", options=LLMOptions(thinking=None))
    assert "extra_body" not in client.calls[0]
    assert client.calls[0]["max_tokens"] > 0

    # 被 max_tokens 截断（finish_reason="length"）→ 自动放大重试
    class _Truncating:
        def __init__(self):
            self.calls: list[dict] = []
            outer = self

            class _Completions:
                def create(self, **kwargs):
                    outer.calls.append(kwargs)
                    first = len(outer.calls) == 1
                    content = '{"partial": ' if first else '{"ok": 6}'
                    message = SimpleNamespace(content=content)
                    reason = "length" if first else "stop"
                    choice = SimpleNamespace(message=message, finish_reason=reason)
                    return SimpleNamespace(choices=[choice])

            self.chat = SimpleNamespace(completions=_Completions())

    client = _Truncating()
    payload = call_json(client, "m", "p", options=LLMOptions(max_tokens=1000, attempts=3))
    assert payload == {"ok": 6}, payload
    assert len(client.calls) == 2, len(client.calls)
    assert client.calls[1]["max_tokens"] == 2000, client.calls[1]["max_tokens"]

    # `.env` 里 "MODEL=   # 注释" 这种写法会被 dotenv 原样读成注释文本，必须清掉
    from .config import _env_model

    keys = ("PULSE_MODEL", "ANALYSIS_ASSESS_MODEL", "OPENAI_MODEL")
    backup = {k: os.environ.get(k) for k in keys}
    try:
        os.environ["PULSE_MODEL"] = "            # 留空则用 OPENAI_MODEL"
        os.environ["ANALYSIS_ASSESS_MODEL"] = "'deepseek-v4-pro'"
        os.environ["OPENAI_MODEL"] = "deepseek-flash   # 行内注释"
        assert _env_model("PULSE_MODEL", "fallback") == "fallback", _env_model("PULSE_MODEL", "fallback")
        assert _env_model("ANALYSIS_ASSESS_MODEL", "x") == "deepseek-v4-pro"
        assert _env_model("OPENAI_MODEL", "x") == "deepseek-flash"
    finally:
        for k, v in backup.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    return "围栏容错 / 空内容重试 / 格式降级 / thinking 三态 / 截断放大 / 模型名清洗 均正常"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="arxiv-digest 离线自检")
    parser.add_argument("--write-preview", type=Path, default=None, help="把渲染结果写到这个 HTML 文件")
    args = parser.parse_args(argv)

    _check("导入链", _check_imports)
    _check("第三方依赖", _check_third_party_deps)
    _check("默认配置", _check_default_config)
    _check("JSON 容错解析", _check_json_parsing)
    _check("分批切分", _check_batching)
    _check("无 key 降级", _check_fallback_path)
    _check("日历读取", _check_calendar)
    _check("读者画像", _check_profile)
    _check("领域包分派", _check_packs)
    _check("全文处理", _check_fulltext)
    _check("多源抓取", _check_sources)
    _check("状态与趋势", _check_state)
    _check("本周脉搏", _check_pulse)
    _check("邻域雷达", _check_radar)
    _check("二手翻译", _check_translate)
    _check("反馈闭环", _check_feedback)
    _check("分数构成", _check_breakdown)
    _check("期刊来源", _check_journals)
    _check("期刊闸门", _check_journal_gate)
    _check("Zotero 库匹配", _check_library)
    _check("两阶段分析", _check_analysis)
    _check("公式排版", _check_math)
    _check("模型调用层", _check_llm_client)
    _check("邮件附件", _check_attachment)
    _check("主题配色", _check_theme)
    _check("邮件渲染", lambda: _check_render(args.write_preview))

    failures = 0
    print("")
    for name, ok, detail in _checks:
        print(f"{PASS if ok else FAIL} {name}")
        if ok and detail:
            for line in detail.splitlines():
                print(f"       {line}")
        elif not ok:
            failures += 1
            for line in detail.splitlines():
                print(f"       {line}")
    print("")
    if failures:
        print(f"自检未通过：{failures} 项失败。")
        return 1
    print(f"自检全部通过（{len(_checks)} 项）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
