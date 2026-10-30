# 变更记录

## v0.19.1 — 精读六字段不再整片"全文节选未检出"（2026-10-30，工作区）

**现象**：读整篇 PDF 之后，精读卡的"数据/处理/系统误差/方法/结果/作者局限"仍然大面积为空；真实文章跑出来 4 张卡里有 2 张六项全空，另一张只活下来"动机"。模型其实读懂了——把 Stage A 的原始 JSON 打出来，七个字段都有中文概括、引文也都能在全文里找到。

**根因（三道过严的闸门，任一道不过就静默清空）**：

1. `analysis.extract_bundle` 要求 **value 里每个数字都出现在它自己那条 quote 里**。模型常把多个数字概括进一段中文、quote 只截一句，于是 `668.4 / 0.017 / 69` 这类数字对不上短引文，整条事实被清成 `Fact()`。
2. `evidence.quote_in_source` 只做严格子串比对。PDF 抽取会带断词连字符、`ﬁ` 合字、页码插行与全半角差异，模型抄写的"逐字引文"于是被判成编造。
3. 模型偶尔不按 schema 返回 `{value, quote}` 对象，只给一段字符串；旧逻辑没有 quote 就直接丢弃。

**修复**：

- 数字回到全文校验（`numbers_supported(value, source)`），仍对不上的按分句抢救（`_keep_fact` + `_salvage_text`），只丢有问题的那半句。
- 引文比对增加"压缩匹配"：NFKC 归一 + 去软连字符 + 只保留字母数字汉字，吸断词、合字、标点与空格差异；压缩后短于 12 字符仍要求逐字命中，避免"the"这种到处都是的串混过。
- 无 quote 的纯字符串事实，只要长度 ≥10 且数字能在全文里找到就保留，并在抽取 prompt 里明确"每个字段必须是 {value, quote} 对象，quote 逐字照抄英文"。
- Stage B 的 `supported_text` 从"只拼 quotes"改为"value + quote"，避免模型复述 Stage A 已核实的数字时被二次误杀。

**验证**：194 项离线测试（新增 11 项 `tests/test_evidence_quotes.py`：断词连字符、`ﬁ` 合字、改写不算引文、短串拒绝、value 数字来自全文其他位置、纯字符串事实保留/编造数字丢弃、分句抢救）、27 项自检、`pip check` 干净。真实模型 + 真实 PDF 复跑整条流水线（4 篇精读）：`2610.39788` 与 MNRAS `stag2061` 均 7/7 字段齐全（后者此前 0/7），`2610.38973` 5/7，"全文节选未检出"从 13 处降到 4 处；剩下的是真的没读到（该篇为期刊论文、没有可用的 arXiv 预印本，basis 退化为摘要）。

## v0.19.0 — 精读直接读整篇 PDF，不再截取（2026-10-30，工作区）

- **整篇全文进模型**：`FULLTEXT_MAX_CHARS=0`、`FULLTEXT_MAX_PAGES=0` 成为默认——下载的 PDF 全文（砍掉参考文献后）原样送进两阶段分析，`fulltext.condense` 与 `analysis._excerpt` 只在显式给出正数预算时才裁剪。"样本怎么选、系统误差怎么校正"不再因为落在前 36k 字符之外而变成"全文节选未检出"。
- **不限页**：`_extract_pdf_text` 在 `max_pages<=0` 时解析所有页（此前 `islice(...,0)` 会一页都不读）；正文仍受 500k 字符硬上限保护，触顶时写 warning。
- **失败降级**：单篇全文分析抛错（最常见是超出模型上下文）时，自动用摘要重跑一次，卡片至少保留摘要级判断，而不是整篇空白。
- **可观测**：每篇日志输出"全文 N 字符，整篇送入抽取"；`selfcheck` 增加"默认整篇返回"断言。
- **文档**：`docs/CONFIG.md` 两个变量默认值改 0（0 = 不限制）；README 中英版配置表同步；徽章 `0.19.0` / 测试 183。
- **验证**：183 项离线测试（新增 7 项 `tests/test_fulltext_unlimited.py`：默认不裁剪、`max_chars=0`、`max_pages=0` 解析全部页、正数预算仍裁剪）、27 项自检、`pip check` 干净；真实 arXiv PDF `2610.35100` 实测抽出 49,750 字符（旧默认 40k 预算下只保留 24,977 字符），`sample`/`systematic` 均在文中。真实模型两阶段与邮件投递尚未在本机复跑。

## v0.18.0 — 中文摘要不再混英文，雷达改成一段话（2026-10-29，工作区）

- **中文摘要不再落英文**：模型批量漏掉条目时，`_fallback_paper_summary` 不再把英文原摘要塞进"中文摘要"位置；缺条目改为**单篇补写**（最多 6 篇），第一次失败再要求"不要出现任何数字"重试一次；日志记录补回篇数。
- **分句级数字校验**：`_salvage_summary` 只丢掉含不可回查数字的分句；删得太多（长文本保留不足 60%）视为半句话，交给重试而不是直接采用。
- **精读卡中文摘要**：`analysis.analyze_paper` 只在摘要含中文且数字可回查时沿用模型摘要，否则用 `verdict + result` 组成中文摘要；渲染层在无摘要时明确写"本次未生成中文摘要，英文原摘要见下方"，不再出现"中文摘要：英文段落"。
- **日期形态不再误判**：`evidence.numbers_supported_ignoring_dates` 识别 `2026 年 7 月` / `July 2026` / `2026-07-01` 等形态，`translate` 用它校验译文——"世界在变"不再因为月份写法被清空而回落英文。
- **邻域雷达改成一段话**：`Radar.overview` 由模型按各方向篇数与头条写 150–260 字中文综述（数字回查），规则兜底同样成段；删掉"扫一眼标题即可"的口吻，条目清单收进 `<details>`，只列标题与篇数。
- **文档**：README 中英版「快速开始」增配置路径流程图（模式一 13 项 → 模式二 Google 侧 6 属性 + 回填 5 项）；`docs/CONFIG.md` 新增「〇、配置路径」逐项表，含随机串生成、`/exec` 与 `/dev` 的区别、改代码要发新版本；徽章版本改 `0.18.0`、测试数改 176。
- **验证**：176 项离线测试（新增 13 项 `tests/test_summary_fallbacks.py`）、27 项自检、`pip check` 干净；真实模型 5 篇样本 5/5 中文完整摘要，雷达总述段渲染正常。真实邮件全量重跑与 SMTP 仍未验证。

