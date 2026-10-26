"""全文 PDF 抓取与文本抽取（精读用）。

为什么精读必须读全文：摘要几乎从不写"样本怎么选"和"误差怎么校正"，
而这两项恰恰是读者每天用来判断"这结果能不能信"的东西。
缺了它们，精读卡就只是加长版速览。

这里刻意只做"够用"的抽取：
- pypdf 抽出来的两栏排版文本会有串行，但抓"含关键词的句子"足够；
- 先砍参考文献（通常占三分之一篇幅），再按预算保留"开头 + 关键词句窗口"；
- 任何一步失败都返回 None，绝不上抛 —— 精读退回摘要，信照发。
"""

from __future__ import annotations

import io
import json
import logging
import re
import ssl
import time
import urllib.error
import urllib.request
import hashlib
import multiprocessing
from pathlib import Path
from itertools import islice
from .storage import atomic_json

LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_CHARS = 40_000
DEFAULT_MAX_PAGES = 40
DEFAULT_TIMEOUT = 120
DOWNLOAD_ATTEMPTS = 2
MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_EXTRACT_CHARS = 500_000

_REFERENCE_RE = re.compile(
    r"\n\s*(?:\d+\.?\s*)?(?:References|REFERENCES|Bibliography|BIBLIOGRAPHY)\s*(?:\n|$)"
)
_PAGE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$", re.MULTILINE)
_HYPHEN_RE = re.compile(r"(\w)-\n(\w)")
_SPACE_RE = re.compile(r"[ \t\u00a0]+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_FIGURE_RE = re.compile(r"\bFig(?:ure)?\.?\s*([A-Z]?\d{1,2})\s*[:.]\s*(.{15,400})")

# 这些词出现的句子里，最容易找到"样本 / 筛选 / 系统误差 / 标定"。
_INTEREST_WORDS: tuple[str, ...] = (
    "sample",
    "catalog",
    "catalogue",
    "select",
    "mask",
    "completeness",
    "contaminat",
    "photometric redshift",
    "photo-z",
    "richness",
    "scaling relation",
    "calibrat",
    "systematic",
    "covariance",
    "miscentering",
    "mis-centering",
    "bias",
    "scatter",
    "likelihood",
    "posterior",
    "mock",
    "simulation",
    "uncertaint",
    "error budget",
    "aperture",
    "background",
    "member",
    # 图注：常常一句话就写完结果，比如"散点比 stacking 小 1.2 倍"
    "figure",
    "fig.",
    "caption",
)


def _ssl_context() -> ssl.SSLContext | None:
    """尽量带上 certifi 的证书链。

    Windows 上 pip 装的 Python 常常没有系统根证书，直接 urlopen 会报
    CERTIFICATE_VERIFY_FAILED。GitHub Actions 的 Linux 环境有系统证书，
    这里只是让本机也能跑。
    """
    try:
        import certifi
    except ImportError:
        return None
    try:
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # pragma: no cover - 证书文件损坏才会走到
        return None


def download_pdf(url: str, *, timeout: int = DEFAULT_TIMEOUT) -> bytes | None:
    """下载 PDF 字节。失败返回 None。"""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "arxiv-digest/1.0 (+https://github.com/)"},
    )
    last_error: Exception | None = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(  # noqa: S310 - 固定来自 arXiv 的 https 地址
                request, timeout=timeout, context=_ssl_context()
            ) as response:
                data = response.read(MAX_PDF_BYTES + 1)
                if len(data) > MAX_PDF_BYTES:
                    LOGGER.warning("PDF 超过 25 MiB 上限，回退摘要。")
                    return None
            break
        except Exception as exc:  # 网络抖动很常见，重试一次比整篇退回摘要划算
            last_error = exc
            if attempt < DOWNLOAD_ATTEMPTS:
                time.sleep(min(2 * attempt, 5))
    else:
        LOGGER.warning("下载 PDF 失败（%s）：%s", url, last_error)
        return None
    if not data.startswith(b"%PDF"):
        LOGGER.warning("下载内容不是 PDF（%s），%d 字节", url, len(data))
        return None
    return data


