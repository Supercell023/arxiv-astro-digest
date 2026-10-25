# arxiv-astro-digest 项目评审与扩展路线

> 评审方式：全量静态阅读（13 个 Python 文件 + 2 个模板 + workflow + 依赖/配置）。
> 环境限制：当前沙箱内 PowerShell 无法启动（`0xC0000142`，DLL 初始化失败），因此**未实际运行代码、未执行测试**。文中"会出错"类结论均基于代码路径推导，标注了具体行号，便于复核。
> 工作区未发现 `.git` 元数据，仓库似乎尚未初始化。

---

## 1. 项目在做什么

一句话：**每天北京时间早上，自动抓取指定 arXiv 分类在"昨天"发布的新论文，用关键词 + Zotero 收藏偏好排序打星，再用 LLM 生成中文总览和逐篇摘要，最后以 HTML 邮件发到邮箱。**

面向的是单用户、无人值守的个人科研信息流。整个运行依赖 GitHub Actions，无服务器、无数据库、无常驻进程。

### 1.1 数据流

```text
GitHub Actions (cron 00:37 UTC = 08:37 北京)
  └─ python -m arxiv_digest.main
       ├─ load_config()                env / .env → Config（32 个字段的单层 dataclass）
       ├─ fetch_recent_papers()        arXiv Atom API，分页 + 429 指数退避
       ├─ fetch_zotero_preferences()   Zotero API → 带正/负标签的偏好条目
       ├─ rank_papers()                关键词命中 + Zotero 词重叠 + 分类加成
       ├─ filter_by_min_rating()       ≥ MIN_RATING 后截断到 MAX_PAPERS
       ├─ generate_digest_content()    一次 LLM 调用 → 总览 + 逐篇中文摘要（失败则降级）
       ├─ render_email_html()          Jinja2 表格模板（内联样式）
       └─ send_email()                 SMTP_SSL(465) 或 STARTTLS(其他端口)

异常分支：
  ArxivRateLimitError ──► 告警邮件 ──► 按 ARXIV_RATE_LIMIT_GRACEFUL 决定是否 exit 0
  其他异常 ─────────────► 告警邮件（含 traceback）──► 重新抛出，Actions 变红
```

### 1.2 代码地图

| 文件 | 行数 | 职责 | 质量印象 |
| --- | --- | --- | --- |
| `arxiv_digest/arxiv_client.py` | 242 | Atom XML 解析、本地日期窗口、分页、重试与退避 | 最复杂，重试策略有冗余分支 |
| `arxiv_digest/config.py` | 110 | 环境变量读取、点号分隔 CSV 解析 | 无校验，单层 32 字段 |
| `arxiv_digest/models.py` | 29 | `Paper` / `ZoteroPreference` 两个 frozen dataclass | 简洁，但可变字段与 frozen 冲突 |
| `arxiv_digest/recommender.py` | 69 | 关键词打分、星级映射、排序、阈值过滤 | 公式过于简单，有失真项 |
| `arxiv_digest/llm.py` | 123 | 一次批量 prompt、JSON 解析、降级 | 单点失败会全量退化 |
| `arxiv_digest/emailer.py` | 79 | Jinja2 渲染、SMTP 发送 | 缺超时/重试/Date 头 |
| `arxiv_digest/zotero_client.py` | 103 | 偏好拉取、词袋相似度 | 分词与归一化偏粗糙 |
| `arxiv_digest/pipeline.py` | 59 | 串联：抓取 → 排序 → 过滤 → 摘要 | 清晰的编排层 |
| `arxiv_digest/main.py` | 68 | CLI 入口、告警、退出码 | 异常处理有覆盖问题 |
| `scripts/preview_digest.py` | 30 | 本地预览，不发信 | 与 main 逻辑重复 |
| `templates/digest_email.html` | 175 | 邮件主体 | 视觉完成度高，兼容性有坑 |
| `templates/error_email.html` | 54 | 失败告警邮件 | 硬编码作者仓库 URL |

