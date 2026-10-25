# Daily arXiv Digest

每天从 arXiv 抓取你关注领域的新论文，结合关键词和 Zotero 兴趣偏好做个性化推荐，用 LLM 生成中文总览和单篇摘要，并通过 GitHub Actions 自动发送邮件。

> **v0.7 初稿（2026-10-25）**：邮件不再是"摘要列表"，而是一份**读过原文的简报**。
> 精读那几篇会下 PDF 全文，按你的读者画像分 10 个分区：一句话结论、方法链、关键数字、图表线索、
> 七问七答、今天能做什么、领域红旗反查、术语小抄、中文摘要、英文原题摘要（折叠）。
> 另有"本周脉搏"（本周大家在看什么、哪些有争论，每条判断都挂证据）、
> "邻域雷达"（别的方向这周在动什么，每方向 3 条只展 1 条、其余折叠）、
> "世界在动"（二手解读 + 会议报告 + NASA/ESA/DESI 等机构源，每条标注来源）、"近期日程"（数据发布倒计时）。
> 正文用宋体 + Times New Roman，`$H_0$` 这类 LaTeX 会转成 `H₀` 直接可读。
> **v0.7 版式重做**：改成本期简报的排版——报头 + 分区眉题 + 卡片，暖纸底色 `#f3f0e9` 配墨色正文与琥珀色强调；
> 标题层级拉开（10.5px 眉题 / 16px 分区 / 17.5px 论文题），正文行高 1.8；速览折叠加回**完整**中英文摘要（原来只有截断片段）；
> 「世界在动」每条中文解读扩到 60–100 字，英文原标题保留作核对锚点。
> 设计文档见 [`docs/PRODUCT_PLAN.md`](docs/PRODUCT_PLAN.md)，问题清单见 [`docs/PROJECT_REVIEW.md`](docs/PROJECT_REVIEW.md)。

---

## 5 分钟跑通

```bash
git clone <this-repo> && cd arxiv-astro-digest
python -m venv .venv && . .venv/Scripts/activate   # Windows;  Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                                # 填 OPENAI_API_KEY 与 SMTP_*
python -m arxiv_digest.selfcheck                    # 10 秒，19 项离线自检，不联网不发信
python -m arxiv_digest.main                         # 真发一封
```

只想先看在浏览器里的样子（不联网、不发信）：

```bash
python scripts/build_preview.py                     # 写出 preview.html
```

把它部署到 Actions：**Settings → Secrets and variables → Actions** 填 `OPENAI_API_KEY`、`SMTP_*`、`EMAIL_*`，然后启用 `Daily arXiv Digest` workflow。默认 `30 5 * * *`（UTC）= 13:30 北京 = 01:30 ET，抓的是 arXiv 前一夜的公告。

**想换成你的领域**：改两处就够——`config/profile.yaml`（你是谁、关心什么）和 `config/packs/*.yaml`（本领域的评价标准与红旗）。分类、二手源、审查角度都从这两处来，代码不用动。

---

## 已完成的主要改进

- **arXiv 抓取**：支持按多个 arXiv category 抓取论文，例如 `astro-ph.GA,astro-ph.CO`。
- **分页抓取**：用较小的 `ARXIV_PAGE_SIZE` 分多次请求 arXiv，同时用 `ARXIV_FETCH_MAX_RESULTS` 控制总候选池大小。
- **北京时间自然日**：用 `DIGEST_TIMEZONE=Asia/Shanghai` 定义“昨天”，不再只是最近 24 小时。
- **推荐排序**：结合关键词、arXiv 分类和 Zotero 标签偏好计算推荐分数。
- **星级评价**：把推荐分数映射为 1-5 星，并在邮件中展示。
- **中文摘要**：LLM 一次生成每日总览和每篇论文的中文单篇摘要。
- **LLM 输入保护**：用 `MAX_LLM_PAPERS` 和 `MAX_ABSTRACT_CHARS` 限制模型输入，降低成本和出错概率。
- **最低星级过滤**：用 `MIN_RATING` 过滤低匹配论文。
- **Zotero 降级**：Zotero 不可用时，系统仍会用关键词和分类排序。
- **LLM 降级**：OpenAI/兼容接口不可用时，邮件仍可发送，摘要退回到基础文本。
- **失败告警**：主流程失败且 SMTP 可用时，会发送失败告警邮件。
- **arXiv 429 处理**：当 arXiv 返回 `HTTP 429 Too Many Requests` 时，使用指数退避重试。
- **429 graceful 模式**：若 arXiv 持续限流，默认发送告警邮件并让 GitHub Actions 正常结束，避免每天红灯。
- **兼容 OpenAI-like API**：支持 OpenAI、DeepSeek、通义千问、Moonshot、Ollama 等兼容 OpenAI API 的模型。
- **HTML 邮件模板**：使用 Jinja2 模板渲染更清晰的邮件内容。

