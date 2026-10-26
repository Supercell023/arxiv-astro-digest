from __future__ import annotations

import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage
from html import escape
from urllib.parse import urlencode, urlparse
from .feedback_security import signed_params

from jinja2 import Environment, FileSystemLoader

from .mathfmt import to_unicode
from .theme import normalize, palette
from markupsafe import Markup

from .calendar import CalendarBundle
from .models import Paper

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
_env = Environment(
    loader=FileSystemLoader(_TEMPLATES_DIR),
    autoescape=True,
    # 所有字符串输出都过一遍 LaTeX→Unicode：模型很爱在正文里写 $H_0$，
    # 而邮件客户端不跑 JS。纯文本走的是快速返回，不影响性能。
    finalize=lambda value: to_unicode(value) if isinstance(value, str)
    and not value.startswith(("http://", "https://")) else value,
)

# 分数构成条：五项各自一个语义色 token，模板只按名字取色，不写死色值
_BREAKDOWN_COLORS = {
    "关键词": "bar_keyword",
    "Zotero": "bar_zotero",
    "分类": "bar_category",
    "库内": "bar_library",
    "反馈": "bar_feedback",
}


def _math(value: object) -> str:
    """模板过滤器 `| math`：把 LaTeX 片段转成能直接显示的 Unicode。

    放在渲染阶段而不是写进模型输出，是为了让同一份 digest.json
    在换成网页/终端输出时还能重新排版。
    """

    return to_unicode(str(value or ""))


_env.filters["math"] = _math


def _safe_url(value: object) -> str:
    text = str(value or "").strip()
    if any(ord(char) < 32 for char in text):
        return ""
    parsed = urlparse(text)
    return text if parsed.scheme.lower() in {"http", "https"} and parsed.netloc else ""


_env.filters["safe_url"] = _safe_url


def _stars(rating: int) -> str:
    rating = max(1, min(5, rating))
    return "\u2605" * rating + "\u2606" * (5 - rating)


def _nl2br(text: str) -> Markup:
    return Markup(escape(text).replace("\n", "<br>"))


def _snippet(text: str, limit: int = 110) -> Markup:
    """速览行用的一句话摘要：按字符截断，不留半句。"""
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return Markup(escape(flat))
    cut = flat[:limit]
    for mark in ("。", "；", "，", ". ", "; "):
        pos = cut.rfind(mark)
        if pos >= limit // 2:
            return Markup(escape(cut[:pos]) + "…")
    return Markup(escape(cut) + "…")


def _en_snippet(text: str, limit: int = 170) -> Markup:
    """英文原句摘要：按句子边界截断，速览行里和中文摘要并排出现。"""
    flat = " ".join((text or "").split())
    if len(flat) <= limit:
        return Markup(escape(flat))
    cut = flat[:limit]
    for mark in (". ", "; ", "? ", "! "):
        pos = cut.rfind(mark)
        if pos >= limit // 2:
            return Markup(escape(cut[: pos + 1]).rstrip() + " …")
    return Markup(escape(cut).rstrip() + " …")


def _now_in(timezone_name: str) -> datetime:
    """用 DIGEST_TIMEZONE 而不是 UTC —— 否则 Actions 里日期会差 8 小时。"""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(timezone_name))
    except Exception:
        return datetime.now()


def _breakdown_rows(paper: Paper) -> list[dict[str, object]]:
    """把 ScoreBreakdown 拆成进度条要的几行；没有构成就返回空。"""

    breakdown = getattr(paper, "breakdown", None)
    if breakdown is None:
        return []
    parts = [(label, value) for label, value in breakdown.parts if abs(value) >= 0.005]
    if not parts:
        return []
    scale = max(abs(value) for _, value in parts) or 1.0
    return [
        {
            "label": label,
            "value": value,
            "value_text": f"{value:+.2f}",
            "pct": max(2, round(abs(value) / scale * 100)),
            "color": _BREAKDOWN_COLORS.get(label, "bar_keyword"),
            "negative": value < 0,
        }
        for label, value in parts
    ]