## v0.17.1 — 合表自检工具（2026-10-28，工作区）

- 新增 `scripts/check_sheets.py`（`python -m scripts.check_sheets`）：只读检查反馈 CSV 与热点档案端点的接线。
  - 反馈侧：校验表头必须是 `item,relevance,usefulness,t`；如果返回的是 `week_start,week_label,topic,votes`，直接提示"CSV 指向的是热点档案表"（最常见的 gid 指错 tab）；HTML 页面或空内容按权限/凭据错误处理。
  - 档案侧：`archive.ready` 时调端点 `health`，未配置则跳过并提示。
  - 退出码 0/1/2（通过 / 有失败 / 两项都没配置），不写任何行。
- 文档：`docs/ARCHIVE.md` 增加「合表部署清单（推荐）」六步表（一份表格 + 两个 Web App + `FEEDBACK_SHEET_NAME` + gid 检查）；`docs/FEEDBACK.md`、README 命令表补入口。
- 文档：README 中英双语整篇重写——徽章头图、居中主张、组件式能力卡、Mermaid 流程图、折叠式命令与配置、双语切换按钮；`README.en.md` 与中文版逐节对齐。
- **验证**：**163 项离线测试**（新增 10 项 `tests/test_check_sheets.py`）、27 项自检、`pip check` 干净。真实端点仍未验证。

## v0.17.0 — 反馈表与热点档案合表：投票按主题聚合（2026-10-28，工作区）

- **新增 `feedback_view` 表**：`weekly_topics` 的同键表（`week_start + topic`），把你在邮件里投的两维票按论文所属主题分摊聚合：`votes` / `mean_relevance` / `mean_usefulness` / `top_useful` / `updated`。均值只统计给出该维的票，没给的那维留空，不按 0 算。
- **一份表格、两个 Web App**：反馈与档案可以共用同一个 Spreadsheet。反馈脚本不再假设"第一张表就是反馈表"，改为按脚本属性 `FEEDBACK_SHEET_NAME` 取 tab（缺省仍取第一张，向后兼容）；`FEEDBACK_SHEET_CSV_URL` 的 `gid` 必须指向该 tab。
- **隐私边界不变**：档案脚本只读写 `SHEETS` 白名单里的表（新增 `feedback_view`），`feedback` 原文不在白名单内，`ARCHIVE_TOKEN` 拿不到投票原文；反馈脚本也不碰档案表。
- **实现**：`archive.build_feedback_rows`（按 `canonical_id` 对齐 arXiv 版本号与 DOI 大小写）、`record(..., votes=...)`（无票不写表）、`PipelineResult.votes` 由 `_run_pipeline` 从合并后的 `FeedbackStore` 传入。
- **验证**：**153 项离线测试**（新增 5 项反馈聚合用例）、**27 项自检**（"热点档案"增加反馈聚合断言）、`pip check` 干净。
- **未验收**：真实 Apps Script 端点、共用表格时的 gid 与权限实测、云端 Actions。

## v0.16.0 — 长期热点档案：全量论文按周进 Google 表格（2026-10-28，工作区）

- **新功能（影子期，只写表不改邮件）**：新增 `arxiv_digest/archive.py` 与 `scripts/google_archive.gs`。每天把**抓到的全部论文**（不只入选的 14 篇）按周写入私有 Google 表格：
  - `papers`：一行一篇，33 列，含 `shown`/`deep`、分数构成五项、精读判断与置信；
  - `weekly_topics`：周 × 主题，`share` / `prev_papers` / `delta_pct` / `ma4` / 预印本与期刊分列；
  - `weekly_overview`：采样数、渠道、`venues`、`top5_topics`、`rising`/`falling`；
  - `dict`：主题别名与口径字典，出现争议以它为准。
- **口径**：周从周日开始（与周报一致），论文按 `published` 的本地日期归档；`share = 命中篇数 / 当周采样篇数`，一篇命中多主题会各占一行，所以各主题 share 之和可以 >100%；上周为 0 时 `delta_pct` 留空；`ma4` 为当周 + 前 3 周均值；`rising`/`falling` 要求两周各 ≥2 篇。只给可核对计数，不让模型断言"变热"。
- **写入时机与幂等**：只在 `commit_delivery` 投递成功后写；预览、失败、跳过都不写。当天写增量并重算当周聚合，周日那封发出后置 `finalized=1`；同时回填上一周处理迟到论文。幂等靠 Apps Script 按复合键 upsert，重跑不产生重复行。
- **失败隔离**：表格端点异常只记入 `run_report.json` 的 `archive` 字段并打 warning，**不影响发信**；下一次运行补写。`python -m arxiv_digest.archive --health` 可单独自检端点。
- **配置**：`ARCHIVE_ENABLED=true`、`ARCHIVE_WEBHOOK_URL`、`ARCHIVE_TOKEN`（建议放 Secret）、`ARCHIVE_TIMEOUT=20`、`ARCHIVE_MAX_ROWS=400`、`ARCHIVE_SNIPPET_CHARS=160`；URL 或令牌为空时整体跳过。`.env.example` 与 workflow 的 `Send digest` env 已同步。
- **验证**：**148 项离线测试**（新增 19 项 `tests/test_archive.py`）、**27 项自检**（新增"热点档案"）、`pip check` 干净。
- **未验收**：真实 Apps Script 端点（需按 [部署说明](docs/ARCHIVE.md) 建表并部署后跑 `--health`）、云端 Actions、表格长期口径是否与直觉一致（影子期 2 周后再决定是否进周报）。

