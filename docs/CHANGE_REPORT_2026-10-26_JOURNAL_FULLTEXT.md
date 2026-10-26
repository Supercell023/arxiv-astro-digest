# 期刊论文全文：DOI 反查 arXiv 预印本（v0.15.0）

日期：2026-10-26（工作区，未发布）
范围：`arxiv_digest/preprints.py`（新增）、`journals.py`、`pipeline.py`、`analysis.py`、`config.py`、`cache.py`、`tests/test_preprints.py`（新增）、`.env.example`、`CHANGELOG.md`。

## 1. 问题

`_fetch_fulltexts` 对 `doi:` 开头的论文直接 `continue`，期刊论文只能读 Crossref 摘要。实测三种取全文的路径都被挡住：

| 路径 | 结果 |
| --- | --- |
| IOP 文章 PDF `iopscience.iop.org/article/{doi}/pdf` | `200 text/html`（反爬页，非 PDF） |
| `https://doi.org/{doi}` | `403 Forbidden` |
| Crossref `/works/{doi}` 的 `link` | 声明 `application/pdf`，脚本取不到 |

用户批准方案 A：**用标题反查 arXiv 预印本取 PDF，但来源仍标期刊名**。

## 2. 实现

### 2.1 `preprints.py`

- 查询 `export.arxiv.org/api/query`，先 `ti:"<标题>"`，无候选再 `all:"<标题>"`，`sortBy=relevance`。
- 三道匹配校验，全过才认：
  - 归一化标题（NFKC + 小写 + 非字母数字转空格）的 `SequenceMatcher` 相似度 ≥ 0.90；
  - 作者：第一作者姓氏命中，或双方姓氏交集 ≥ 2（缺作者信息时放行）；
  - 年份差 ≤ 2。
- 命中返回 `PreprintMatch`（arxiv_id、title、summary、published、authors），提供 `.pdf_url` / `.abs_url`。
- 正负缓存：命中 180 天、未命中 14 天，落在 `<cache_dir>/preprints/<sha256>.json`；请求间隔 ≥ 3 秒。
- `preprint_pdf_url(paper, ...)`、`enrich_summary(paper, ...)`：后者只在 `paper.summary` 为空时用预印本摘要补，`replace()` 后 `venue`、`doi`、`id` 原样保留。

### 2.2 管线接线

- `_fetch_fulltexts`：`doi:` 论文先 `preprint_pdf_url()`，命中则当普通 URL 下载解析；未命中记 `期刊论文没有可用的 arXiv 预印本，该篇精读回退摘要`。日志明确写"来源仍记 ApJ/A&A"。
- `_merge_journals`：`fetch_journal_papers(..., preprocess=fill_abstract)`，在主题闸门**之前**补摘要。Nature 系在 Crossref 经常只有标题，此前会因"标题无主题词"被闸门误杀；补完再过闸门。额度由 `PREPRINT_ABSTRACT_LOOKUPS` 控制（默认 4）。
- 补全后再 `dispatch(..., hint=paper.pack_hint, title=title+summary)` 重算领域包提示。

### 2.3 顺带修掉的实跑问题

- **数值护栏整段清空**：`background` / `verdict` / 七问里只要有一个数字不可回查，此前会整段置为"未提供/未检出"。现在先 `_salvage_text()` 按分句过滤，只删含不可回查数字的那半句；整段无可用分句才回退。日志 `省略含不可回查数字的分句：<字段>`。
- **verdict 回退**：回查失败时用 `result` 截 60 字作一句话结论，不再出现"摘要未提"当结论。
- 缓存命名空间与回收：`cache.prune_cache` 覆盖 `preprints/`；`preprints` 缓存键含 `v1:` 前缀，改匹配参数时不会误命中。

## 3. 验证（实际执行）

### 3.1 离线