分层是干净的：**数据获取 / 领域模型 / 推荐 / 摘要 / 渲染 / 编排** 各自独立，没有循环依赖，`pipeline.py` 只做组合。这是后续扩展最好的基础。

---

## 2. 优点（值得保留的设计决策）

1. **降级策略是系统性设计，不是零散 try/except。** LLM 不可用 → 英文摘要兜底；Zotero 不可用 → 只靠关键词；arXiv 持续 429 → 告警并正常退出。对无人值守的定时任务来说，这是最关键的一条，作者显然踩过坑才补上的。
2. **尊重上游限流。** 分页 + 页间延迟 + 指数退避 + jitter + 优先遵守 `Retry-After`，比绝大多数同类玩具项目认真。
3. **"429 不标红"的产品判断正确。** 把基础设施抖动和代码 bug 区分开，避免每天收到红色构建、最终忽略所有告警。
4. **评分可解释。** 邮件里展示 `keyword: xxx` / `Zotero match: ...` 和原始分数，用户能理解"为什么推这篇"。可解释性比分数精度更重要。
5. **邮件 HTML 用 table + 内联样式**，是邮件客户端兼容性的正确做法；视觉层级（星级 → 标题 → 作者 → 依据 → 分类 → 中文摘要 → 折叠英文 → PDF）信息密度合理。
6. **12-factor 配置。** 全环境变量驱动，Secrets / Variables 分离得当，fork 后改配置即可用，不需要改代码。
7. **零基础设施。** 没有数据库、没有容器、没有常驻服务，GitHub Actions 免费额度即可跑，维护成本极低。
8. **类型与版本约束。** `from __future__ import annotations`、完整类型签名、`requires-python >= 3.11`、依赖全部钉版本、`mypy strict` 配置已就位（只是没跑）。
9. **中文摘要的提示词写得克制。** 明确要求"不要夸大结论""只来自摘要时保持谨慎措辞"，这在天文领域很重要（很多 arXiv 摘要的结论会被 LLM 放大）。

---

## 3. 问题清单

严重度定义：**P0** = 会直接导致错误结果或运行失败；**P1** = 明显损害质量、可维护性或可扩展性；**P2** = 体验与工程细节。

### 3.1 P0：会导致错误结果或运行失败