## v0.15.1 — 本地试跑复查：全文节选不再只剩引言、推荐依据不再二次转义（2026-10-26，工作区）

- **全文节选用"节选"而不是"前 N 字符"**：`analysis._excerpt` 超预算时走 `fulltext.condense`（开头 45% + 含样本/筛选/系统误差/协方差关键词的句子）。此前 `_clip(text, 36_000)` 直接截断，PDF 一长就只剩引言，精读卡满屏"全文节选未检出"（用户 22:16 的预览里 4 篇精读的 data/processing/method 都在截断点之后）。同一份缓存 PDF 复核：旧节选缺 `sampling`/`systematics`/`covariance`，新节选三者全含。
- **不再把内部占位句写进正文**：新增 `_strip_missing_notes`，从 `background`/`method_detail`/`verdict` 里删掉"摘要未提/全文节选未检出"并收拾残留标点；`verdict` 为空时退回 `result` 前 60 字，再不行才写"原文未提供可核对的一句话结论"（此前会把"全文节选未检出"原样当结论显示）。
- **推荐依据分隔符**：模板 `score_reasons|join(" &middot; ")` 被 autoescape 二次转义成 `&amp;middot;`，改为真实字符 `" · "`；自检加断言防回归。
- **关键词去重**：`ARXIV_KEYWORDS` 里重复一项会重复计分、并在推荐依据里重复出现。`load_config()` 现在按顺序忽略大小写去重 `arxiv_keywords` / `arxiv_categories`（用户 `.env` 不必改）。
- **CI 暴露 `PREPRINT_*`**：workflow `Send digest` 的 env 补上四个预印本开关，云端可用仓库变量覆盖。
- **验证**：129 项离线测试（新增 3 项）、26 项自检通过；回放快照重渲染确认 `&amp;middot;` 计数为 0、` · ` 出现 51 次。
- 仍未验收：真实模型输出的抽取质量（`deepseek-flash` 在 36k 字符节选上会把 data/processing/result 抽空，属模型能力边界，非代码缺陷）、SMTP/Zotero/反馈端点/云端 Actions。

## v0.15.0 — 期刊论文也能读全文：DOI 反查 arXiv 预印本（2026-10-26，工作区）

- **期刊全文兜底**：新增 `arxiv_digest/preprints.py`，按标题反查 arXiv（标题相似度 ≥0.90、作者姓氏、年份 ±2 三道校验），命中就用预印本 PDF 做精读；`venue`、`doi`、`id` 仍是期刊，来源徽标不改写成 arXiv。未命中一律回退摘要，不会因为没有预印本而丢论文。
- **摘要补全**：Nature Astronomy 等 Nature 系在 Crossref 常常只给标题。`fetch_journal_papers` 新增 `preprocess`，先补摘要再过主题闸门，减少"只有标题被判为不相关"的误杀（此前 30 篇 NatAs 只留 1 篇）。
- **缓存与礼貌**：命中缓存 180 天、未命中 14 天，请求间隔 ≥3 秒，避免每轮重复打 arXiv；`cache.prune_cache` 一并回收 `preprints/`。
- **数值护栏改为按分句降级**：`background` / `verdict` / 七问里只有个别数字不可回查时，只删含该数字的那半句（`_salvage_text`），不再整段清空；整段没有可核对分句才回退"未提供/未检出"。verdict 回查失败时退回 `result` 生成一句话结论。
- **新增配置**：`PREPRINT_ENABLED`（默认 true）、`PREPRINT_TIMEOUT=20`、`PREPRINT_MAX_RESULTS=5`、`PREPRINT_ABSTRACT_LOOKUPS=4`；`.env.example` 同步说明。
- **验证**：126 项离线测试通过（新增 19 项）、26 项自检通过；真实外网核验 6 篇期刊有 3 篇匹配到 arXiv，两篇精读全部取到全文（118,165 / 80,514 字符），来源仍标 ApJ / A&A；未命中的 3 篇按设计回退摘要。
- 外部投递、Zotero、反馈端点与云端 Actions 仍未验收。

## v0.14.2 — 本地实跑核验与默认快照路径（2026-10-26，工作区）

- `DIGEST_JSON_PATH` 默认改为 `digest.json`：本地预览/实跑也会落快照，反馈 CLI 与服务能读到标题、作者、分类；显式留空仍可关闭（此前默认空，导致"按 README 跑预览→投票"学不到任何元数据）。
- 新增回归 `test_default_digest_path_supports_feedback_metadata`。
- 真实运行核验（两次，含 4 篇精读全链路与快照回放）：抓取 57 篇采样、14 篇入选、期刊 12 篇、二手 30 条；18 次模型请求 / 输入 55,054 / 输出 36,359；反馈两维独立落盘并学到作者。证据与未验收项见 [本地实跑核验](docs/LIVE_RUN_2026-10-26.md)。
- 实跑发现的遗留问题（未修，已记录建议）：精读卡 `background` / `verdict` 数字回查失败时整段丢弃；期刊论文无 arXiv 分类，反馈只学到作者。
- 外部投递、Zotero、反馈端点与云端 Actions 仍未验收；评分修订见审查报告 §11。

## v0.14.1 — 审查修复：预览不再污染快照、CI 持久化兜底与可观测性（2026-10-26，工作区）

- 预览只写 `runs/<run_id>.json`，不再改写 `digest.json`；反馈服务与 CLI 不会再拿到预览版元数据。
- 精读评估失败可见：run_report 新增 `analyzed_papers` / `analysis_failures`，整体失败时打 warning（仍按单阶段摘要投递，不阻断发信）。
- CI 回写 state 不再依赖 best-effort 的 `run_report.json`：`merge_state.py` 在文件缺失/损坏时退回当前日期与时区（新增 `--today`/`--timezone`），workflow 传入 `DIGEST_TIMEZONE`。
- 读坏的 `state.json` / `feedback.json` 先另存为 `.corrupt` 再降级，不再静默覆盖。
- 缓存键补齐调用参数（Stage A 的 temperature/max_tokens/thinking，全文的 `max_chars`），改开关不会误命中旧缓存。
- `canonical_id` 剥掉 `.pdf` 后缀；`atomic_json` 的 NaN `ValueError` 不再击穿 `save_state`；缓存回收对 symlink 环只跳过单文件。
- 删除死代码（`_primary_categories`、`record_day` 导入）；`__version__` 与 pyproject/Crossref UA 对齐；`FEEDBACK_URL` 无签名密钥时告警。
- 详见 [审查修复报告](docs/CHANGE_REPORT_2026-10-26_PATCH.md)；外部投递、模型质量与云端部署仍未验收。