### v0.7 新增

- **版式重做**：报头（刊名 / 日期 / 今日量级）+ 分区眉题（READING / SKIM / THE FIELD），去掉粗黑下划线与重边框，改 1px 暖灰细线与 10px 圆角卡片。
- **配色系统**：暖纸底 `#f3f0e9`、墨色 `#1b2431`、琥珀强调 `#b45309`、藏青 `#1f4e6b`、紫 `#6b4c9a`，全篇 55 个色值收敛为一套语义色。
- **摘要完整性**：速览折叠块内的中英文摘要是全文，不再截断到 110/170 字。
- **二手解读加长**：`text_zh` 由一句扩到 50–100 字（写清发现 / 方法 / 为什么值得知道），翻译提示词同步收紧。

### v0.6 新增

- **机构源分频抓取**：NASA 每日，ESA / ESO / DESI / Keck 每周（`cadence: daily|weekly`），上次抓取日记在 `state.json`。
- **公式可读**：`$H_0 = 67.4 \pm 0.5$` → `H₀ = 67.4 ± 0.5`，`$\Lambda$CDM` → `ΛCDM`，不依赖客户端脚本。
- **字体**：正文英文 Times New Roman、中文宋体（`Songti SC` / `SimSun` 回退链）。

### v0.5 新增

- **会折叠的雷达**：每个方向取 3 条、默认只展 1 条，其余点开再看；每条 80–110 字解读，讲清"这是什么、为什么值得看"。
- **速览摘要折叠**：10 条速览也有中英摘要，收在 `展开中英文摘要` 里，不占默认版面。
- **世界在动扩容**：二手解读与会议报告合并，每条前置来源色块（AAS Nova / Astrobites / ESO / Phys.org / Universe Today），标题中英并列。
- **排版**：正文行高 1.72、分区标题统一、卡片圆角与阴影收敛——信息密度不变，读起来不压人。

### v0.4 新增

- **邻域雷达**：跨领域"只挑不读"，信号词加权，把别的方向最值得一瞥的那条挑出来。
- **二手翻译**：RSS 与会议报告自动翻成中文，失败时保留英文原文、不阻塞发信。
- **反馈闭环**：👍/😐/👎 三种投票，按分类与作者学习权重；故意不做关键词自动改写。
- **评测集**：`scripts/eval_analysis.py` 生成 20 篇骨架，检查覆盖、引文保真、字段填充、数字召回。

### v0.3 新增

- **本周脉搏**：这周到底在动什么。先本地数主题（领域包里的 aliases，标题权重 2、正文权重 1）、找跨源共现、识别材料里的争论措辞，再让模型在**每条判断必须挂证据 id** 的约束下写 3-4 个问题；没挂证据的判断直接丢弃，无 key 时只报主题统计、不编故事。
- **主题分布条形图**：邮件里给出本周材料的主题分布，红色条表示该主题的材料里出现过 tension / inconsistent / puzzle 这类措辞。
- **意见与统计分离**：计数、张力识别、图注抽取全是本地正则，零成本；只有最后写判断那一步调一次模型（约 $0.01/天）。

### v0.2 新增
- **分批 LLM 调用**：14 篇拆成 5 篇一批，单批失败只降级那一批，不再"一次截断全篇变英文"。
- **两阶段分析**：先抽事实（带原文引文），再按读者画像 + 领域包写评论；真读不到就写"摘要未提"，不许猜。
- **全文精读**：精读篇目下 PDF → 去参考文献 → 按关键词句裁剪 → 40k 字符预算，徽标标明「全文」/「仅摘要」。
- **领域包**：`config/packs/*.yaml` 纯数据扩展，换领域不改代码。
- **多源**：RSS（Astrobites / AAS Nova / ESO）与 Indico 会议报告，任一源挂掉不影响其余部分与发信。
- **近期日程**：数据发布、会议、提案截止的倒计时，日期不确定时显示"预计 2026 年 3 月"而不是假精确。
- **跨天状态**：`state.json` 去重 + 周环比，Actions 每天自动 commit 回去（顺带避免 60 天无活动被停用）。
- **离线自检**：`python -m arxiv_digest.selfcheck` 14 项，CI 每次先跑它，坏了当场红。