def _extract_pdf_text(pdf_bytes: bytes, *, max_pages: int = DEFAULT_MAX_PAGES) -> str:
    """用 pypdf 抽文本。未安装或解析失败返回空串。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        LOGGER.warning("未安装 pypdf，全文抽取跳过")
        return ""

    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        pages = islice(reader.pages, max(0, max_pages))
    except Exception as exc:
        LOGGER.warning("解析 PDF 失败：%s", exc)
        return ""

    chunks: list[str] = []
    used = 0
    for page in pages:
        try:
            chunk = (page.extract_text() or "")[:MAX_EXTRACT_CHARS - used]
            chunks.append(chunk)
            used += len(chunk)
            if used >= MAX_EXTRACT_CHARS:
                break
        except Exception as exc:  # 单页坏掉不该毁掉整篇
            LOGGER.debug("单页抽取失败：%s", exc)
    return "\n".join(chunks)


def _pdf_worker(data: bytes, pages: int, connection) -> None:
    try:
        connection.send(_extract_pdf_text(data, max_pages=pages))
    except Exception:
        connection.send("")
    finally:
        connection.close()


def extract_pdf_text(pdf_bytes: bytes, *, max_pages: int = DEFAULT_MAX_PAGES, timeout: int = 30) -> str:
    """Parse in a disposable process so a stuck parser can actually be stopped."""
    if len(pdf_bytes) > MAX_PDF_BYTES:
        return ""
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_pdf_worker, args=(pdf_bytes, max_pages, sender), daemon=True)
    try:
        process.start()
        sender.close()
        if receiver.poll(timeout):
            return receiver.recv()
        LOGGER.warning("PDF 解析超过 %s 秒，回退摘要。", timeout)
        return ""
    except (OSError, EOFError, RuntimeError):
        LOGGER.warning("无法完成隔离 PDF 解析，回退摘要。")
        return ""
    finally:
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
        receiver.close()
        sender.close()


def clean_text(raw: str) -> str:
    """修连字符断行、去掉孤立页码行、压掉多余空格。"""
    text = _HYPHEN_RE.sub(r"\1\2", raw)
    text = _PAGE_NUMBER_RE.sub("", text)
    text = text.replace("\u00ad", "")
    lines = [_SPACE_RE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def cut_references(text: str) -> str:
    """砍掉参考文献。只在命中位置足够靠后时才砍，避免误伤正文里的 "References"。"""
    match = _REFERENCE_RE.search(text)
    if match is not None and match.start() > len(text) * 0.3:
        return text[: match.start()]
    return text


def condense(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """按预算保留"开头 + 关键词句"，给模型一个高信息密度的窗口。"""
    if max_chars <= 0 or len(text) <= max_chars:
        return text

    head_budget = int(max_chars * 0.45)
    head = text[:head_budget]
    rest = text[head_budget:]
    picked: list[str] = []
    used = 0
    for sentence in _SENTENCE_SPLIT_RE.split(rest):
        lowered = sentence.lower()
        if not any(word in lowered for word in _INTEREST_WORDS):
            continue
        if used + len(sentence) > max_chars - head_budget:
            break
        picked.append(sentence.strip())
        used += len(sentence) + 1

    if not picked:
        return text[:max_chars].rstrip() + " …"
    return head.rstrip() + "\n…（以下为文中与样本/系统误差相关的句子）…\n" + "\n".join(picked) + "\n…"


def extract_figure_captions(text: str, *, limit: int = 3) -> tuple[str, ...]:
    """从正文里捞出 Figure N 的图注。

    图注是整篇论文里信息密度最高的一段文字之一：它常常直接写着
    "散点比 stacking 小 1.2 倍"、"灰色带是 68% 置信区间"。
    纯正则提取，不花模型的钱。

    注意：PDF 抽出的正文里，图注常常是 "Figure 3:" 独占一行、说明另起一行，
    所以先把换行压平再匹配，否则一条都抓不到。
    """
    flat = _SPACE_RE.sub(" ", text.replace("\n", " "))
    captions: list[str] = []
    seen: set[str] = set()
    for match in _FIGURE_RE.finditer(flat):
        label = match.group(1)
        body = match.group(2).strip(" .;:")
        stop = body.find(". ")
        if stop > 30:
            body = body[:stop]
        if len(body) < 15:
            continue
        if len(body) > 260:
            body = body[:260].rstrip() + "…"
        key = body[:24]
        if key in seen:
            continue
        seen.add(key)
        captions.append(f"图 {label}：{body}")
        if len(captions) >= limit:
            break
    return tuple(captions)


def fetch_fulltext(
    url: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    max_pages: int = DEFAULT_MAX_PAGES,
    timeout: int = DEFAULT_TIMEOUT,
    parse_timeout: int = 30,
    cache_dir: str = "",
) -> str | None:
    """下载 + 抽取 + 清洗 + 裁剪。任何一步失败返回 None（调用方回退摘要）。"""
    if not url:
        return None
    cache_key = hashlib.sha256(f"v1:{url}:{max_pages}".encode("utf-8")).hexdigest()
    cache_path = Path(cache_dir) / "fulltext" / (cache_key + ".json") if cache_dir else None
    if cache_path and cache_path.is_file():
        try:
            text = json.loads(cache_path.read_text(encoding="utf-8")).get("text")
            if isinstance(text, str) and len(text) >= 800:
                return condense(text, max_chars)
        except (OSError, ValueError, AttributeError):
            pass
    pdf_bytes = download_pdf(url, timeout=timeout)
    if pdf_bytes is None:
        return None
    text = clean_text(extract_pdf_text(pdf_bytes, max_pages=max_pages, timeout=parse_timeout))
    if len(text) < 800:
        LOGGER.warning("全文过短（%d 字符），回退摘要：%s", len(text), url)
        return None
    text = cut_references(text)
    if cache_path:
        try:
            atomic_json(cache_path, {"text": text})
        except OSError:
            LOGGER.warning("全文缓存不可写，继续本次分析。")
    return condense(text, max_chars)


def pdf_url_for(abs_url: str, pdf_url: str = "") -> str:
    """arXiv 的 abs 链接换成 pdf 链接；已有 pdf_url 时优先用它。"""
    if pdf_url:
        return pdf_url
    if "/abs/" in abs_url:
        return abs_url.replace("/abs/", "/pdf/")
    return abs_url