## v0.14.0 — 周日精选、跨领域资讯与复审修复（2026-10-26，工作区）

- 配置时区的周日自动回顾过去七个完整自然日，独立周报去重；不受日报已发送过滤，不把周报论文消费到日报 seen。
- 日报/周报增加兴趣闸门及可解释匹配，模型明确判为 off 的论文排除；不为篇数补入弱相关论文。
- 世界在变加入物理、化学、历史/考古六个 RSS 源；近七日、去重、约 30 条软目标、最多 36 条、天文与其他领域约 1:1，短缺互补。
- 修复雷达单独漏发、可见评论字段数值校验、脉搏坏类型降级、归零趋势、429 graceful、合并清理、并发诊断归属和固定输入评测。
- 每次运行有独立诊断快照及失败阶段；Actions 验证/投递分组、可配置 artifact 路径、官方提交 SHA 固定和业务缓存。
- 反馈端点限制连接数/读取超时；缓存按保留期及容量回收已知可再生文件。外部投递、模型质量和云端部署仍未验收。
- 详见 [本轮改动与验收报告](docs/CHANGE_REPORT_2026-10-26_V014.md)，不以历史复审评分作为修复后新评分。

## v0.13.0 — 状态、证据与反馈闭环修复（2026-10-26，工作区）

- SMTP 显式验证 TLS 证书、主机名并设置超时；预检包含用户名和密码。
- 预览不消费状态，成功发信后才确认投递；稳定 RSS/Indico ID、论文/雷达去重、本地 OS 锁、远端状态合并。
- state v2 按论文日期和 ID 记录采集量；旧不可信计数重建。近七天材料持久化，统计标签说明样本范围。
- digest v3 完整保存事实、来源、关键数字及分析；反馈、评测、回放共用序列化。
- 引文及数值回查、来源标签由程序控制、无证据头条过滤、分式/根式分组保留。
- 表格反馈补齐元数据、逐维时间戳、分数同步星级；个人票值不提交进公开 state。
- 默认离线回放；Stage A/全文缓存；PDF 下载限制与可终止解析；来源独立降级。
- 领域包标题词、期刊词表和 RSS/Indico 装配进入实际流程，保留显式 disabled。
- GET 反馈确认页、POST 一次性令牌、可选签名；新增私有 Sheet Apps Script 后端，尚未云端部署验收。
- PR/push 离线门禁、完整 Actions 配置、运行报告与模型用量；增加回归测试及 AI 交接文档。

详细验收和剩余边界见 docs/CHANGE_REPORT_2026-10-26.md；本条不是发布/真实服务验证声明。


## v0.12.0 — 分数可拆、精读讲背景、反馈两维、期刊入库、Zotero 真参与（2026-10-26）

这一版针对五条使用反馈：分数看不懂、精读只有"结论"没有"为什么做"、反馈只有一个按钮、来源只有 arXiv、Zotero 形同虚设。

### 新增

- **精读卡显示分数构成**。排序分拆成五项：关键词 / Zotero / 分类 / 库内 / 反馈，每项一条色条 + 数值，合计仍等于原来的总分。`ScoreBreakdown` 落在 `Paper.breakdown`，`DIGEST_JSON` 里也有一份，事后可对账。
- **精读卡新增「研究背景 · 为什么做这个」与「研究方法 · 具体怎么做」**。前者回答"这个问题为什么值得做、缺了什么"，后者把估计量、建模、样本 cut、协方差的一次性讲清，不再受 20 分钟阅读时长约束——`DEEP_PAPERS` 只决定排版篇数，模型不再被要求为了卡时长删内容。
- **反馈改为两维**：相关性（直接相关 / 沾边 / 无关）与有用性（很有用 / 一般 / 没用）各三档，互不覆盖。旧 `up/meh/down` 仍兼容，折算成两维同档。
- **期刊来源**：新增 `arxiv_digest/journals.py`，用 Crossref 抓 ApJ / MNRAS / Nature Astronomy / AJ / A&A，`config/journals.yaml` 配 ISSN 与领域包。与 arXiv 结果按标题去重，同标题优先保留 arXiv 版并把期刊信息补上去。
- **期刊主题闸门**：期刊什么都发，`_journal_topic_terms()` 只放行领域包里的诊断性主题词（星系团/群、弱透镜、质量标定、BAO、暗能量、模拟…），行星与恒星物理稿件被挡在门外，避免每天被噪声稀释。
- **Zotero 从"词袋加成"升级为"库内判断"**：直接取库条目（含 DOI / arXiv ID / 标签），DOI → arXiv ID → 标题三级匹配判断"这篇我是不是已经收过"；命中的论文扣 3.0 分并标注「已在 Zotero 库」，`ZOTERO_SKIP_COLLECTED=true` 可改为直接不推。库内标题与标签还会作为【读者已收藏】块喂进评估 prompt，让模型点名重复工作。
- `scripts/feedback_server.py`：本机 stdlib HTTP 端点，收邮件里的六个按钮写入 `config/feedback.json`；`docs/FEEDBACK.md` 同时给了 Google Sheet + Apps Script 的免费方案与 `FEEDBACK_SHEET_CSV_URL` 回读。

### 修复

