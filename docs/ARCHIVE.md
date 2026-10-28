# 长期热点档案（Google 表格）

每天把**抓取到的全部论文**（不只是入选的 14 篇）写进一张私有 Google 表格，按周聚合主题数量与占比，用来观察「哪些方向在变多、哪些在变少」。这是影子功能：**只写表，不改邮件内容**。

## 为什么单独做一张表

邮件只能呈现"今天"，无法回答"最近三周星系团质量标定是不是变多了"。表格能：

- 保留原始计数，随时回查某一天到底有什么；
- 用四周移动平均抵消 arXiv 公告节律（周末/节假日公告少）；
- 区分「预印本先行」与「期刊跟进」，看一个方向是刚起还是在收尾。

## 五张表

一次部署会建五张表（若表已存在则复用；表头必须一致，否则会报错而不是悄悄写坏）。前四张是抓取与热点，`feedback_view` 是你自己投票的聚合。

### `papers` — 明细，一行一篇

键：`week_start + id`。

| 列 | 含义 |
| --- | --- |
| week_start / week_label | 该论文所属周（周日为起点）与可读标签 |
| date | 论文发布日（按 `DIGEST_TIMEZONE` 折算） |
| id / doi / venue / source_kind | 稳定 ID、DOI、期刊名、`arxiv` 或 `journal` |
| pack / categories / topics / keywords | 领域包、arXiv 分类、命中的领域主题、命中的关键词 |
| title / title_zh / first_author / n_authors | 标题信息 |
| summary_snippet | 摘要前 N 字（默认 160） |
| score / rating | 总分与星级；`s_*` 是分数构成五项 |
| shown / deep | 是否入选邮件、是否进入精读 |
| relevance / relevance_kind / verdict | 精读判断（未精读为空） |
| concepts / n_numbers / n_red_flags / basis / confidence | 精读抽取的术语、数字个数、红旗个数、依据与置信 |

### `weekly_topics` — 周 × 主题

键：`week_start + topic`。

| 列 | 含义 |
| --- | --- |
| papers / share | 当周命中篇数 / 当周采样篇数 |
| prev_papers / delta_pct | 上一周篇数与变化百分比（上周为 0 时留空） |
| ma4 | 当周与前 3 周的移动平均 |
| arxiv_papers / journal_papers | 预印本与期刊各几篇 |
| mean_score | 命中论文平均分 |
| top_concepts | 精读论文里出现最多的术语（前 3） |
| first_week | 该主题首次出现在样本里的周 |
| finalized | 该周是否已结束（周日之后置 1） |

### `weekly_overview` — 每周一行

`sampled`、`arxiv_papers`、`journal_papers`、`unique_categories`、`deep_reads`、`venues`（如 `ApJ=4;arXiv=8`）、`top5_topics`、`top5_concepts`、`rising` / `falling`（要求本周与上周都至少 2 篇）、`finalized`。

### `dict` — 口径字典

`topic / aliases / pack / source / first_week / last_week / total_papers`。任何"这个主题到底包含什么"的争议都以这张表为准。

### `feedback_view` — 周 × 主题的投票汇总

键：`week_start + topic`。数据来自 `config/feedback.json`（本地）与反馈 Sheet（云端）合并后的票，按论文所属主题分摊。

| 列 | 含义 |
| --- | --- |
| votes | 当周该主题下、你投过票的论文篇数 |
| mean_relevance / mean_usefulness | 只对给出该维的票求均值；没给的那维留空，不按 0 算 |
| top_useful | 「很有用」（有用性=2）的论文原始 id，最多 3 个，`;` 分隔 |
| updated | 写这一行的日期 |

它和 `weekly_topics` 用同一个键，可以直接并排看："这个方向在变多"和"这个方向对我是有用的"不一定是同一件事。

## 口径（看表前先看这里）