| # | 位置 | 问题 | 影响 |
| --- | --- | --- | --- |
| 1 | `.github/workflows/daily-arxiv-digest.yml:5` vs `README.md:207` | cron 是 `37 0 * * *`（00:37 UTC / 08:37 北京），README 写"UTC 03:37 / 北京 11:37" | 文档与行为不一致。按 README 理解会以为 11:37 发信，实际是 08:37；若有人照 03:37 去改，会变成 11:37 触发但摘要窗口仍是"北京昨天"，跨日边界出错 |
| 2 | `llm.py:70-111` | 12 篇论文拼成一个巨大 prompt，一次请求，`json.loads` 无任何容错 | 任何一篇导致输出被截断（`finish_reason=length`）、模型加了 ` ```json ` 围栏、或某个兼容端点不支持 `response_format`，**整份摘要全部退化为英文原文**。应该是分批 + 单批失败只降级该批 |
| 3 | `recommender.py:52` | `category_bonus = 0.2 * len(paper.categories)`，按论文 cross-list 的分类"数量"加分，与用户关注什么无关 | 纯失真项。一篇挂了 6 个分类但主题毫不相干的论文得 1.2 分 → 直接 3 星。而所有抓回来的论文本来就都命中搜索分类，等于给所有人发"参与奖" |
| 4 | 全局 | 零测试；CI 只 `pip install` + 运行，不跑 lint / 类型检查 | 任何重构都无安全网。`mypy strict` 是摆设。改评分公式、改解析、改模板全靠"明天看看邮件对不对" |
| 5 | `main.py:16`、`emailer.py:30` | 邮件标题和渲染时间用 `datetime.now()`，即 runner 的 UTC 时间，而摘要窗口用 `DIGEST_TIMEZONE` | 标题日期（UTC 当天）与内容窗口（北京"昨天"）可能差一天，用户看到 `2026-10-24 (12 papers)` 但里面是 10-23 的论文 |
| 6 | `main.py:59-63` | `except Exception as error: try: notify_failure(...) finally: raise`。若发信本身失败，`finally: raise` 重抛的是**发信异常**而不是原始异常 | 真正的根因（比如 arXiv 解析失败）被 SMTP 错误覆盖，日志里看不到，排查方向被带偏。另外 `load_config()` 在 try 之外，配置写错时直接崩、**不发任何邮件** |
| 7 | `emailer.py:71-78` | 只区分 465(SSL) 与"其他全部 STARTTLS"；`smtplib` 无 `timeout`；发送无重试 | 端口 25 的无 TLS 中继、需要隐式 SSL 的非 465 端口、以及网络抖动都会失败。无 timeout 时 Actions 可能挂到 45 分钟上限 |
| 8 | 工作流 `timeout-minutes: 45` vs 退避预算 | 4 页 × 5 次尝试 ×（60+120+240+480s）≈ 最坏 60 分钟 | 触发限流时 workflow 会被强杀，连"限流告警邮件"都来不及发，正好破坏了作者最在意的 graceful 设计 |
| 9 | `templates/error_email.html:37` | 硬编码 `github.com/Supercell023/arxiv-astro-digest` | fork 后告警邮件把人引到原作者仓库，且泄漏作者身份。应走配置项 |

### 3.2 P1：质量与可维护性

| # | 位置 | 问题 | 建议 |
| --- | --- | --- | --- |
| 10 | `models.py:7-21` | `frozen=True` 但字段是 `list`，语义自相矛盾；`__hash__` 实际不可用（list 不可哈希）；三处用 `Paper(**paper.__dict__)` 复制 | 改 `dataclasses.replace()`；或把 `score/rating/chinese_summary` 拆到独立的 `RankedPaper` / `EnrichedPaper`，让领域对象真正不可变 |
| 11 | `zotero_client.py:97` | `similarity = len(overlap) / len(preference_terms)`：只对偏好侧归一化，不对论文侧；无 IDF；`_normalize` 用 `[a-zA-Z][a-zA-Z0-9-]{2,}`，`the` / `and` 等停用词会被当有效词 | 标准做法是 BM25 或至少 `|A∩B| / sqrt(|A|·|B|)` + 停用词表 + IDF 加权；否则一两个高频词就能造出"高相似" |
| 12 | `zotero_client.py:91-101` | 相似度对所有偏好求和且无上限；`preferences` 最多 100 条 | 命中 10 个收藏就能把分数堆到 10+，星级彻底失去区分度。应 `tanh(total/scale)` 或取 top-k 加权 |
| 13 | `recommender.py:26` | `\b{keyword}\b` 无词形还原：`galaxy` 匹配不到 `galaxies`，`star formation` 匹配不到 `star forming`（连字符已被替换，所以 `star-forming` 恰好能中） | 轻量词干化（`stemming` 库，纯 Python 无重依赖）或为关键词生成常见变体 |
| 14 | `arxiv_client.py:130-148` | 所有 `HTTPError` 都重试：400（查询语法错）、403（被封）也白等 10+20+40s；503 的 `Retry-After` 被忽略（只对 429 读） | 仅重试 429/5xx；`Retry-After` 统一处理（它也可能是 HTTP-date 格式，当前 `isdigit()` 判断会漏） |
| 15 | `config.py` 全局 | 无任何校验：`SMTP_PORT` 可填 99999、`ARXIV_PAGE_SIZE` 可超 arXiv 的 2000 上限、`MIN_RATING` 可填 7（被静默夹到 5）、`OPENAI_BASE_URL` 拼错无提示 | 用 `pydantic-settings` 或手写 `validate()`；错误信息里给出期望范围 |
| 16 | `config.py:70` | `smtp_password` 等字段参与 `__dict__`/`repr` | 加 `field(repr=False)`，避免哪天 `logger.info(config)` 把密码打进 Actions 日志 |
| 17 | `pyproject.toml` | 没有 `[project.dependencies]`、`license`、`readme`、`[project.scripts]`；依赖另有一份 `requirements.txt` | `pip install .` 装不出依赖；双轨漂移。用 `pyproject` 单一事实源 + `requirements.txt` 由 `pip-compile` 生成，或反过来 |
| 18 | 全局 | 无跨天状态：同一篇论文连续两天被推；workflow 重跑会重复发信 | 用 `state/seen.json`（配 `actions/cache` 或提交回仓库）做 ID 去重；顺手支持"失败了重跑不重复" |
| 19 | `main.py` / `scripts/preview_digest.py` | 预览脚本与主流程各写一遍编排；无 `--dry-run` / `--out digest.html` / `--limit` 参数 | 用 `argparse` 统一入口，`--dry-run` 只渲染不发信，`--out` 落盘 HTML 便于调试样式 |
| 20 | `emailer.py:18,39` + 模板 `:65-66` | `_stars()` 传入模板但模板自己用 `range()` 画星，死代码 | 删；或模板改用 `{{ stars(paper.rating) }}` 统一逻辑 |
| 21 | `llm.py:15-16` | 无 API key 时"中文摘要"里塞的是英文原文，还带着中文前缀"这篇论文的英文摘要要点：" | 要么明确标注"未生成中文摘要"，要么本地做标题翻译兜底；现在这样会让用户误以为模型坏了 |
| 22 | `llm.py:56-58` | `MAX_LLM_PAPERS < MAX_PAPERS` 时，排名靠后的论文只有英文原文，而 UI 位置相同 | 至少在模板上标注"（LLM 配额已用尽）"，或优先给高分论文做摘要（当前确实按分数顺序取前 N，OK） |
| 23 | `llm.py:102-109` | 未设 `timeout` / `max_tokens` / `temperature` | 单次请求可能挂很久；输出长度不可控；同一输入两次跑结果不同（默认 temperature 1） |
| 24 | `arxiv_client.py:123` | UA 是 `daily-arxiv-digest/0.1`，无联系邮箱；未用 API 礼仪建议的 `mailto` 参数 | arXiv 明确要求可识别的 UA。加 `mailto=you@example.com` 既礼貌也降低被限流概率 |
| 25 | `zotero_client.py:56-64` | 无分页（`limit=100` 封顶）、无 `Zotero-API-Version` 头、无版本缓存、无重试 | 收藏多的人偏好只取最近 100 条；`If-Modified-Since` + `Last-Modified-Version` 可让每天只拉增量 |
| 26 | 全局 | 无运行报告 artifact：抓了多少、过滤掉多少、LLM 花了多少 token、哪步耗时 | Actions 里加 `--out` 的 JSON 报告 + upload-artifact，才可能持续调优 |

### 3.3 P2：体验与细节

| # | 位置 | 问题 |
| --- | --- | --- |
| 27 | `templates/digest_email.html:18` | 头部只有 `background: linear-gradient(...)`，没有 `background-color` 兜底。Outlook（Word 渲染引擎）不支持渐变 → 背景变白，白色标题**直接看不见**。加 `background-color:#1a1a2e` 即可 |
| 28 | `templates/digest_email.html:129` | `<details>` 在 Gmail / Outlook 中不支持，英文摘要可能永久隐藏或异常展开。邮件正文里折叠交互本质上不可靠，建议改为"摘要默认截断 + 链接到 arXiv" |
| 29 | 模板全局 | 中文标签（"LLM 总览""中文摘要""英文原始摘要"）硬编码，无 i18n；界面中英混杂（标题英文、正文中文、推荐理由英文） |
| 30 | `emailer.py:68` | 纯文本替代内容写死一句英文提示，没有真正的 text/plain 版本；无 `Date`、无 `Message-ID`、无 `List-Unsubscribe` |
| 31 | `send_email` 签名 | 只支持单个 `EMAIL_TO` 字符串，不支持多收件人 / CC / BCC |
| 32 | `.github/workflows/*.yml` | 无 `permissions`、无 `concurrency`（手动触发与定时触发会并发跑）、无 pip 缓存、无 `workflow_dispatch` 输入参数（想临时换个分类得改 Variable） |
| 33 | `README.md` | 前 22 行是 changelog 混在 README 里；没有架构图、没有 LICENSE、没有贡献指南；配置表与代码默认值需手动同步（已经出现 cron 这种漂移） |
| 34 | `main.py:31` | 失败邮件附带完整 traceback；若收件人是多人或邮件转发，可能暴露内部路径与配置细节 |