- **分类加成封顶**：`0.2 × 分类数` 原来挂满 cross-list 就能白拿 1.2 分，导致"分类多=高分"。现在按 `min(分类数, 2)` 计，最多 0.4。
- **Zotero 相似度收进区间**：原来只对库侧归一化、无上界，热点领域能刷出离谱高分；现在夹在 `[-2.5, 3.0]`。
- **`archiveID="arXiv:2610.12345"` 解析不出 arXiv ID**：库里这种写法很常见，现在会剥掉 `arXiv:` 前缀再比对。

### 迁移

- `config/feedback.json` 旧格式（v1，`{"votes": {"id": "up"}}`）自动读成两维记录，不需要手工转换。
- `selfcheck` 从 22 项增到 26 项（分数构成 / 期刊来源 / 期刊闸门 / Zotero 库匹配）。

## v0.11.4 — 让「领域包」真正参与抓取与理由（2026-10-26）

### 修复

- **领域包现在决定抓哪些分类**。`ARXIV_CATEGORIES` 未显式设置时，抓取分类由 `FIELDS` 选中的领域包取并集（例如 `galaxy_cosmology` + `galaxy_formation` → `astro-ph.CO, astro-ph.GA`），都没有才退回天文两分类，日志会打印 `按领域包解析出抓取分类：…`。此前 `FIELDS` 只影响评论判据，抓取永远用钉死的两分类。
- **精读卡的标签不再显示英文占位符 `category / date match`**。那是我早期写的兜底文案，并不是判据。现在每篇都会带真实理由：`领域包：星系宇宙学` 或 `通用判断`；确实没有理由时才退成中文「分类匹配：astro-ph.CO」。

### 注意

- 排序分本身未改（仍是关键词 + Zotero 相似度 + 分类数量加成），这一版只让「为什么它在」变得可见、可核对。
- 如果你在 `.env` 或 Actions Variables 里显式写了 `ARXIV_CATEGORIES`，它仍然优先——此时改 `FIELDS` 不会改变抓取范围，删掉该变量才会交给领域包。

## v0.11.3 — 修 Actions 回写状态时的推送冲突（2026-10-26）

### 修复

- **`Persist state` 步骤不再直接 `git push`**。原来只要你在本地推了任何 commit，正在跑的 Action 回写 `state.json` 就会被拒（`! [rejected] main -> main (fetch first)`），整个 job 标红。
- 新做法：先 `cp` 出这一轮跑出来的状态 → `git fetch` + `git reset --mixed origin/<branch>` 把仓库挪到远端最新 → 贴回该状态 → **只提交 `state.json` 一个文件** → 推送失败重试 3 次。全程不 merge、不 rebase，所以不会冲突，也不会覆盖别人推上来的提交。
- 用本地模拟验证过：远端先被另一个 runner 推了 commit 的情况下，我方 `state.json` 正确覆盖，对方提交仍保留在历史里，push 成功。

### 已知行为

- 推送三次都失败时仍会标红（这时去重记录确实没写回，第二天可能重复推送），但会打印明确原因。

---

## v0.11.2 — 修「世界在动」在云端整体消失（2026-10-26）

### 修复

- **"今天"改用 `DIGEST_TIMEZONE` 计算**（`pipeline._digest_today`）。原来用 `datetime.now().date()`，取的是**运行机器的时区**：GitHub Actions 跑在 UTC，北京时间 01:22 触发时 UTC 还是前一天 17:22，算出来的日期与昨天写进 `state.json` 的 `fetched` 撞车 → 所有"每天一次"的源被判定"今天已经抓过"而集体跳过，邮件里「世界在动」整块消失。
- **重置随仓库提交的 `state.json`**：原文件里 9 个源的 `fetched` 全是 `2026-10-25`、31 条 RSS 已在 `seen` 里，是本地调试留下的数据，会让云端第一次跑就什么都显示不出来。

### 新增

- 诊断日志：跳过源时打印 `按抓取频率跳过：desi、esa、eso、keck（今天已经抓过）`；源都抓到了但条目全被去重时打印警告，直接说明"想重看就删 state.json 里的记录"。

### 已知行为

- 同一天在 Actions 上手动重跑第二次，「世界在动」仍为空 —— 条目确实都推送过了，这是去重生效，不是故障。

---

## v0.11.1 — 网格纹理 / 卡片发光 / 双主题（2026-10-25）

### 新增

- **网格纹理**：报头用两层 `repeating-linear-gradient`（0°/90°，24px 网格）铺出坐标纸质感。深色版透明度 5.5%，几乎只在斜看时出现；浅色版 10%，像信纸。
- **卡片发光**：精读卡加 `box-shadow`——深色版是青色外发光（`0 0 24px rgba(34,211,238,.10)`），浅色版是中性投影。用眼睛看是"卡浮起来了"，不是"加了个边框"。
- **双主题 `EMAIL_THEME=dark|light`**：模板里 185 处颜色全部换成 `{{ P.xxx }}` 令牌，由 `arxiv_digest/theme.py` 提供两套 54 个色值的调色板。
  - `dark`（默认）：深空冷色，`#05080f` 底 + 青/靛/绿松/紫/天蓝/琥珀六色相。
  - `light`：v0.10 的暖纸版，一键切回。
- 命令行可临时覆盖：`python scripts\build_preview.py --theme light`、`python -m arxiv_digest.main --theme light`。

### 改进

- **文档**：README 中英双语重写（`README.md` / `README.en.md`），补齐现状、使用说明、升级说明、数据边界与已知限制。
- 主题是**渲染期**决定的，`digest.json` 里不存颜色——同一份数据可以出深色邮件、浅色邮件、以后出网页或 PDF。
- 新增自检「主题配色」：两套键集必须完全一致、深浅底色必须不同、别名（深色/暗色/夜）必须归一、渲染后不得残留 `{{ P.` 变量 → 现在 **22 项**。

### 已知限制

- 网格与发光依赖 CSS `background-image` / `box-shadow`。Outlook（Word 渲染引擎）会忽略它们，退化为纯色底 —— 不影响可读性。
- 邮件客户端普遍不认 `prefers-color-scheme`，所以"跟随系统深色"做不到，只能显式配置。