## 本地运行

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env` 后，先跑离线自检（不联网、不需要 API key、不发邮件）：

```powershell
python -m arxiv_digest.selfcheck
```

它会依次验证：导入链、默认配置、LLM 返回的 JSON 容错解析、分批切分、无 key 降级、日历文件读取、邮件模板渲染。改完代码先跑它，比直接发邮件快得多。

想看渲染效果可以顺手写一份 HTML：

```powershell
python -m arxiv_digest.selfcheck --write-preview preview.html
```

再预览真实结果：

```powershell
python -m scripts.preview_digest
```

确认配置无误后发送邮件：

```powershell
python -m arxiv_digest.main
```

## GitHub Actions 配置

把项目推送到 GitHub 后，进入仓库：

```text
Settings -> Secrets and variables -> Actions
```

你需要分别配置 `Secrets` 和 `Variables`。

### Secrets

这些是敏感信息，必须放在 `Secrets` 中：

| Name | 示例/说明 |
| --- | --- |
| `OPENAI_API_KEY` | OpenAI 或兼容接口的 API key |
| `SMTP_HOST` | 例如 `smtp.gmail.com` |
| `SMTP_PORT` | 例如 `587`，SSL 可用 `465` |
| `SMTP_USERNAME` | 发件邮箱账号 |
| `SMTP_PASSWORD` | 邮箱授权码或 SMTP 密码 |
| `EMAIL_FROM` | 发件地址 |
| `EMAIL_TO` | 收件地址 |
| `EMAIL_ATTACH_HTML` | true | 邮件里附一份 HTML（可下载转发到群里） |
| `EMAIL_THEME` | dark | 邮件主题：`dark` 深空冷色 / `light` 暖纸浅色 |
| `ZOTERO_API_KEY` | 可选，Zotero API key |
| `ZOTERO_USER_ID` | 可选，Zotero user ID |

### Variables

这些是普通配置，放在 `Variables` 中。推荐先使用下表的值跑通项目，再逐步调整。

| Name | 推荐值 | 说明 |
| --- | --- | --- |
| `ARXIV_CATEGORIES` | `astro-ph.GA,astro-ph.CO` | 关注的 arXiv 分类，逗号分隔 |
| `ARXIV_KEYWORDS` | `galaxy,cosmology,black hole,star formation` | 兴趣关键词，逗号分隔 |
| `DIGEST_TIMEZONE` | `Asia/Shanghai` | 用于定义“昨天”的时区 |
| `ARXIV_FETCH_MAX_RESULTS` | `100` | arXiv 总候选池大小 |
| `ARXIV_PAGE_SIZE` | `25` | 单次 arXiv API 请求最多获取多少篇 |
| `ARXIV_MAX_PAGES` | `4` | 最多请求多少页 |
| `ARXIV_PAGE_DELAY_SECONDS` | `3` | 页与页之间等待多少秒 |
| `ARXIV_REQUEST_TIMEOUT` | `60` | arXiv 单次请求超时秒数 |
| `ARXIV_REQUEST_RETRIES` | `5` | arXiv 请求失败后的最大尝试次数 |
| `ARXIV_RATE_LIMIT_BACKOFF_BASE` | `60` | 429 指数退避基础等待秒数 |
| `ARXIV_RATE_LIMIT_BACKOFF_MAX` | `600` | 429 指数退避最大等待秒数 |
| `ARXIV_RATE_LIMIT_BACKOFF_JITTER` | `30` | 每次等待额外加入的随机秒数上限 |
| `ARXIV_RATE_LIMIT_GRACEFUL` | `true` | 持续 429 时发送告警并让 workflow 正常结束 |
| `MAX_PAPERS` | `14` | 邮件最多展示论文数 |
| `MAX_LLM_PAPERS` | `14` | 最多让 LLM 生成摘要的论文数 |
| `MAX_ABSTRACT_CHARS` | `1800` | 每篇英文摘要送入 LLM 前的最大字符数 |
| `LLM_BATCH_SIZE` | `5` | 每次请求送几篇给 LLM；调小更抗截断，调大更省钱 |
| `DEEP_PAPERS` | `4` | 精读区篇数（完整卡片） |
| `BRIEF_PAPERS` | `10` | 速览区篇数（紧凑行） |
| `MIN_RATING` | `1` | 最低推送星级，建议初期保留 `1` |
| `LOOKBACK_DAYS` | `1` | 回顾天数，通常保持 `1` |
| `CALENDAR_ENABLED` | `true` | 是否在邮件里附「近期日程」 |
| `CALENDAR_EVENTS_PATH` | `config/events.yaml` | 数据发布等确定性事件 |
| `CALENDAR_CONFERENCES_PATH` | `config/conferences.yaml` | 会议届次与系列 |
| `CALENDAR_HORIZON_DAYS` | `120` | 只展示未来多少天内的事件 |
| `CALENDAR_RECENT_DAYS` | `14` | 刚过去的会议保留几天 |
| `OPENAI_MODEL` | `gpt-4o-mini` | 使用的模型名称；配了 DeepSeek 的 base URL 却留空时自动用 `deepseek-flash` |
| `OPENAI_BASE_URL` | 留空 | 使用 OpenAI 官方时留空；兼容接口时填写 base URL |
| `ANALYSIS_ENABLED` | `false` | 开启两阶段分析：精读那几篇先抽事实、再按读者画像写七问七答 |
| `ANALYSIS_EXTRACT_MODEL` | 同 `OPENAI_MODEL` | 阶段 A（抽事实）用的模型，可以选便宜的 |
| `ANALYSIS_ASSESS_MODEL` | 同 `OPENAI_MODEL` | 阶段 B（写评论）用的模型，建议用强的 |
| `ANALYSIS_EXTRACT_THINKING` | `false` | 抽取阶段的思考模式；关掉更快更省，结果也更稳定 |
| `ANALYSIS_ASSESS_THINKING` | 留空 | 评论阶段的思考模式；留空 = 跟随模型默认（DeepSeek 关、其他端点不传） |
| `LLM_THINKING` | 留空 | 总览与单篇中文摘要的思考模式 |
| `PULSE_THINKING` | 留空 | 本周脉搏的思考模式 |
| `RADAR_THINKING` | 留空 | 邻域雷达解读的思考模式 |
| `PROFILE_PATH` | `config/profile.yaml` | 读者画像：决定评论回答哪几个问题 |
| `PACKS_DIR` | `config/packs` | 领域包目录：每个 YAML 是一个学科的评价标准 |
| `FIELDS` | 留空 | 勾选领域包（逗号分隔）；留空 = 全部启用 |
| `DIGEST_JSON_PATH` | 留空 | 填 `digest.json` 可把每期结构落盘，便于回放与调参 |
| `FULLTEXT_ENABLED` | `true` | 精读篇目是否下 PDF 抽正文 |
| `FULLTEXT_MAX_CHARS` | `40000` | 送进模型的正文上限（字符），约 1 万 token |
| `FULLTEXT_MAX_PAGES` | `40` | 最多解析多少页 PDF |
| `FULLTEXT_TIMEOUT` | `120` | 单篇 PDF 下载超时（秒） |
| `SOURCES_ENABLED` | `true` | 是否抓二手解读（RSS）与会议报告（Indico） |
| `SOURCES_PATH` | `config/sources.yaml` | 信息源清单；每个源可单独 `enabled: false` |
| `SOURCES_MAX_ITEMS` | `6` | 每个源最多取几条 |
| `SOURCES_TIMEOUT` | `30` | 单个源抓取超时（秒） |
| `STATE_PATH` | `state.json` | 跨天状态：已读去重 + 周环比（Actions 每天 commit 回去） |
| `TREND_ENABLED` | `true` | 是否计算"本周投稿量变化" |
| `TREND_ROWS` | `4` | 邮件里最多显示几行趋势 |
| `RADAR_ENABLED` | true | 邻域雷达开关 |
| `RADAR_PATH` | config/radar.yaml | 雷达方向配置 |
| `RADAR_PER_FIELD` | 3 | 每个方向取几条（默认只展 1 条） |
| `RADAR_MAX_FIELDS` | 5 | 最多几个方向 |
| `RADAR_LOOKBACK_DAYS` | 7 | 雷达回看天数 |
| `TRANSLATE_ENABLED` | true | 二手解读中文翻译 |
| `TRANSLATE_LIMIT` | 40 | 最多翻多少条（二手源总数也就 20-40 条） |
| `TRANSLATE_THINKING` | 留空 | 翻译的思考模式；开思考容易把 `max_tokens` 吃光 |
| `FEEDBACK_URL` | 空 | 反馈链接后端；留空则隐藏按钮 |
| `FEEDBACK_PATH` | config/feedback.json | 投票落盘位置 |
| `PULSE_ENABLED` | `true` | 是否生成"本周脉搏" |
| `PULSE_MODEL` | 留空 | 写判断用的模型；留空则跟随 `OPENAI_MODEL` |
| `PULSE_THREADS` | `4` | 最多写几条判断 |
| `PULSE_MAX_MATERIALS` | `30` | 喂给模型的材料条数上限（越多越贵，30 条约 6k token） |
| `ZOTERO_LIBRARY_TYPE` | `user` | Zotero 类型，可为 `user` 或 `group` |
| `ZOTERO_COLLECTION_ID` | 留空 | 可选，仅参考某个 Zotero collection |

## arXiv 429 指数退避

现在抓取策略分成两层：

```text
ARXIV_FETCH_MAX_RESULTS = 总共最多收集多少篇候选论文
ARXIV_PAGE_SIZE = 单次请求最多取多少篇
ARXIV_MAX_PAGES = 最多翻几页
ARXIV_PAGE_DELAY_SECONDS = 每页之间等待多少秒
```

例如：

```text
ARXIV_FETCH_MAX_RESULTS=100
ARXIV_PAGE_SIZE=25
ARXIV_MAX_PAGES=4
ARXIV_PAGE_DELAY_SECONDS=3
```

表示最多分 4 次请求，每次最多 25 篇，理论上最多收集 100 篇候选论文。这样比单次请求 100 篇更温和，也更适合扩大候选池。

当 arXiv 返回 `HTTP 429 Too Many Requests` 且没有提供 `Retry-After` 头时，程序按下面的公式等待：

```text
delay = min(base * 2^attempt + random(0, jitter), max)
```

默认配置下，前几次等待大约是：

```text
60-90s, 120-150s, 240-270s, 480-510s, 最高 600s
```

如果 arXiv 返回了 `Retry-After`，程序会优先尊重 arXiv 指定的等待时间。

## LLM 模型配置示例

OpenAI：

```ini
OPENAI_API_KEY=sk-xxxxxxxx
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=
```

DeepSeek（2026-10 官方在售只有两个模型：`deepseek-flash` / `deepseek-v4-pro`）：

```ini
OPENAI_API_KEY=你的 DeepSeek key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-flash

