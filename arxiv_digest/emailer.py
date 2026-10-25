from __future__ import annotations

import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from html import escape

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
    finalize=lambda value: to_unicode(value) if isinstance(value, str) else value,
)


def _math(value: object) -> str:
    """模板过滤器 `| math`：把 LaTeX 片段转成能直接显示的 Unicode。

    放在渲染阶段而不是写进模型输出，是为了让同一份 digest.json
    在换成网页/终端输出时还能重新排版。
    """

    return to_unicode(str(value or ""))


_env.filters["math"] = _math


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
) -> str:
    template = _env.get_template("digest_email.html")
    rendered = [
        {
            **paper.__dict__,
            "chinese_summary": _nl2br(paper.chinese_summary),
            "brief_zh": _snippet(paper.chinese_summary),
            "brief_en": _en_snippet(paper.summary),
        }
        for paper in papers
    ]
    deep_count = max(0, deep_papers)
    deep = rendered[:deep_count]
    brief = rendered[deep_count : deep_count + max(0, brief_papers)]
    return template.render(
        generated_at=_now_in(timezone_name).strftime("%Y-%m-%d %H:%M"),
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
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(smtp_username, smtp_password)
            server.send_message(message)
    else:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(message)