## v0.10.0 — 可下载转发 / 世界在动加厚 / 板块配色（2026-10-25）

### 新增

- **邮件附件**：每封附带 `arxiv-digest-YYYY-MM-DD.html`，下载后可直接转发到群里；`EMAIL_ATTACH_HTML=false` 可关。页脚会写明附件文件名。
- **板块配色**：精读=琥珀、速览=靛蓝、世界在动=松绿、脉搏=紫、雷达=钢青、日程=赭。每块标题行带浅色底 + 4px 色条，标题 16px→21px。
- 自检新增「邮件附件」（无附件=alternative、有附件=mixed、文件名与内容都在）。

### 改进

- **世界在动**：英文原标题改成带 `EN` 标签的独立行（11.5px 灰斜体），和中文标题拉开层级；每条摘要从 50-100 字加到 **150-220 字**，写清"是什么 / 怎么做 / 结论边界 / 意味着什么"四段。
- 翻译取样 600→900 字符，给长摘要留出素材。

## v0.9.0 — 思考模式全显式化 + 二手翻译分批（2026-10-25）

### 新增

- **六处调用各有思考开关**：`LLM_THINKING` / `ANALYSIS_EXTRACT_THINKING` / `ANALYSIS_ASSESS_THINKING` / `PULSE_THINKING` / `RADAR_THINKING` / `TRANSLATE_THINKING`。三态：`true` 强制开、`false` 强制关、留空跟随模型默认（DeepSeek 关、其他端点不传）。
- `TRANSLATE_LIMIT`（默认 40）。

### 修复

- **「世界在动」后半截全是英文**：`translate_items` 的 `MAX_ITEMS=12` 把 31 条截成 12 条，并且一次调用翻 12 条、截断即整批作废。改为每批 8 条分批翻译，单批失败不影响其他批。实测 12/31 → 30/31。
- 翻译超出上限的条目现在原样保留（以前直接丢弃）。

## v0.8.2 — 首次真跑修复（2026-10-25）

### 修了

- **`--out` 与 SMTP 预检**：`python -m arxiv_digest.main --out preview_live.html` 只渲染不发信；没配 SMTP 时**开跑前**就提示并退出（以前是抓完 arXiv、调完模型才崩）。退出码 2。
- **模型名清洗**：`.env` 里 `PULSE_MODEL=   # 注释` 会被 dotenv 原样读成 model 名（`# 注释`）→ 400。`.env.example` 改成注释独立成行，并在 `_env_model()` 里截断行内注释、剥引号、空值回落默认。
- **回看窗口自适应**：周末/节假日 arXiv 不公告，固定 1 天窗口会得到空简报。现在按 1→2→3→5→7→10→14 天递进直到凑够篇数，邮件头部标注「覆盖近 N 天」。
- **思考模式预算**：DeepSeek 默认开思考，翻译这类简单任务会把 `max_tokens` 全吃在 reasoning 上、content 为空。现在非显式指定时 DeepSeek 一律关闭思考；若仍遇到"思考吃光预算"，自动关思考重试。
- **截断分支的 `content` 未赋值**（`UnboundLocalError`）：调整判断顺序。
- **空简报不发信**：arXiv 无新投稿且二手源也无更新时跳过发信，只记一条 warning。

## v0.8.1 — max_tokens 校准与截断兜底（2026-10-25）

### 改了

- **上限上调**：抽取 4000→6000、评论 6000→9000、总览/摘要 2500→4000、脉搏 6000→8000、翻译 2000→3000。依据见 `docs/PRODUCT_PLAN.md` §14.12.6（按实测 analysis JSON 1,426–2,246 字符反推）。
- **截断自动放大**：`finish_reason == "length"` 时 `max_tokens` 翻倍重试一次（上限 32,000）——把"给窄了"退化成"慢一次"，而不是整篇退回英文。
- **用量日志**：每次调用打印输入/输出/思考 token，第一次真跑就能校准。
- `selfcheck` 的「模型调用层」增加截断放大断言（1000 → 2000）。

## v0.8.0 — 模型调用层与 DeepSeek 适配（2026-10-25）

### 新增

- **统一的模型调用层 `llm_client.py`**：`call_json(client, model, prompt, *, system=None, options=None)`，五处调用点（总览、抽取、评论、脉搏、雷达、翻译）全部改走它。三个实打实的问题在这里一次解决：
  - `response_format` 不被支持时**自动降级**为提示词约束 JSON 并重试（兼容网关会报 `Unrecognized request argument`）；
  - **空 content 自动重试**（DeepSeek 文档承认 JSON 模式有概率返回空，以前会把整批论文打回英文）；
  - **思考模式三态**：`None` 不发、`True/False` 显式开关。思考模式下 `temperature` 不传（DeepSeek 会静默忽略），发给 OpenAI 也不会 400。
- **`LLMOptions(temperature, max_tokens, thinking, attempts)`**：所有调用显式给 `max_tokens`——DeepSeek 文档明确要求，否则 JSON 会被中途截断。
- **三态配置**：`ANALYSIS_EXTRACT_THINKING`（默认 `false`，抽取要稳要省）、`ANALYSIS_ASSESS_THINKING`、`LLM_THINKING`、`PULSE_THINKING`（默认留空 = 跟随端点）。
- **模型名兜底**：配了 `OPENAI_BASE_URL=https://api.deepseek.com` 却忘写 `OPENAI_MODEL` 时，自动用 `deepseek-flash`，少一个配置陷阱。

### 改了

- 默认温度按任务分开：抽取 `1.0`（官方推荐的数据抽取值）、评论 `1.0`、脉搏 `1.0`、翻译 `0.3`。
- `ANALYSIS_EXTRACT_MODEL` / `ANALYSIS_ASSESS_MODEL` 留空即跟随 `OPENAI_MODEL`（`.env.example` 里给了「抽取用 flash、评论用 pro」的推荐组合）。
- `README` 的「LLM 模型配置示例」按 2026-10 官方文档重写：模型只有 `deepseek-flash` / `deepseek-v4-pro`，`deepseek-chat` 已下线。
- `selfcheck` 扩到 **20 项**（新增「模型调用层」：围栏容错 / 空内容重试 / 格式降级 / thinking 三态 / max_tokens）。

