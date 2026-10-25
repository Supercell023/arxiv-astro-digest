from __future__ import annotations

import argparse
import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path

from .arxiv_client import ArxivRateLimitError
from .config import Config, load_config
from .emailer import render_email_html, render_error_email_html, send_email
from .pipeline import run_pipeline


def _today_label(timezone_name: str) -> str:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(timezone_name)).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def main(config: Config, *, out: str | None = None) -> None:
    result = run_pipeline(config)
    attach_name = ""
    if config.email_attach_html:
        attach_name = f"arxiv-digest-{_today_label(config.digest_timezone)}.html"
    html_body = render_email_html(
        result.summary,
        result.enriched,
        result.calendar,
        timezone_name=config.digest_timezone,
        deep_papers=config.deep_papers,
        brief_papers=config.brief_papers,
        secondary=result.secondary,
        talks=result.talks,
        trend=result.trend,
        sources=result.sources,
        pulse=result.pulse,
        radar=result.radar,
        feedback_url=config.feedback_url,
        theme=config.email_theme,
        window_days=result.window_days,
        attach_name=attach_name,
    )
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_body, encoding="utf-8")
        logging.info(
            "已写出 %s（精读 %d / 速览 %d，共 %d 篇），未发送邮件。",
            path,
            len(result.enriched[: config.deep_papers]),
            len(result.enriched[config.deep_papers :]),
            len(result.enriched),
        )
        return

    if not result.enriched and not result.secondary and not result.talks:
        logging.warning("今日没有任何新内容（arXiv 无新投稿、二手源也无更新），跳过发信。")
        return

    subject = f"Daily arXiv Digest: {_today_label(config.digest_timezone)} ({len(result.ranked)} papers)"
    send_email(
        config.smtp_host,
        config.smtp_port,
        config.smtp_username,
        config.smtp_password,
        config.email_from,
        config.email_to,
        subject,
        html_body,
        attachment=(attach_name, html_body.encode("utf-8")) if attach_name else None,
    )


def notify_failure(config: Config, exc: Exception) -> None:
    subject = f"Daily arXiv Digest FAILED: {_today_label(config.digest_timezone)}"
    body = render_error_email_html(traceback.format_exc(), timezone_name=config.digest_timezone)
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


def _smtp_ready(config: Config) -> bool:
    return bool(config.smtp_host and config.email_from and config.email_to)


def run_cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m arxiv_digest.main",
        description="生成今日 arXiv 简报并发邮件。只想看效果就用 --out。",
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        help="把渲染结果写成 HTML，不发邮件（不需要 SMTP）",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    loaded_config = load_config()

    if args.out is None and not _smtp_ready(loaded_config):
        # 先查 SMTP 再跑流水线：否则抓完 arXiv、调完模型、跑了几分钟才在最后一步崩掉。
        lines = [
            "",
            "未配置 SMTP，无法发送邮件（SMTP_HOST / EMAIL_FROM / EMAIL_TO 至少要有）。",
            "",
            "  只想看效果（不发邮件）：",
            "      python -m arxiv_digest.main --out preview_live.html",
            "      python scripts/preview_digest.py --out preview_live.html",
            "",
            "  真要发邮件，请在 .env 里补齐：",
            "      SMTP_HOST= SMTP_PORT=587 SMTP_USERNAME= SMTP_PASSWORD= EMAIL_FROM= EMAIL_TO=",
            "",
        ]
        print(chr(10).join(lines), file=sys.stderr)
        return 2

    try:
        main(loaded_config, out=args.out)
    except ArxivRateLimitError as error:
        try:
            notify_failure(loaded_config, error)
        except Exception:
            logging.exception("Failed to send arXiv rate-limit notification email.")
            raise
        if loaded_config.arxiv_rate_limit_graceful:
            logging.warning("arXiv rate limit persisted; notification sent and workflow will exit successfully.")
            return 0
        raise
    except Exception as error:
        # 通知失败不能覆盖掉真正的错误：记日志，然后把原始异常抛出去。
        try:
            notify_failure(loaded_config, error)
        except Exception:
            logging.exception("Failed to send failure notification email; re-raising the original error.")
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
