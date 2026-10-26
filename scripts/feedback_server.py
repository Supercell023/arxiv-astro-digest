"""本地反馈端点：接住邮件里那六个按钮的点击。

邮件里的链接长这样（FEEDBACK_URL=http://127.0.0.1:8765/log）：

    .../log?item=2610.31660v1&rel=2&t=2026-10-12%2007%3A00
    .../log?item=2610.31660v1&use=1&t=...

这个脚本用标准库起一个只管写入的小 HTTP 服务：收到一次点击就更新
`config/feedback.json`（与 scripts/feedback.py 同一份文件），返回一个
"已记录" 的纯 HTML 页面。它是本机方案；想在任何设备上点、并且不想开电脑，
按 docs/FEEDBACK.md 换成 Google Apps Script + Google Sheet。

启动：

    python scripts/feedback_server.py                 # 127.0.0.1:8765
    python scripts/feedback_server.py --host 0.0.0.0 --port 8765

然后把 .env 里的 FEEDBACK_URL 指过去，重新生成一封邮件即可。
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import threading
import secrets
import time
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from arxiv_digest.feedback import (  # noqa: E402
    format_plain_text,
    load_feedback,
    record_legacy_vote,
    record_vote,
    save_feedback,
    combine_stores, store_from_dict, remember_papers,
)
from arxiv_digest.models import Paper
from arxiv_digest.state import load_state
from arxiv_digest.identity import canonical_id
from arxiv_digest.feedback_security import verify_params  # noqa: E402
from arxiv_digest.storage import delivery_lock

_LOCK = threading.Lock()


class FeedbackServer(ThreadingHTTPServer):
    """Bound connections and slow-body lifetime even when explicitly exposed."""
    daemon_threads = True
    request_timeout = 10

    def __init__(self, address, handler, *, max_connections=32):
        self._slots = threading.BoundedSemaphore(max_connections)
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, handler)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            request.settimeout(self.request_timeout)
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


def _load_papers(path: Path) -> dict[str, Paper]:
    import json
    from arxiv_digest.contracts import papers_from_payload
    if not path.is_file():
        return {}
    try:
        return {paper.id: paper for paper in papers_from_payload(json.loads(path.read_text(encoding="utf-8")))}
    except (OSError, ValueError, TypeError, AttributeError):
        return {}


def _page(title: str, body: str, *, tone: str = "#22d3ee") -> bytes:
    return (
        "<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        f"<title>{html.escape(title)}</title></head>"
        "<body style='margin:0;background:#05080f;color:#e8f0f8;"
        "font-family:-apple-system,BlinkMacSystemFont,\"Segoe UI\",sans-serif;'>"
        "<div style='max-width:560px;margin:12vh auto;padding:0 24px;'>"
        f"<div style='font-size:11px;letter-spacing:2px;color:{tone};font-weight:700;'>"
        "DAILY ARXIV DIGEST</div>"
        f"<h1 style='font-size:22px;margin:10px 0 14px;'>{html.escape(title)}</h1>"
        f"<div style='font-size:14px;line-height:1.8;color:#cfe0f0;'>{body}</div>"
        "</div></body></html>"
    ).encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    server_version = "ArxivDigestFeedback/2.0"

    def _send(self, status: int, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if not self.server.signing_key and urlparse("http://" + self.headers.get("Host", "")).hostname not in ("localhost", "127.0.0.1", "::1"):
            self._send(403, _page("主机无效", "请使用本机地址访问。"))
            return
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/") in ("", "/health"):
            self._send(200, _page("反馈端点在线", "打开邮件中的反馈链接，确认后记录。"))
            return
        if parsed.path.rstrip("/") != "/log":
            self._send(404, _page("找不到", "只支持 /log 与 /health。"))
            return
        params = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        if not params.get("item") or not verify_params(params, self.server.signing_key):
            self._send(403, _page("链接无效", "缺少论文编号、签名无效或链接已过期。"))
            return
        nonce = secrets.token_urlsafe(32)
        with _LOCK:
            self.server.nonces = {key: value for key, value in self.server.nonces.items() if value[0] > time.time()}
            if len(self.server.nonces) >= 1000:
                self._send(429, _page("请求过多", "稍后重试。"))
                return
            self.server.nonces[nonce] = (time.time() + 900, params)
        self._send(200, _page("确认这次反馈", "<p>" + html.escape(params["item"]) + "</p>"
                   + '<form method="post" action="/log"><input type="hidden" name="nonce" value="'
                   + nonce + '"><button type="submit">确认记录</button></form>'))

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/log":
            self._send(404, _page("找不到", "只支持 /log。"))
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        origin = self.headers.get("Origin")
        if not 0 < length <= 8192 or (origin and urlparse(origin).netloc != self.headers.get("Host")):
            self._send(403, _page("请求无效", "请重新打开邮件链接。"))
            return
        params = parse_qs(self.rfile.read(length).decode("utf-8", errors="replace"))
        nonce = (params.get("nonce") or [""])[0]
        with _LOCK, delivery_lock(self.server.feedback_path):
            challenge = self.server.nonces.pop(nonce, None)
            if not challenge or challenge[0] < time.time():
                self._send(403, _page("确认已过期", "请重新打开邮件链接。"))
                return
            query = challenge[1]
            if not verify_params(query, self.server.signing_key):
                self._send(403, _page("链接无效", "签名无效或链接已过期。"))
                return
            item_id = query["item"]
            store = combine_stores(store_from_dict(load_state(self.server.state_path).feedback),
                                   load_feedback(self.server.feedback_path))
            papers = _load_papers(self.server.digest_path)
            remember_papers(store, list(papers.values()))
            meta = store.history.get(canonical_id(item_id)) or {}
            paper = papers.get(item_id) or Paper(
                id=item_id, title=str(meta.get("title") or item_id), authors=list(meta.get("authors") or []),
                categories=list(meta.get("categories") or []), summary="", published="", updated="", pdf_url="", abs_url="")
            def level(name):
                try:
                    number = int(query.get(name, ""))
                except ValueError:
                    return None
                return number if number in (0, 1, 2) else None
            if query.get("v") in ("up", "meh", "down"):
                ok = record_legacy_vote(store, paper, query["v"])
            else:
                ok = record_vote(store, paper, relevance=level("rel"), usefulness=level("use"))
            if ok:
                save_feedback(self.server.feedback_path, store)
        self._send(200 if ok else 400, _page("已记录" if ok else "取值无效", html.escape(paper.title)))

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D401
        sys.stderr.write("[feedback] request completed\n")


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    feedback_path: Path | None = None,
    digest_path: Path | None = None,
) -> None:
    from arxiv_digest.config import load_config

    config = load_config()
    if host not in ("localhost", "127.0.0.1", "::1") and not config.feedback_signing_key:
        raise ValueError("非本机监听必须设置 FEEDBACK_SIGNING_KEY，并使用签名反馈链接。")
    server = FeedbackServer((host, port), _Handler)
    server.signing_key = config.feedback_signing_key
    server.nonces = {}
    server.state_path = Path(config.state_path)
    server.feedback_path = feedback_path or Path(config.feedback_path or "config/feedback.json")  # type: ignore[attr-defined]
    server.digest_path = digest_path or Path(config.digest_json_path or "digest.json")  # type: ignore[attr-defined]
    store = load_feedback(server.feedback_path)  # type: ignore[attr-defined]
    print(f"[ OK ] 反馈端点：http://{host}:{port}/log")
    print(f"       写入：{server.feedback_path}")  # type: ignore[attr-defined]
    print(f"       元数据：{server.digest_path}")  # type: ignore[attr-defined]
    print(format_plain_text(store))
    print("       把 FEEDBACK_URL 设成上面的 /log 地址（含 http://），Ctrl+C 退出。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[ .. ] 已停止")
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="本地反馈端点")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--feedback", default="", help="反馈文件路径，默认取 FEEDBACK_PATH")
    parser.add_argument("--digest", default="", help="digest.json 路径，默认取 DIGEST_JSON_PATH")
    args = parser.parse_args(argv)
    serve(
        host=args.host,
        port=args.port,
        feedback_path=Path(args.feedback) if args.feedback else None,
        digest_path=Path(args.digest) if args.digest else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