### 已知

- 思考模式的 `reasoning_content` 目前**没有回传**（只取 `content`）。脉络长时无法复用，暂不影响正确性。

## v0.7.0 — 版式重做与可读性（2026-10-25）

### 改了

- **报头**：刊名（DAILY ARXIV DIGEST）+ 日期 + 一行量级摘要（精读 4 篇 · 速览 10 篇 · 二手解读 17 条 · 20 分钟）。深色渐变头改成暖纸底 + 3px 墨色下边，更像期刊而非通知。
- **分区眉题**：精读 / 速览 / 世界在动三处统一为「小号英文眉题 + 中文标题 + 1px 细线」，替换原来的 13px 粗体 + 2px 黑色下划线（那条线在手机上比标题还抢眼）。
- **配色**：55 个色值收敛成一套语义色——暖纸底 `#f3f0e9`、墨色 `#1b2431`、正文 `#29323c`、次要 `#6b7280`、琥珀 `#b45309`、藏青 `#1f4e6b`、紫 `#6b4c9a`、绿 `#157a42`、红 `#b3261e`。原来的纯黑、亮橙、宝蓝、亮紫全部降饱和，长文阅读不刺眼。
- **卡片**：边框 `#e6e1d7`、圆角 10px、白底，与暖纸底形成层次；论文标题 17.5px，行高 1.8。

### 修了

- **速览摘要不完整**：折叠块里原来放的是 `brief_zh`/`brief_en`（110/170 字截断），现在放 `chinese_summary`（完整中文）+ `summary`（arXiv 英文原文全文）。截断句只保留在速览列表的一行预览里。
- **二手解读太短**：`text_zh` 从 40–60 字扩到 60–100 字，每条写清"发现了什么 / 怎么做的 / 为什么值得知道"；`translate.py` 的提示词从"不超过 60 字"改成"50-100 字，写清三件事，不许用套话凑长度"。

## v0.6.0 — 机构源分频、宋体排版与公式（2026-10-25）

### 新增

- **机构与项目源**：NASA（每日）、ESA / ESO / DESI / Keck（每周）。每个源可写 `cadence: daily|weekly`，由 `state.json` 的 `fetched` 记录上次抓取日。
- **`exclude` 过滤**：`nasa` 源用 `exclude: ["APOD:"]` 挡掉每日一图，避免"新闻流"被天文图片塞满。
- **公式排版 `mathfmt.py`**：`$H_0 = 67.4 \pm 0.5$` → `H₀ = 67.4 ± 0.5`，`$10^{-3}$` → `10⁻³`，`$\Lambda$CDM` → `ΛCDM`。经 Jinja `finalize` 应用到所有字符串输出。
- **字体**：正文改 `'Times New Roman' … 'Songti SC' / 'SimSun'`，英文 Times、中文宋体。

### 改了

- `State` 增 `fetched`；新增 `should_fetch / due_ids / mark_fetched`。
- `SourceResult` 增 `key`（回写节奏用）；`collect_sources(..., skip_ids=...)`。
- `selfcheck` 扩到 **19 项**（新增「公式排版」）。

### 修掉的

- 之前所有源每天全抓：DESI 这类一周发不了一条的站点被白抓六次，还增加被限流的风险。

### 已知

- 公式只覆盖常见符号；矩阵与 `\begin{}` 环境退化成纯文本。
- 邮件客户端不保证字体：iOS Mail、Outlook 可能替换，版式按"字体退化也不塌"设计。
- 脉搏"更丰富"本轮只做调研，形态待定。

## v0.5.0 — 会折叠的雷达 + 更像样的一封信（2026-10-25）

这一版不加新数据源，只解决"**信息密度与可读性的矛盾**"：默认看得少，想看的点开就有。

### 新增

- **邻域雷达改块**：每个方向取 3 条、只展 1 条，其余进 `<details>`（`RadarBlock.headline / rest / total`）；每条带 80–110 字解读。
- **速览中英摘要折叠**：`<details><summary>展开中英文摘要</summary>`，中文解读 + 英文原句并列。
- **世界在动扩容与归并**：二手解读与会议报告合并成一块；每条前置来源色块（AAS Nova / Astrobites / ESO / Phys.org / Universe Today），标题中英并列。
- **信息源**：新增 Phys.org 天文、Universe Today；各源 `max_items` 提到 5–6。

### 改了

- 正文行高 1.65→1.72；分区标题统一 13px/700/字距 0.8；卡片圆角 12px、阴影更柔。
- 雷达与二手条目按发布时间倒序，免得旧闻压住新消息。

### 修掉的

- 雷达曾显示论文的"主分类"而不是"匹配到的分类"，导致系外行星条目挂着 astro-ph.CO。
- 同一篇论文曾同时出现在两个方向的版面上 → 加全局 `used_ids`。

### 已知

- 预览里「本周投稿量变化」为空：它需要连续两天的状态文件，预览刻意不写状态。
- Astrobites 在数据中心 IP 上返回 403，GitHub Actions 上正常。

## v0.4.0 — 邻域雷达 / 二手翻译 / 反馈闭环 / 评测集（2026-10-25）

- **`arxiv_digest/radar.py`**：跨领域"只挑不读"的雷达；信号词加权 + 规则解读，有模型时换成判断句。
- **`arxiv_digest/translate.py`**：二手解读与会议报告的中文翻译（失败原样保留英文，不阻塞发信）。
- **`arxiv_digest/feedback.py`** + `scripts/feedback.py`：👍/😐/👎 三种投票，分类与作者两级权重学习，`MAX_BOOST=1.8`；**故意不做关键词自动改写**。
- **`scripts/eval_analysis.py`**：20 篇评测骨架，检查覆盖、引文保真、字段填充、数字召回。