---

## 4. 评分系统诊断（最该先动的地方）

当前公式（`recommender.py:47-64`）：

```text
score = Σ_关键词 min(3.0, 0.8 × 命中次数)        # 每个词独立封顶 3.0
      + Σ_偏好 (重叠比例 × 标签权重)              # 无上限
      + 0.2 × 分类数量                            # 与兴趣无关
```

星级阈值：`≥5.0 → 5★`，`≥3.0 → 4★`，`≥1.2 → 3★`，`≥0.4 → 2★`，否则 1★。

用几个例子说明失真：

| 情形 | 分数 | 星级 | 问题 |
| --- | --- | --- | --- |
| 零关键词命中，但挂了 6 个分类 | 1.2 | 3★ | 完全跑题的论文也能"中等相关" |
| 单个关键词命中 4 次 | 3.0 + 0.2~0.4 | 4★ | 一个高频泛词就能顶到 4 星 |
| 2 个关键词各命中 4 次 | 6.0+ | 5★ | 阈值 5.0 太容易被击穿 |
| 命中 10 个 Zotero 收藏 | 可达 10+ | 5★ | 分数无上界，星级不可比 |

结论：**阈值是硬的，分数是软的且无上界**，导致星级在同一封邮件内可比、跨天不可比，且对 cross-list 数量敏感。