- `python -B -m unittest discover -s tests -q` → **Ran 126 tests, OK**（新增 19 项：解析、匹配、缓存命中/未命中、PDF URL、摘要补全、preprocess 先于 predicate、管线接线、salvage 分句、配置默认值）。
- `python -B -m arxiv_digest.selfcheck` → **26 项全部通过**。
- `python -B -m pip check` → No broken requirements found.
- `node tests/check_google_feedback.cjs` → 本会话 PATH 无 node，**未复现**（沿用 v0.14 记录，不能算本轮通过）。

### 3.2 真实外网（一次）

用 `digest.json` 里 6 篇期刊条目（正是本地实跑 #2 的产物）逐篇 `resolve_preprint()`：

| 期刊 | DOI | 结果 |
| --- | --- | --- |
| ApJ | 10.3847/1538-4357/aea55e | 命中 arXiv:2604.06318，摘要 1733 字 |
| A&A | 10.1051/0004-6361/202662252 | 命中 arXiv:2610.28752，摘要 1909 字 |
| ApJ | 10.3847/1538-4357/aeaa36 | 命中 arXiv:2604.05022，摘要 1671 字 |
| ApJ | 10.3847/1538-4357/aea61a | 未命中（Radio Quasar positive feedback） |
| A&A | 10.1051/0004-6361/202660155 | 未命中（Euclid Q1 XLIII） |
| ApJ | 10.3847/1538-4357/aea97b | 未命中（Hot CGM） |

命中率 3/6，与预估的 50–70% 一致。对首个命中取 PDF：抽取 **39,968 字符**，`id` 仍为 `doi:10.3847/1538-4357/aea55e`。

再调 `pipeline._fetch_fulltexts` 取 digest 前 2 篇期刊：

```
要求 2 篇，取到 2 篇全文
  ApJ   doi:10.3847/1538-4357/aea55e     118165 字符  venue=ApJ
  A&A   doi:10.1051/0004-6361/202662252    80514 字符  venue=A&A
```

字符数大是因为 `.env` 设了 `FULLTEXT_MAX_CHARS=400000`（默认 40000）；`fetch_fulltext` 按配置裁剪，不是未生效。pypdf 报 `Exceeded 5000 form XObject invocations`（库内上限保护），文本仍成功抽出。

### 3.3 未验证

- 未用真实 Nature Astronomy 数据重跑整轮：摘要补全逻辑有单测（`test_preprocess_runs_before_predicate`），但"NatAs 命中率是否真的提升"没有端到端数字。
- 未实跑含分析的完整投递（会产生模型费用）；期刊论文进入精读卡后的渲染只在离线测试里覆盖。
- SMTP 投递、反馈端点、Zotero、云端 Actions 仍未验收。

## 4. 行为变化与迁移

- 新增 `PREPRINT_ENABLED`（默认 true）、`PREPRINT_TIMEOUT=20`、`PREPRINT_MAX_RESULTS=5`、`PREPRINT_ABSTRACT_LOOKUPS=4`。关掉 `PREPRINT_ENABLED` 即回到"期刊只读摘要"。
- 每次运行最多为 4 篇无摘要期刊发 arXiv 查询（3 秒间隔 + 缓存），最坏多约 12 秒。
- `digest.json` 的期刊条目 `summary` 可能来自预印本；`venue`/`doi`/`id` 不变，UI 仍显示期刊名。
- 缓存目录新增 `preprints/`，`prune_cache` 会按 30 天 / 256 MiB 回收。

## 5. 剩余风险

- 标题反查依赖 arXiv 元数据质量；改名、会议版、撤稿版本可能匹配不上或匹配到旧版（年份 ±2 已限制）。未命中一律回退摘要，不会出错但会少读。
- 预印本与正式版可能有小差异（审稿改动）；精读卡不区分"预印本全文"与"期刊全文"，只在数据边界里注明。
- pypdf 对复杂排版（大量 form XObject）只能抽取部分内容，已按 40 页 / 400k 字符设界。