## v0.3.0 — 本周脉搏（2026-10-25）

v0.2 解决了"这一篇讲了什么"，v0.3 开始回答"**这一周到底在动什么**"。
之前的"新动向"只到日程倒计时——那是记录，不是判断。

### 新增

- **`arxiv_digest/pulse.py`**：`Material` / `TopicHit` / `Thread` / `Pulse` 四个数据类；`materials_from()` 把论文、二手解读、会议报告摊平成同一个池子；`count_topics()` 按领域包 aliases 计数（标题权重 2、正文权重 1、二手 2.0×、报告 1.5×）；`_TENSION_RE` 识别 tension / discrepancy / inconsistent / puzzle / challenge / anomalous / at odds 等措辞；`compose_pulse()` 调一次模型写 3-4 条判断；`coerce_threads()` **丢弃没有 evidence 的判断**；`fallback_pulse()` 无 key 时只报主题统计。
- **领域包 `pulse.topics`**：`galaxy_cosmology` 12 个主题、`galaxy_formation` 8 个；换领域包=换观察维度，代码不动。
- **邮件「本周脉搏」区块**：头条 + 3-4 条判断（状态胶囊：新结果 / 有争论 / 尚无定论）+ 证据链接 + 主题分布条形图 + 页脚材料与源计数。
- **`scripts/refresh_pulse.py`**：现场抓一次 arXiv + RSS，把材料与主题统计写进 replay，供离线重渲。

### 修掉的

- `Material.source` 曾用 arXiv 分类名充当，导致每篇论文都成了"独立源"，跨源共现永远为真 → 统一为 `arXiv`。
- 短别名 `desi` 命中了 `design`，DESI 提及数虚高 → 别名按词边界 `(?<![A-Za-z0-9])…(?![A-Za-z0-9])` 匹配。

### 已知限制

- **二手源对宇宙学基本空转**：这周 7 条 AAS Nova / ESO 报道全是 TDE、黑洞占据率、系外卫星，一条宇宙学都没有，因此 8 个主题全是"单源"。要让"大家在讨论什么"真正成立，得补同领域的高频源（CosmoCoffee、Bluesky 上的宇宙学圈子、合作组公告），而不是调大现有 RSS 的条数。
- 通用词主题（如 `environment`）仍会带进噪声，计数高不等于重要。
- 状态胶囊的 debate / unsettled 依赖模型判断，材料里没有争论措辞时容易全判成"新结果"。

## v0.2.0 — 初稿（2026-10-25）

这一版把"每日摘要邮件"改成"有人读过原文的简报"。分成五个批次的改动记录在
[`docs/PRODUCT_PLAN.md`](docs/PRODUCT_PLAN.md) §14，这里只列对人可见的变化。

### 邮件

- **精读卡 10 分区**：星级 → 一句话结论 + 阅读建议 → 方法链 → 七问七答 → 关键数字 → 图表线索 → 今天能做什么 → 红旗反查 → 术语小抄 → 中文摘要 → 英文原题摘要（可折叠）。
- **速览行中英双语**：英文取 arXiv 摘要原句，不翻译——它是核对锚点，不是装饰。
- **全文精读**：精读篇目下 PDF 抽正文，徽标显式标「全文」（绿）/「仅摘要」（橙）。
- **近期日程**：数据发布、会议、提案截止倒计时；日期只到月/年时显示"预计 2027 年 3 月"，不假装精确。
- **世界在动**：二手解读（Astrobites / AAS Nova / ESO）与会议报告（Indico），每条带来源与日期。
- **本周投稿量变化**：本周 vs 上周的分类投稿数，凑够两天状态才出现。
- **页脚信息源健康**：每个源 ✓/✗，坏了能一眼看见。

### 分析

- **分批调用**：默认 5 篇一批，单批失败只降级那一批（原来 14 篇一次 `json.loads`，一次截断全篇变英文）。
- **两阶段**：阶段 A 抽事实（每条带原文引文，抽不到写"摘要未提"）→ 阶段 B 按读者画像 + 领域包写判断。
- **领域包**：`config/packs/*.yaml` 提供 `evaluation.focus` 与 `red_flags`，换学科不改代码。
- **读者画像**：`config/profile.yaml` 决定评论回答哪五个问题、保留哪些英文术语、禁说什么。

### 工程

- **离线自检 14 项**：`python -m arxiv_digest.selfcheck`，不联网、不需要 API key、不发信；CI 每次先跑。
- **跨天状态**：`state.json`（已读去重 + 每日投稿量），原子写入，Actions 每天 commit 回去。
- **信息源容错**：每个源独立 `SourceResult`，任一源 403/超时/改版都只影响它自己那一行。
- **PDF 重试**：下载失败重试一次 + 退避（真实网络下单次尝试太脆）。
- **本地 SSL 修复**：`urllib` 显式带 certifi 上下文，本机不再报 `CERTIFICATE_VERIFY_FAILED`。
- **CI**：`selfcheck` job + `send-digest` job；`permissions: contents: write` 用于回写状态。
- **LICENSE**：MIT。

### 排程

- cron 从 `37 0 * * *` 改为 `30 5 * * *`。原因：原表达式在**冬令时**会比 arXiv 当日公告早 23 分钟，整天空跑。

### 已知限制（下一步）

- 全文 PDF 是**摘要级 vs 全文级**的分水岭，但 equation/扫描版 PDF 抽取会掉字；目前只对精读 4 篇做。
- 周环比要求状态文件积累两天以上，新装用户第一周看不到该区块。
- 二手源对国内网络不友好（Astrobites 本机 `403`，AAS Nova 与 ESO 正常）；Actions 上不受影响。
- Indico 会议需手动维护 `event id`，`config/sources.yaml` 里给了禁用示例。
- 反馈闭环（M4）尚未实现：点开/收藏行为没有回传，权重不会学习。

## v0.1.0 — 改造前基线

- 每日 arXiv 抓取、关键词 + Zotero 排序、LLM 中文摘要、HTML 邮件、GitHub Actions 定时发送。