建议（改动小、收益大）：

```text
relevance  = 关键词覆盖率（命中词数 / 配置词数），加 IDF 或位置权重（标题命中 ×2）
affinity   = BM25(论文, 用户画像) 归一到 [0,1]，画像 = Zotero 正/负标签条目 + 历史高分论文
freshness  = 与窗口的关系（可选，通常都是新的）
score      = 0.6 × relevance + 0.4 × affinity        # 归一到 [0,1]
rating     = 按当日分布分位（top 10% → 5★）或固定区间
```

分位映射的好处：论文少的淡日不会全是 1 星，爆发日不会全是 5 星，星级重新变成"今天值不值得看"的信号。同时把 `relevance` / `affinity` 两个子分都显示在邮件里，可解释性不降反升。

---

## 5. 扩展路线：从"脚本"到"可插拔的每日情报流水线"

### 5.1 目标架构

现在 `pipeline.py` 是硬编码顺序。要扩技能，先把它变成**阶段可注册、失败可隔离**的流水线：

```text
arxiv_digest/
  domain/        models.py（Paper 增加 doi / citations / tldr / relevance 等）
                 config.py（拆成 ArxivConfig / LlmConfig / ZoteroConfig / SmtpConfig / SkillsConfig）
  sources/       arxiv.py  openalex.py  crossref.py  rss.py  biorxiv.py
                 ↑ 统一 Source Protocol: fetch(ctx) -> list[Paper]
  rankers/       keyword.py  bm25.py  embedding.py  hybrid.py
                 ↑ Reranker Protocol: rerank(papers, profile) -> list[Paper]
  enrichers/     summary_zh.py  translate_title.py  citations.py  code_link.py
                 trend.py  glossary.py  author_watch.py
                 ↑ Enricher Protocol: enrich(papers) -> list[Paper]
  renderers/     email.py  markdown.py  static_site.py  telegram.py
  sinks/         smtp.py  webhook.py  file.py  zotero_save.py
  state/         seen.json（跨天去重）  cache/（LLM 结果、Zotero 版本）
  pipeline.py    按 ENABLED_SKILLS 组装阶段，逐阶段 try/except，失败降级并记入报告
```

