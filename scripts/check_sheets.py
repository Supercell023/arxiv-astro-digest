"""检查反馈表与热点档案是否接在同一份 Google 表格上、表头与端点是否正常。

用法::

    python -m scripts.check_sheets

只读：不会写任何行。所有检查失败都只影响退出码，不会改动配置或表格。

退出码：0 = 全部配置项通过；1 = 有检查失败；2 = 两项都没配置（无从检查）。
"""

from __future__ import annotations

import argparse
import sys
from urllib.request import Request, urlopen

from arxiv_digest import archive
from arxiv_digest.config import load_config

FEEDBACK_HEADER = ["item", "relevance", "usefulness", "t"]
ARCHIVE_HEADER_PREFIX = ["week_start", "week_label", "topic", "votes"]
MAX_CSV_BYTES = 1 << 20
USER_AGENT = "arxiv-astro-digest/check"


def fetch_csv(url: str, *, timeout: float = 20.0) -> str:
    """取回 CSV 文本；HTML 或空内容按错误处理（通常是权限/凭据/部署错误）。"""

    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - 只读固定配置的 URL
        raw = response.read(MAX_CSV_BYTES + 1)
    if len(raw) > MAX_CSV_BYTES:
        raise ValueError(f"反馈 CSV 超过 {MAX_CSV_BYTES // 1024} KiB 上限")
    text = raw.decode("utf-8", "replace").lstrip("\ufeff \t\r\n")
    if not text:
        raise ValueError("返回空内容：检查部署地址、访问权限与读取凭据")
    if text.startswith("<"):
        raise ValueError("返回的是 HTML 页面：检查访问权限与读取凭据，或部署版本是否正确")
    return text


def check_feedback(url: str, *, fetch=fetch_csv) -> tuple[bool, str]:
    """校验反馈 CSV 的表头与行数，并识别“CSV 指到了档案表”这种常见错配。"""

    lines = [line for line in fetch(url).splitlines() if line.strip()]
    if not lines:
        return False, "CSV 只有空白行"
    header = [cell.strip().strip('"').lower() for cell in lines[0].split(",")[:4]]
    if header == ARCHIVE_HEADER_PREFIX:
        return False, ("CSV 指向的是热点档案表，不是反馈表："
                       "检查 FEEDBACK_SHEET_CSV_URL 的 gid，或脚本属性 FEEDBACK_SHEET_NAME")
    if header != FEEDBACK_HEADER:
        return False, f"表头应为 {','.join(FEEDBACK_HEADER)}，实际是 {','.join(header)}"
    return True, f"表头正确，共 {len(lines) - 1} 行投票"


def check_archive(config, *, transport=None) -> tuple[bool, str]:
    """检查档案端点；未配置时返回 (True, 提示)。"""

    if not archive.ready(config):
        return True, "未配置 ARCHIVE_WEBHOOK_URL / ARCHIVE_TOKEN，跳过"
    handler = transport or archive.HttpTransport(
        config.archive_webhook_url, config.archive_token,
        timeout=config.archive_timeout, max_rows=config.archive_max_rows)
    try:
        payload = handler.health()
    except archive.ArchiveError as exc:
        return False, str(exc)
    if not payload.get("ok"):
        return False, f"端点返回 {payload!r}"
    return True, "端点可用"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=20.0, help="反馈 CSV 超时（秒）")
    args = parser.parse_args(argv)

    config = load_config()
    results: list[tuple[str, bool, str]] = []

    ok, message = check_archive(config)
    results.append(("热点档案端点", ok, message))

    if config.feedback_sheet_csv_url:
        try:
            ok, message = check_feedback(config.feedback_sheet_csv_url, fetch=lambda url: fetch_csv(url, timeout=args.timeout))
        except (OSError, ValueError) as exc:
            ok, message = False, f"{type(exc).__name__}: {exc}"
        results.append(("反馈 CSV", ok, message))
    else:
        results.append(("反馈 CSV", True, "未配置 FEEDBACK_SHEET_CSV_URL，跳过"))

    for label, ok, message in results:
        print(f"[{'OK' if ok else 'FAIL'}] {label}：{message}")

    if config.feedback_url and not config.feedback_signing_key:
        print("[WARN] 配了 FEEDBACK_URL 但没有 FEEDBACK_SIGNING_KEY：邮件按钮会被跳过。")

    if all(message.startswith("未配置") for _, _, message in results):
        print("[WARN] ARCHIVE_WEBHOOK_URL 与 FEEDBACK_SHEET_CSV_URL 都没有配置，无从检查。")
        return 2
    return 0 if all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