def _feedback_links(base: str, paper_id: str, stamp: str, signing_key: str = "") -> dict[str, str]:
    """六个按钮：相关性三档 + 有用性三档。

    v1 只有一个 `v=up|meh|down`，两个问题挤在一起；v2 拆成
    `rel=0|1|2` 与 `use=0|1|2` 两组，点哪个只改哪一维。
    """

    if not base:
        return {}
    joined = "&" if "?" in base else "?"

    def link(**extra: object) -> str:
        params = signed_params({"item": paper_id, "t": stamp, **extra}, signing_key)
        return f"{base}{joined}{urlencode(params)}"

    return {
        "rel2": link(rel=2),
        "rel1": link(rel=1),
        "rel0": link(rel=0),
        "use2": link(use=2),
        "use1": link(use=1),
        "use0": link(use=0),
    }


def render_email_html(
    summary: str,
    papers: list[Paper],
    calendar: CalendarBundle | None = None,
    *,
    timezone_name: str = "Asia/Shanghai",
    deep_papers: int = 4,
    brief_papers: int = 10,
    secondary: list[object] | None = None,
    talks: list[object] | None = None,
    trend: list[object] | None = None,
    sources: list[object] | None = None,
    pulse: object | None = None,
    radar: object | None = None,
    feedback_url: str = "",
    window_days: int = 1,
    attach_name: str = "",
    theme: str = "dark",
    feedback_signing_key: str = "",
    edition: str = "daily",
    period_label: str = "",
    news_counts: dict[str, int] | None = None,
) -> str:
    template = _env.get_template("digest_email.html")
    stamp = _now_in(timezone_name).strftime("%Y-%m-%d %H:%M")
    rendered = [
        {
            **paper.__dict__,
            "chinese_summary": _nl2br(paper.chinese_summary),
            "brief_zh": _snippet(paper.chinese_summary),
            "brief_en": _en_snippet(paper.summary),
            "breakdown_rows": _breakdown_rows(paper),
            "feedback_links": _feedback_links(feedback_url, paper.id, stamp, feedback_signing_key),
        }
        for paper in papers
    ]
    deep_count = max(0, deep_papers)
    deep = rendered[:deep_count]
    brief = rendered[deep_count : deep_count + max(0, brief_papers)]
    return template.render(
        generated_at=stamp,
        timezone_label=timezone_name,
        summary=_nl2br(summary),
        deep_papers=deep,
        brief_papers=brief,
        total_papers=len(rendered),
        calendar=calendar,
        stars=_stars,
        secondary=secondary or [],
        talks=talks or [],
        trend=trend or [],
        sources=sources or [],
        pulse=pulse,
        radar=radar,
        feedback_url=feedback_url,
        window_days=window_days,
        attach_name=attach_name,
        P=palette(theme),
        theme_name=normalize(theme),
        edition=edition,
        period_label=period_label,
        news_counts=news_counts or {},
    )


def render_error_email_html(error_message: str, *, timezone_name: str = "Asia/Shanghai") -> str:
    template = _env.get_template("error_email.html")
    return template.render(
        generated_at=_now_in(timezone_name).strftime("%Y-%m-%d %H:%M"),
        error_message=error_message,
    )


def build_message(
    *,
    email_from: str,
    email_to: str,
    subject: str,
    html_body: str,
    attachment: tuple[str, bytes] | None = None,
) -> EmailMessage:
    """构造 MIME 邮件。单独抽出来是为了能在不发信的情况下自检附件。"""

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = email_from
    message["To"] = email_to
    message.set_content(
        "Your email client does not support HTML. "
        "Please view this digest in an HTML-capable client."
    )
    message.add_alternative(html_body, subtype="html")
    if attachment:
        filename, data = attachment
        message.add_attachment(
            data,
            maintype="text",
            subtype="html",
            filename=filename,
        )
    return message


def send_email(
    smtp_host: str,
    smtp_port: int,
    smtp_username: str,
    smtp_password: str,
    email_from: str,
    email_to: str,
    subject: str,
    html_body: str,
    attachment: tuple[str, bytes] | None = None,
    timeout: float = 30,
) -> None:
    if not all([smtp_host, smtp_username, smtp_password, email_from, email_to]):
        raise ValueError("SMTP settings are incomplete.")

    message = build_message(
        email_from=email_from,
        email_to=email_to,
        subject=subject,
        html_body=html_body,
        attachment=attachment,
    )

    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=timeout, context=ssl.create_default_context()) as server:
            server.login(smtp_username, smtp_password)
            server.send_message(message)
    else:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=timeout) as server:
            server.starttls(context=ssl.create_default_context())
            server.login(smtp_username, smtp_password)
            server.send_message(message)