三条关键约束，决定扩展会不会失控：

1. **每个技能必须可单独失败。** 一个 enricher 抛异常只能让它自己跳过，不能让整封邮件消失。这是现有降级哲学的自然延伸。
2. **技能按名字开关**：`ENABLED_SKILLS=summary_zh,trend,author_watch`，默认值保证老配置完全兼容（不要破坏现有 env 变量名）。
3. **阶段之间只通过 `Paper` 传递**，不要互相 import。否则半年后又是一个 `pipeline.py` 里 800 行 if/else。

### 5.2 技能清单（按性价比排序）

| 技能 | 价值 | 实现要点 | 成本 |
| --- | --- | --- | --- |
| **结构化中文摘要**（问题 / 方法 / 数据 / 结论 / 意义） | ★★★★★ | 改 prompt + `Paper` 加字段 + 模板分块显示；分批调用 | 1 天 |
| **相关度解释**（"为什么推给你"） | ★★★★★ | 让 LLM 基于命中的关键词与 Zotero 条目写一句理由，比现在 `keyword: x` 有用得多 | 0.5 天 |
| **中文标题翻译** | ★★★★☆ | 与摘要同一次调用返回，边际成本近零 | 0.2 天 |
| **跨天去重 + 状态** | ★★★★☆ | `state/seen.json` + `actions/cache`；同时解决重跑重复发信 | 0.5 天 |
| **主题聚类 / 今日趋势** | ★★★★☆ | TF-IDF + k-means（`scikit-learn`）或 LLM 一次归纳；输出"今天 arXiv 的 3 条主线" | 1 天 |
| **关注作者追踪** | ★★★★☆ | `WATCH_AUTHORS=...`，命中则加权并在邮件顶部单列"你关注的作者有新论文" | 0.5 天 |
| **引用 / 影响力**（OpenAlex 免费，无需 key） | ★★★☆☆ | 按 DOI/arXiv ID 查 `cited_by_count`；注意新论文引用为 0，只做参考不做排序权重 | 1 天 |
| **代码 / 数据链接** | ★★★☆☆ | 正则从摘要捞 GitHub/Zenodo 链接，或查 Papers with Code | 0.5 天 |
| **反馈闭环**（👍/👎） | ★★★★★ | 邮件里放带 `paper_id` 的链接 → 一个轻量 endpoint（Cloudflare Worker / GitHub Issue）→ 写入权重文件 → 下次排序生效。**这是让推荐真正变好的唯一途径** | 2-3 天 |
| **多通道**：Telegram / 飞书 / Slack / RSS | ★★★☆☆ | 抽 `Renderer` + `Sink`，复用同一份 `Paper` 列表；Telegram 用 MarkdownV2 | 1 天 |
| **静态归档站**（GitHub Pages） | ★★★☆☆ | 渲染 Markdown 到 `docs/`，workflow 里 commit；顺带有历史可搜索 | 1 天 |
| **Zotero 自动入库** | ★★★☆☆ | 高分论文直接 `POST /items` 进 Zotero 指定 collection，形成"读—收—评"闭环 | 1 天 |
| **多 profile / 多用户** | ★★☆☆☆ | `profiles/*.yaml` 各自分类、关键词、收件人；workflow matrix 展开。复杂度陡增，非必要不做 | 3 天 |
| **PDF 全文 / 图表** | ★★☆☆☆ | 下载 PDF + `pypdf` 抽文本；成本与限流风险高，先不做 | 3 天 |
| **播客 / TTS** | ★☆☆☆☆ | 好玩，但对科研价值低 | 1 天 |

### 5.3 建议的推进顺序

