from __future__ import annotations

import argparse
import logging
import sys
import traceback
from datetime import datetime
from pathlib import Path
from dataclasses import replace
from contextlib import nullcontext

from .arxiv_client import ArxivRateLimitError
from .config import Config, load_config
from .emailer import render_email_html, render_error_email_html, send_email
from .pipeline import commit_delivery, run_pipeline, update_diagnostics
from .storage import delivery_lock


def _today_label(timezone_name: str) -> str:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo(timezone_name)).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now().strftime("%Y-%m-%d")


def main(config: Config, *, out: str | None = None) -> None:
    if not out and not _smtp_ready(config):
        raise ValueError("SMTP settings are incomplete; use --out for a preview.")
    with nullcontext() if out else delivery_lock(config.state_path):
        _run(config, out=out)


def _run(config: Config, *, out: str | None = None) -> None:
    result = run_pipeline(config, persist_state=False, preview=bool(out))
    if result.report.get("skip_reason") == "weekly_already_delivered":
        logging.info("本期周报已经成功投递，跳过重复抓取和发信。")
        return
    edition = result.report.get("edition", "daily")
    attach_name = ""
    if config.email_attach_html:
        attach_name = f"arxiv-{edition}-{_today_label(config.digest_timezone)}.html"
    try:
        html_body = render_email_html(
            result.summary, result.enriched, result.calendar,
            timezone_name=config.digest_timezone,
            deep_papers=result.report.get("deep_papers", config.deep_papers),
            brief_papers=result.report.get("brief_papers", config.brief_papers),
            edition=edition, period_label=result.report.get("period_label", ""),
            news_counts=result.report.get("news_counts", {}),
            secondary=result.secondary, talks=result.talks, trend=result.trend,
            sources=result.sources, pulse=result.pulse, radar=result.radar,
            feedback_url=config.feedback_url, feedback_signing_key=config.feedback_signing_key,
            theme=config.email_theme, window_days=result.window_days, attach_name=attach_name,
        )
    except Exception as exc:
        result.report.update(delivery="failed", stage="render", error_type=type(exc).__name__)
        update_diagnostics(config, result)
        raise
    if out:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html_body, encoding="utf-8")
        result.report["delivery"] = "preview"
        update_diagnostics(config, result)
        logging.info(
            "已写出 %s（精读 %d / 速览 %d，共 %d 篇），未发送邮件。",
            path,
            len(result.enriched[: config.deep_papers]),
            len(result.enriched[config.deep_papers :]),
            len(result.enriched),
        )
        return

    if not result.has_deliverable:
        commit_delivery(config, result, delivery="skipped")
        logging.warning("本次没有未发送内容，已保存采集结果，跳过发信。")
        return

    label = result.report.get("period_label") if edition == "weekly" else _today_label(config.digest_timezone)
    subject = f"{'Weekly Research' if edition == 'weekly' else 'Daily arXiv'} Digest: {label} ({len(result.enriched)} papers)"
    try:
        send_email(
            config.smtp_host, config.smtp_port, config.smtp_username, config.smtp_password,
            config.email_from, config.email_to, subject, html_body,
            attachment=(attach_name, html_body.encode("utf-8")) if attach_name else None,
            timeout=config.smtp_timeout,
        )
    except Exception as exc:
        result.report.update(delivery="failed", stage="smtp", error_type=type(exc).__name__)
        update_diagnostics(config, result)
        raise
    commit_delivery(config, result)


def notify_failure(config: Config, exc: Exception) -> None:
    subject = f"Daily arXiv Digest FAILED: {_today_label(config.digest_timezone)}"
    details = traceback.format_exc()
    for secret in (config.openai_api_key, config.zotero_api_key, config.smtp_password, config.feedback_signing_key):
        if secret:
            details = details.replace(secret, "[REDACTED]")
    body = render_error_email_html(details, timezone_name=config.digest_timezone)
    send_email(
        config.smtp_host,
        config.smtp_port,
        config.smtp_username,
        config.smtp_password,
        config.email_from,
        config.email_to,
        subject,
        body,
        timeout=config.smtp_timeout,
    )


def _smtp_ready(config: Config) -> bool:
    return all((config.smtp_host, config.smtp_username, config.smtp_password, config.email_from, config.email_to))


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
    parser.add_argument("--theme", choices=("dark", "light"), help="临时覆盖 EMAIL_THEME")
    parser.add_argument("--edition", choices=("auto", "daily", "weekly"), help="auto 在配置时区的周日生成周报")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    loaded_config = load_config()
    if args.theme:
        loaded_config = replace(loaded_config, email_theme=args.theme)
    if args.edition:
        loaded_config = replace(loaded_config, digest_edition=args.edition)

    if args.out is None and not _smtp_ready(loaded_config):
        # 先查 SMTP 再跑流水线：否则抓完 arXiv、调完模型、跑了几分钟才在最后一步崩掉。
        lines = [
            "",
            "SMTP 配置不完整，需要 SMTP_HOST / SMTP_USERNAME / SMTP_PASSWORD / EMAIL_FROM / EMAIL_TO。",
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
        if args.out:
            raise
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
        if not args.out:
            try:
                notify_failure(loaded_config, error)
            except Exception:
                logging.exception("Failed to send failure notification email; re-raising the original error.")
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())