- 一周从**周日**开始，与周报窗口一致；论文按 `published` 落在哪一周归档，跨周重抓不会重复。
- `share = 命中篇数 / 当周采样篇数`。一篇论文命中多个主题会各占一行，所以各主题 share 之和**可以超过 100%**，不要当饼图看。
- `delta_pct = (本周 − 上周) / 上周`。上周为 0 时留空，不写 100%。
- `ma4` 是当周与前 3 周的算术平均，用来抵消公告节律；只看一两天的数字容易误判。
- 这是**采样**：抓到什么算什么，周末与节假日公告少、期刊有出版滞后。它不证明"整个领域在变热"，只说明"这几天我们看到的样本里它在变多"。
- 主题与关键词是**规则打标**（领域包 topics + 配置关键词），会漏也会重；模型只对精读的 4 篇补术语，不参与打标。

## 部署（一次性，约 15 分钟）

1. 新建一个 Google 表格，记下地址里的 `SPREADSHEET_ID`（`/d/<这一段>/edit`）。
2. 打开 <https://script.google.com> → 新建项目 → 粘贴 `scripts/google_archive.gs` 全文。
3. 项目设置 → 脚本属性，加两项：
   - `ARCHIVE_SPREADSHEET_ID`：上一步的表格 ID；
   - `ARCHIVE_TOKEN`：随便生成一段长随机串（例如 `openssl rand -hex 32`）。
4. 部署 → 新建部署 → 类型「Web 应用」→ 执行身份「我」→ 访问权限「任何人」→ 复制 `/exec` 地址。
5. 本地 `.env` 或 GitHub 仓库 Settings：
   - Variables：`ARCHIVE_WEBHOOK_URL=<刚才的 /exec 地址>`；
   - Secrets：`ARCHIVE_TOKEN=<第 3 步的令牌>`。
6. 自检端点：

```bash
python -m arxiv_digest.archive --health
# [ OK ] 表格端点可用
```

第一次写表时脚本会按 `SHEETS` 里的规范建表头；之后若手动改过表头，写入会失败并提示**表头不一致**，而不是静默覆盖。

### 和反馈表共用一份表格（可选）

可以只建**一个** Google 表格：`feedback`（投票原始行）放第一张，其余 `papers / weekly_topics / weekly_overview / dict / feedback_view` 由档案脚本自己建。此时：

1. 反馈脚本（`scripts/google_feedback.gs`）的脚本属性 `FEEDBACK_SHEET_NAME` 写 `feedback`——不写时仍取第一张表，保持旧行为；**不要依赖"它一定是第一张"**，同名 tab 才稳。
2. `FEEDBACK_SHEET_CSV_URL` 里的 `gid` 必须指向 `feedback` 这张表（地址栏 `#gid=` 后面的数字），不是 tab 序号。
3. 隐私边界：档案脚本只读写 `SHEETS` 白名单里的表，`feedback` 不在其中，所以 `ARCHIVE_TOKEN` 拿不到投票原文；反过来反馈脚本也不碰档案表。两个 Web App 仍然分开部署。

## 写入时机

- 只在**邮件投递成功之后**（`commit_delivery`）写表；预览、失败、跳过都不会写。
- 每天写当周增量并重算当周聚合；周日那封发出后该周 `finalized=1`。
- 每次还会回填**上一周**（处理迟到论文）；幂等靠 Apps Script 按复合键 upsert，同一篇重跑不会产生重复行。
- 表格端点失败只写进 `run_report.json` 的 `archive` 字段并打 warning，**不影响发信**；下一次运行会补写。

## 影子期

前 2 周只看表、不进邮件。观察点：

1. `weekly_topics` 的 `share` 是否与你的直觉一致；不一致先看 `dict` 的别名口径。
2. `delta_pct` 是否被单日波动放大——若经常大幅跳动，说明该看 `ma4`。
3. `papers` 里是否有明显不该出现的主题（规则太宽）或明显漏掉的主题（规则太窄）。

两周后再决定是否把表格摘要放进周报。

## 已知边界

- 只统计**当天抓到的样本**，不是全领域普查；`share` 是样本内占比。
- arXiv 分类与领域包别名是人工维护的，出现新方向需要往 `config/packs/*.yaml` 的 `topics` 加词。
- `concepts` 只来自精读的 4 篇，不是全量术语频次。
- "变多"不等于"更重要"；引用与影响力要 ADS/OpenAlex，本表不做。
- Apps Script 有单次执行时长与每日配额限制，所以默认每批 400 行；行数异常增大时优先看 `ARCHIVE_MAX_ROWS` 与日志里的 `written`。