**第一批（1-2 天）：止血 + 建安全网。** 不写新功能，先让现状可信。

1. 加 `pytest`：用真实 arXiv Atom XML 快照做 fixture，覆盖解析、日期窗口、429 退避（mock `urlopen`）、评分、模板渲染。
2. 修 cron 与 README 不一致；统一用 `DIGEST_TIMEZONE` 生成标题日期。
3. 修 `category_bonus`、Zotero 归一化、关键词词干化（第 4 节公式）。
4. LLM 改分批 + JSON 容错（去围栏、失败重试一次、单批降级）。
5. 修 `finally: raise` 覆盖原始异常；`load_config()` 进 try；告警邮件仓库 URL 走配置。
6. workflow：`permissions: contents: read`、`concurrency`、pip 缓存、`timeout` 与退避预算对齐。

**第二批（3-5 天）：可维护性。** `pyproject` 单一事实源、`ruff` + `mypy` 进 CI、配置校验、`--dry-run/--out`、跨天状态、运行报告 artifact。

**第三批（1 周）：技能框架 + 前三个技能。** `Source/Ranker/Enricher/Renderer/Sink` Protocol + 注册表；落地结构化摘要、相关度解释、中文标题、主题趋势。

**第四批（按需）：反馈闭环与多通道。** 这是把"每天读一堆摘要"变成"越用越准"的分水岭。

---

## 6. 一句话总结

这是一个**工程质量明显高于同类玩具项目、但缺少测试与架构弹性**的个人工具：降级设计和限流处理是真正的亮点，而致命短板是"评分公式失真 + LLM 单点全量失败 + 零测试"。在扩技能之前，先花两天补上测试与评分修正，否则每加一个技能，都是在没有安全网的情况下加一条新的失败路径。

---

## 7. 定位与开源化路线

### 7.1 结论：个人优先，但把"可逆性"当作约束

目标顺序是"自己先用好 → 效果好再开源"。这两件事不冲突，但会争夺时间，正确的做法不是二选一，而是：

> **按单用户实现，只对"以后改起来贵"的地方提前让步。**

理由是：开源项目真正的门槛不是功能数量，而是**别人 5 分钟内能不能跑起来**，以及**作者自己是不是真的每天都在用**。后者决定项目能不能活过第三个月。所以第一阶段绝不能为"假想的用户"做多租户、插件 SDK、i18n——那是典型的过度设计。

### 7.2 现在必须定、以后改动代价高的决定

| 决定 | 纯个人可接受 | 开源后的代价 | 现在的低成本做法 |
| --- | --- | --- | --- |
| 状态文件格式 | 随手写 JSON | fork 用户已有旧格式，得写迁移 | `{"version": 1, "seen": {"2401.00001": "2026-10-23"}}`，读取时容忍未知版本，按 ID 去掉 `vN` 去重 |
| 环境变量命名 | 随手加 | 改名即破坏所有已有部署 | **把现有 env 名冻结为公共契约**；新增只追加，默认值必须能开箱跑 |
| `Paper` 模型 | 缺字段就加 | 字段扩散到模板、测试、所有 enricher | 一次性定好 3 个强类型字段（`tldr`、`relevance_reason`、`title_zh`）+ `extra: dict[str, Any]` 逃生舱 |
| 输出层 | 只有 emailer | 加 Telegram 得重写渲染 | 拆 `Renderer`（产 HTML/Markdown）与 `Sink`（发邮件/发 webhook/写文件），+0.5 天 |
| 技能命名 | 内部函数名 | 名字写进 README 就是 API | 现在就定 `summary_zh`、`trend`、`author_watch`、`citations`，用 `ENABLED_SKILLS` 开关 |
| 许可证 | 没有 | 别人**法律上不能用** | 一个 MIT 文件，5 分钟 |
| 失败语义 | 抛异常就崩 | 用户分不清是 bug 还是上游抖动 | 定死契约：**技能级失败不致命**，写入运行报告并降级 |

