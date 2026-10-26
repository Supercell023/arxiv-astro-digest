# v0.15.1 本地试跑复查报告（2026-10-26）

触发：用户在本地跑了一次真实预览（22:16，`run_report.json` 与 `preview.html` 都在工作区），要求复查、修复、提交并推送。

## 1. 复查看到的证据

- `run_report.json`：`delivery=preview`、474.1 秒、采样 53 篇、入选 14 篇、精读 4 篇、`analysis_failures=0`、二手 30 条、19 次模型请求（输入 61,666 / 输出 48,675 / 思考 26,237）、`errors=[]`。
- `preview.html`：分数构成/研究背景/研究方法各 4 张卡都在；期刊论文进了精读（venue 徽标 AJ/MNRAS/A&A，DOI 链接 6 个）；但出现 `全文节选未检出` 23 处。
- 独立快照 `runs/digest-e7bb5a93c1f94720a177b1f933757765.json` 显示：4 篇精读的 `analysis.source_text` 长度都是 36,002，HSC 论文的 `source_text` 不含 `FastSpecFit`/`245,236`/`BGS`——这些内容在 PDF 的 methods 节，被截断点挡在外面。
- 同一份 HTML 里推荐依据显示 `keyword: galaxy &amp;middot; keyword: ...`；`load_config().arxiv_keywords` 有 17 项，其中 `galaxy evolution` 重复一次。

## 2. 根因与修复

| 现象 | 根因 | 修复 |
| --- | --- | --- |
| 精读大面积"未检出" | `analysis` 用 `_clip(fulltext, 36_000)` 硬截前 36k 字符，长论文只剩引言 | 新增 `_excerpt`，超预算走 `fulltext.condense`：开头 45% + 含样本/筛选/系统误差/协方差关键词的句子 |
| 一句话结论显示"全文节选未检出" | verdict 数字回查失败后回退只认旧常量 `摘要未提/全文未提`，匹配不到 `全文节选未检出` | `_strip_missing_notes` 先删标记，空则用 `result[:60]`，再不行才写"原文未提供可核对的一句话结论" |
| 研究背景里出现占位句 | 模型把事实块里的占位标记抄进正文 | 同上，`background`/`method_detail` 一并清理 |
| `&amp;middot;` | Jinja 表达式里写了 HTML 实体，`autoescape=True` 二次转义 | 模板改用真实字符 `" · "` |
| 推荐依据重复、分数虚高 | `ARXIV_KEYWORDS` 重复项各自计分 | `load_config()` 对 `arxiv_keywords`/`arxiv_categories` 按顺序忽略大小写去重 |

## 3. 验证（实际执行）

- `python -B -m unittest discover -s tests -q` → **Ran 129 tests, OK**（新增 3 项：全文节选覆盖方法节、占位标记不写回正文、重复关键词去重）。
- `python -B -m arxiv_digest.selfcheck` → **26 项全部通过**（渲染检查新增 `&amp;middot;` 断言）。
- `python -B -m pip check` → No broken requirements found.
- 不花钱的节选复核：`.cache/fulltext` 里那份 118,165 字符的 PDF，旧节选（36,000）缺 `sampling`/`systematics`/`covariance`，新节选（35,868）三者全含。
- 快照回放：`scripts/build_preview.py --replay runs/digest-e7bb….json` 重渲染后 `&amp;middot;` 计数 0、` · ` 出现 51 次。
- `node` 不在本会话 PATH，10 项 Google Apps Script 契约仍未复现。

## 4. 尚未验证 / 已知边界

- 真实模型抽取质量：`deepseek-flash` 在 36k 字符节选上仍会把 `data`/`processing`/`result` 抽空（复核 sapphire 那篇时只抽到 `method` + 3 个数字 + 4 个术语）。这是模型能力边界，不是本轮代码改动引入的；如需更好效果，应换更强的抽取模型或缩减单篇输入。
- SMTP 投递、反馈端点（本机 / Apps Script）、Zotero、云端 Actions 均未在本轮验收。
- 世界新闻当天缺天文是本机重复投递 + 时段节流导致（早上 13:30 的定时任务已抓过 9 个天文源），不是配置错误；换 `STATE_PATH` 重放即可看到天文条目。
