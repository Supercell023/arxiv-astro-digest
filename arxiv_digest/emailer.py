from __future__ import annotations

import os
import smtplib
from datetime import datetime
from email.message import EmailMessage
from html import escape

from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup

from .models import Paper

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
_env = Environment(loader=FileSystemLoader(_TEMPLATES_DIR), autoescape=True)


def _stars(rating: int) -> str:
    rating = max(1, min(5, rating))
    return "\u2605" * rating + "\u2606" * (5 - rating)


def _nl2br(text: str) -> Markup:
    return Markup(escape(text).replace("\n", "<br>"))


def render_email_html(summary: str, papers: list[Paper]) -> str:
    template = _env.get_template("digest_email.html")
    return template.render(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        summary=_nl2br(summary),
        papers=[
            {
                **paper.__dict__,
                "chinese_summary": _nl2br(paper.chinese_summary),
            }
            for paper in papers
        ],
        stars=_stars,
    )


def render_error_email_html(error_message: str) -> str:
    template = _env.get_template("error_email.html")
    return template.render(
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        error_message=error_message,
    )


def send_email(
    smtp_host: str,
    smtp_port: int,
    smtp_username: str,
    smtp_password: str,
    email_from: str,
    email_to: str,
    subject: str,
    html_body: str,
) -> None:
    if not all([smtp_host, smtp_username, smtp_password, email_from, email_to]):
        raise ValueError("SMTP settings are incomplete.")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = email_from
    message["To"] = email_to
    message.set_content("Your email client does not support HTML. Please view this digest in an HTML-capable client.")
    message.add_alternative(html_body, subtype="html")

    if smtp_port == 465:
        with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
            server.login(smtp_username, smtp_password)
            server.send_message(message)
    else:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_username, smtp_password)
            server.send_message(message)