# 想省钱又想要质量，就拆开用：
# ANALYSIS_EXTRACT_MODEL=deepseek-flash     # 抽事实：便宜、量大
# ANALYSIS_ASSESS_MODEL=deepseek-v4-pro      # 写评论：一篇只调一次
# PULSE_MODEL=deepseek-v4-pro

# 思考模式：留空 = 跟随模型默认（DeepSeek 端点关、其他端点不传）
# 六处调用都能单独开关；想全开就都写 true（v4-pro 上更划算）
ANALYSIS_EXTRACT_THINKING=true               # 抽事实
ANALYSIS_ASSESS_THINKING=true                # 写评论（最值得开）
PULSE_THINKING=true                          # 本周脉搏
RADAR_THINKING=true                          # 邻域雷达解读
TRANSLATE_THINKING=false                     # 翻译：开思考容易把预算吃光，建议关
LLM_THINKING=true                            # 总览与单篇摘要
```

参数上有三条官方硬约束，代码里都处理了：

- `max_tokens` 必须显式给，否则 JSON 可能被中途截断（本项目每处调用都传了）。
- 思考模式下 `temperature` 无效，代码在 `thinking=true` 时干脆不传。
- `thinking` 是 DeepSeek 专有参数，发给 OpenAI 会 400；三态开关留空即不发。

通义千问：

```ini
OPENAI_API_KEY=你的阿里云 key
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
OPENAI_MODEL=qwen-plus
```

Ollama：

```ini
OPENAI_API_KEY=ollama
OPENAI_BASE_URL=http://localhost:11434/v1
OPENAI_MODEL=qwen2.5:7b
```

## Zotero 标签

正向标签会提高相似论文的推荐分数：

- `favorite`
- `important`
- `interested`
- `high-interest`
- `must-read`
- `relevant`

负向标签会降低相似论文的推荐分数：

- `not-interesting`
- `irrelevant`
- `low-interest`
- `skip`

## 星级评分

内部推荐分数由关键词命中、Zotero 相似度和分类加成组成，然后映射为星级：

| 分数范围 | 星级 | 含义 |
| --- | --- | --- |
| `>= 5.0` | 5 星 | 高度符合兴趣 |
| `>= 3.0` | 4 星 | 明显相关 |
| `>= 1.2` | 3 星 | 中等相关 |
| `>= 0.4` | 2 星 | 领域相关但个性化信号较弱 |
| `< 0.4` | 1 星 | 保底收录 |

## GitHub Actions

工作流文件位于：

```text
.github/workflows/daily-arxiv-digest.yml
```

默认每天 `UTC 05:30` 运行，也就是北京时间 `13:30`、美东 `01:30`。

为什么是这个点：arXiv 的**新提交截止是 14:00 ET，当日公告 20:00 ET**。原来的 `00:37 UTC` 在夏令时是 20:37 ET（公告之后，没问题），到了**冬令时就变成 19:37 ET，比公告早 23 分钟**——那段时间会整天空跑。`05:30 UTC` 永远落在公告之后、下一次截止之前，不再随夏令时漂移。

工作流分两步：先跑 `python -m arxiv_digest.selfcheck`（不联网、10 秒），通过后再发信；最后把 `state.json` commit 回仓库——这既是跨天去重的存储，也顺手避开"公共仓库 60 天无活动自动停用定时任务"。

想换时间就改 `cron`，注意它写的是 **UTC**。

你也可以在 GitHub 页面中手动触发：

```text
Actions -> Daily arXiv Digest -> Run workflow
```

## 降级策略

| 情况 | 行为 |
| --- | --- |
| 未配置 OpenAI key | 中文摘要退回到基础文本 |
| LLM API 调用失败 | 使用 fallback 摘要，不阻塞邮件 |
| Zotero 不可用 | 只使用关键词和分类排序 |
| arXiv 持续返回 429 | 发送限流告警邮件，默认不把 Actions 标红 |
| 主流程异常且 SMTP 正常 | 发送失败告警邮件 |

## 项目结构

```text
arxiv_digest/
  arxiv_client.py   arXiv API 抓取、日期窗口、429 重试、SSL 上下文
  config.py         环境变量配置
  emailer.py        HTML 邮件渲染和 SMTP 发送
  llm.py            分批 LLM 调用、JSON 容错、降级
  analysis.py       两阶段分析：抽事实（A）→ 按画像写判断（B）
  fulltext.py       PDF 下载、抽文、清洗、砍参考文献、裁剪、抓图注
  profile.py        读者画像加载（config/profile.yaml）
  packs.py          领域包加载与分派（config/packs/*.yaml）
  calendar.py       确定性事件与会议倒计时（config/events.yaml）
  state.py          跨天状态：已读去重、每日投稿量、周环比
  sources/          多源接入：base 契约、rss、indico
  main.py           命令入口和失败告警
  models.py         数据模型
  pipeline.py       主数据流程编排
  recommender.py    推荐分数和星级
  zotero_client.py  Zotero 偏好读取
  selfcheck.py      离线自检（14 项，不联网不发信）
  templates/        邮件模板
  prompts/          Stage B 提示词骨架

config/
  profile.yaml      你是谁、关心什么（决定评论问什么、禁说什么）
  packs/            每个 YAML 是一个学科的评价标准与红旗
  events.yaml       数据发布、发射等确定性事件
  conferences.yaml  会议系列与已定届次
  sources.yaml      RSS 与 Indico 会议清单

scripts/
  preview_digest.py 本地预览（不写状态、不发邮件）
  build_preview.py  用录制数据渲染 preview.html
```

## 推送到 GitHub

```powershell
git init
git add .
git commit -m "Initial commit: Daily arXiv Digest"
git remote add origin https://github.com/你的用户名/你的仓库名.git
git branch -M main
git push -u origin main
```

推送后先手动运行一次 workflow，确认 Secrets 和 Variables 都配置正确。