七项里只有第一、四项需要真的写代码，其余是"定规矩 + 写文档"的成本。

### 7.3 明确推迟的事（不要现在做）

多 profile / 多用户、i18n、插件 SDK 与第三方注册机制、embedding 排序、PDF 全文、Web UI、TTS。它们都不是可逆性陷阱——以后加是纯增量，现在做是纯浪费。

### 7.4 开源门面清单（低成本、高回报）

| 项 | 成本 | 为什么重要 |
| --- | --- | --- |
| `LICENSE`（MIT） | 5 分钟 | 没有许可证 = 别人不能合法复用，也是"不专业"的第一印象 |
| README 重写 + **一张真实邮件截图** | 2 小时 | 90% 的 stars 在 README 前 30 秒决定，截图比任何文字都有说服力 |
| `--dry-run --out preview.html` | 2 小时 | 让陌生人**不配 SMTP 也能看到效果**，转化率最高的一步 |
| 英文 README（或中英双语） | 2 小时 | 受众主要在英文社区，中文 README 会挡住大半潜在用户 |
| 3 步 quickstart + template repository | 2 小时 | 从 clone 到收到第一封邮件必须 ≤5 分钟 |
| CI 徽章（test / ruff / mypy） | 1 小时 | 绿色徽章是可信度的廉价信号 |
| `CHANGELOG.md` 从 README 拆出 | 1 小时 | 现在 README 前半部分是 changelog，挡住真正的使用说明 |
| `docs/ARCHITECTURE.md` | 2 小时 | 让"想改但怕改坏"的人敢提 PR |
| 通用化默认值说明 | 1 小时 | 默认 `astro-ph.GA` 没问题，但要写明"改成 `cs.LG` 即可用于任意领域" |

### 7.5 "效果很好"要有可验证的标准

不要用"感觉不错"决定是否开源，建议跑满两周再判断：

1. 连续 14 天每天真的打开邮件。
2. 每天至少点开 3 篇 PDF / abs 页。
3. 至少出现一次"这篇我本来会错过"的正面案例。
4. 只留 Zotero 偏好、关掉关键词时，推荐质量明显下降（说明个性化信号真在起作用）。

四条满足三条，产品成立，可以开始写英文 README；否则先解决问题。

### 7.6 修订后的路线（与 5.3 节合并）

| 阶段 | 内容 | 周期 | 状态 |
| --- | --- | --- | --- |
| **A. 止血 + 门面** | pytest（arXiv XML fixture）、修 cron / 评分 / LLM 分批 / 异常覆盖、`--dry-run --out`、LICENSE、README + 截图、CI 徽章 | 2-3 天 | 可给别人试用 |
| **B. 结构弹性** | `Profile` 抽象、`Renderer`/`Sink` 拆分、`ENABLED_SKILLS` 注册表、跨天状态、运行报告 artifact | 3-5 天 | 可标 `v0.2` |
| **C. 差异化技能** | 结构化中文摘要、相关度解释、中文标题、主题趋势、关注作者 | 1 周 | 可发 `v1.0` 并宣传 |
| **D. 反馈闭环** | 👍/👎 → 权重回写 → 排序生效（个人用也立刻受益） | 2-3 天 | 形成护城河 |

唯一调整：把 7.4 的门面项并进阶段 A。理由不是"为了开源"，而是这些东西（截图、dry-run、README、CI）恰好也是你个人调试和长期维护最需要的工具，零浪费投入。

### 7.7 发布渠道（参考）

技术上准备好之后，决定 star 数的是分发：`awesome-arxiv` 类清单、r/MachineLearning、r/astronomy、Hacker News Show HN、arXiv 社区 Slack/Discord、知乎/小红书的"科研自动化"话题。标题不要写"我做了一个 arXiv 摘要工具"，而是**"我用 3 个月把 arXiv 阅读时间从 2 小时压到 15 分钟，这是全部配置"**——具体收益 + 可复制，比功能罗列有效得多。
