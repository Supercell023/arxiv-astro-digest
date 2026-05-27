from __future__ import annotations

import logging
import traceback
from datetime import datetime

from .config import Config, load_config
from .emailer import render_email_html, render_error_email_html, send_email
from .pipeline import run_pipeline


def main(config: Config) -> None:
    result = run_pipeline(config)
    html_body = render_email_html(result.summary, result.enriched)
    subject = f"Daily arXiv Digest: {datetime.now().strftime('%Y-%m-%d')} ({len(result.ranked)} papers)"
    send_email(
        config.smtp_host,
        config.smtp_port,
        config.smtp_username,
        config.smtp_password,
        config.email_from,
        config.email_to,
        subject,
        html_body,
    )


def notify_failure(config: Config, exc: Exception) -> None:
    subject = f"Daily arXiv Digest FAILED: {datetime.now().strftime('%Y-%m-%d')}"
    body = render_error_email_html(traceback.format_exc())
    send_email(
        config.smtp_host,
        config.smtp_port,
        config.smtp_username,
        config.smtp_password,
        config.email_from,
        config.email_to,
        subject,
        body,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    loaded_config = load_config()
    try:
        main(loaded_config)
    except Exception as error:
        try:
            notify_failure(loaded_config, error)
        finally:
            raise
