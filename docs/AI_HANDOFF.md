# 给下一位 AI 的交接说明

最后更新：2026-10-30，**v0.19.1 工作区**。先读 [长期热点档案](ARCHIVE.md)、[期刊全文兜底报告](CHANGE_REPORT_2026-10-26_JOURNAL_FULLTEXT.md)、[审查修复报告](CHANGE_REPORT_2026-10-26_PATCH.md)、[v0.14.0 审查全文](REVIEW_2026-10-26_V2.md)、[周报/资讯改动与验收报告](CHANGE_REPORT_2026-10-26_V014.md)、[v0.14 验证记录](VALIDATION_2026-10-26_V014.json) 和 [配置说明](CONFIG.md)。然后按需回看 [v0.13 复审](REVIEW_2026-10-26_POST_FIX.md)、[原审阅基线](REVIEW_2026-10-26.md) 与 [v0.13 改动报告](CHANGE_REPORT_2026-10-26.md)。`PROJECT_REVIEW.md` 是早期历史材料。

## v0.19.1 当前进展：精读字段不再被三道闸门清空

- 真实全文下"数据/处理/系统误差/方法/结果/作者局限"大面积为空，根因不是节选而是校验过严：①`extract_bundle` 要求 value 的每个数字都出现在它自己那条短 quote 里 ②`quote_in_source` 只认逐字子串，扛不住 PDF 断词连字符与合字 ③模型偶尔只返回字符串、没有 quote。
- 现在：数字按**全文**校验，对不上的按分句抢救（`_keep_fact` → `_salvage_text`）；引文比对先 NFKC 压缩再匹配（短于 12 字符仍要求逐字）；无 quote 的字符串事实在数字可回查且 ≥10 字时保留；Stage B 的 `supported_text` 纳入 `value`。
- 抽取 prompt 明确"字段必须是 `{value, quote}` 对象，quote 逐字照抄英文"。
- 验证：**194 项离线测试（新增 11 项 `tests/test_evidence_quotes.py`）、27 项自检、`pip check` 通过**；真实模型 + 真实 PDF 全流程复跑（4 篇精读）：两篇 7/7、一篇 5/7，"全文节选未检出"13 → 4 处，余下是期刊论文无 arXiv 预印本、basis 退化为摘要所致。
- 边界：quote 缺失时只剩数字校验，属有意放宽；真要逐句核对仍需看卡片里的引文。

## v0.19 当前进展：精读直接读整篇 PDF

- `FULLTEXT_MAX_CHARS=0` / `FULLTEXT_MAX_PAGES=0` 为新默认：`fetch_fulltext` 下载的全文（砍参考文献后）原样返回，`condense` 与 `analysis._excerpt` 只在显式正数预算时才裁剪；`_extract_pdf_text` 在 `max_pages<=0` 时解析全部页。
- 单篇全文分析抛错时自动用摘要重跑一次（`analyze_papers`），避免一张卡片整篇空白；每篇日志打印"全文 N 字符，整篇送入抽取"。
- 验证：**183 项离线测试（新增 7 项 `tests/test_fulltext_unlimited.py`）、27 项自检、`pip check` 通过**；真实 PDF `2610.35100` 抽出 49,750 字符（旧 40k 预算下仅 24,977），`sample`/`systematic` 在文中。真实模型两阶段与 SMTP 未复跑。
- 已知边界：全文远超模型上下文时仍会失败，走摘要降级；`MAX_EXTRACT_CHARS=500_000` 是抽取层硬上限，触顶会 warning。

## v0.18 当前进展：中文摘要与雷达口吻

- 模型漏条目不再回落英文：`_fallback_paper_summary` 返回空串，`generate_digest_content` 对缺失条目单篇补写（最多 6 篇），失败后以"不要出现任何数字"再试一次；`_salvage_summary` 按分句保留可回查内容，删太多则判半句、交给重试。
- 精读卡摘要只接受"含中文 + 数字可回查"的文本，否则用 `verdict + result` 组合；模板在无摘要时明确写"英文原摘要见下方"。
- `evidence.numbers_supported_ignoring_dates` 让 `2026 年 7 月` 与 `July 2026` 等价，`translate` 改用该版本，世界新闻不再整段回落英文。
- 雷达从"每方向一条卡"改成 `Radar.overview` 一段话（模型写 150–260 字，数字回查；规则兜底也成段），条目收进 `<details>`；"扫一眼即可"已从代码与模板删除（自检有断言）。
- 验证：**176 项离线测试（新增 13 项）、27 项自检、`pip check` 通过**；真实模型 5 篇 5/5 中文，雷达段渲染已截图确认。未验证：全量邮件、SMTP、云端 Actions。

## v0.17.1 当前进展：合表自检

- 新增 `scripts/check_sheets.py`（`python -m scripts.check_sheets`）：只读检查反馈 CSV 表头（识别"CSV 指到档案表"的 gid 错配）、HTML/空响应、档案端点 `health`；退出码 0/1/2，不写行。
- 文档：`docs/ARCHIVE.md` 增加「合表部署清单（推荐）」六步；`docs/FEEDBACK.md` 与 README 命令表补入口。
- 验证：**163 项离线测试（新增 10 项）、27 项自检、`pip check` 通过**；真实端点、gid 与权限仍未验收。

## v0.17 当前进展：反馈表与热点档案合表

- 新增第五张档案表 `feedback_view`（`week_start + topic` 键）：把两维投票按论文主题分摊聚合，`votes / mean_relevance / mean_usefulness / top_useful / updated`；均值只算给出该维的票。与 `weekly_topics` 同键，可直接并排看"变多"与"对我有用"的差别。
- `archive.build_feedback_rows` 用 `canonical_id` 对齐 arXiv 版本号与 DOI 大小写；`record(..., votes=...)` 无票不写表；`PipelineResult.votes` 由 `_run_pipeline` 从合并后的 `FeedbackStore` 传出。
- `scripts/google_feedback.gs` 不再假设"第一张表"：脚本属性 `FEEDBACK_SHEET_NAME` 指定反馈 tab（缺省仍第一张，向后兼容），找不到时明确报错；档案脚本 `SHEETS` 白名单只加 `feedback_view`，读不到投票原文。
- 合表部署：一份 Spreadsheet + 两个 Web App；`FEEDBACK_SHEET_CSV_URL` 的 `gid` 必须指向反馈 tab。
- 本轮验证：**153 项 Python 回归（新增 5 项反馈聚合）、27 项自检（热点档案增加反馈断言）、`pip check` 通过**。真实 Apps Script 端点、共用表格的 gid/权限、云端 Actions **未验收**。

## v0.16 当前进展：长期热点档案（影子期）

- 新增 `arxiv_digest/archive.py` + `scripts/google_archive.gs`：把**当天抓到的全部论文**（不只入选 14 篇）按周写入私有 Google 表格，四张表 `papers` / `weekly_topics` / `weekly_overview` / `dict`。
- 接线：`_run_pipeline` 末尾构建 `PipelineResult.ledger`（`archive.ready(config)` 才构建，异常只 warning）；`commit_delivery` 在 feedback 持久化之后、写 `delivery` 之前调 `archive.record(...)`，失败只写 `report["archive"]`，不阻断投递；预览永远不写表。
- 口径：周从周日开始（与周报一致）、按 `published` 本地日期归档；`share` 为样本内占比（多主题可 >100%）、上周为 0 时 delta 留空、`ma4` 为四周均值、`rising/falling` 需两周各 ≥2 篇。规则打标用领域包 `topics` + 关键词，`concepts` 只来自精读 4 篇。
- 配置：`ARCHIVE_ENABLED/ARCHIVE_WEBHOOK_URL/ARCHIVE_TOKEN/ARCHIVE_TIMEOUT/ARCHIVE_MAX_ROWS/ARCHIVE_SNIPPET_CHARS`；URL 或令牌为空即整体跳过。`--health` 可单独检查端点。
- 本轮验证：**148 项 Python 回归（新增 19 项）、27 项自检（新增热点档案）、`pip check` 通过**。真实 Apps Script 端点、云端 Actions 与表口径是否符合直觉（影子期 2 周）**未验收**，不要在报告里写成已验证。
- 幂等：Apps Script 按复合键 upsert；每天写当周增量并重算聚合，周日 `finalized=1`，同时回填上一周。

## v0.15.1 当前进展：本地试跑复查（在 v0.15.0 之上）

- 用户 22:16 的本地预览暴露三处：精读卡"全文节选未检出"满屏、一句话结论直接显示"全文节选未检出"、推荐依据显示 `&amp;middot;`。
- 修复：`analysis._excerpt` 不再 `text[:36000]` 硬截断，改用 `fulltext.condense`（开头 45% + 样本/筛选/系统误差/协方差关键词句），同一份缓存 PDF 复核 `sampling`/`systematics`/`covariance` 三项从"旧节选缺"变为"新节选全含"；`_strip_missing_notes` 从 `background`/`method_detail`/`verdict` 删占位标记并收拾标点，verdict 空则退 `result[:60]`；模板分隔符改真实 `" · "`；`load_config()` 对 `ARXIV_KEYWORDS`/`ARXIV_CATEGORIES` 去重（重复关键词会重复计分）；workflow 补 `PREPRINT_*` 映射。
- 验证：**129 项 Python 回归、26 项自检、`pip check` 通过**；回放快照重渲染 `&amp;middot;` 为 0、` · ` 为 51。仍未验收：SMTP、反馈端点、Zotero、云端 Actions、真实模型抽取质量（`deepseek-flash` 在 36k 字符节选上仍可能把 data/processing/result 抽空，属模型能力边界）。
- 已知设计：预览只写 `runs/<run_id>.json`；`DIGEST_JSON_PATH` 默认 `digest.json`；`run_pipeline` 不确认投递，只有 `commit_delivery` 在发信成功后消费条目。

## v0.15 当前进展：期刊论文也能读全文

- 新增 `arxiv_digest/preprints.py`：`doi:` 论文按标题反查 arXiv（标题相似度 ≥0.90 + 作者姓氏 + 年份 ±2），命中就用预印本 PDF 精读；`venue`/`doi`/`id` 仍是期刊，UI 不改标 arXiv；未命中回退摘要。
- `_fetch_fulltexts` 的doi 分支、`_merge_journals` 的摘要补全都接上了；`fetch_journal_papers(..., preprocess=)` 让 Nature Astronomy 这类"Crossref 只给标题"的条目先补摘要再过主题闸门。
- 正负缓存（命中 180 天 / 未命中 14 天）+ ≥3 秒节流；`cache.prune_cache` 一并回收 `preprints/`。
- 数值护栏改为按分句降级（`analysis._salvage_text`）：只有个别数字不可回查时不再整段清空 `background`/`verdict`/七问；verdict 回退 `result` 生成一句话结论。
- 新增配置 `PREPRINT_ENABLED/PREPRINT_TIMEOUT/PREPRINT_MAX_RESULTS/PREPRINT_ABSTRACT_LOOKUPS`；`.env.example` 已同步。
- 本轮本地验证：**126 项 Python 回归、26 项自检、`pip check` 通过**；真实外网一次性核验 6 篇期刊命中 3 篇，两篇精读全部取到全文（118,165 / 80,514 字符），来源仍标 ApJ / A&A。未跑 SMTP、反馈端点、Zotero、云端 Actions；`node` 不在本会话 PATH，10 项 JS 契约未复现。
- 未验收项与风险（预印本版本差异、标题改名匹配不上、pypdf 排版限制）见上述期刊全文报告 §3.3 与 §5。

## v0.14 当前进展

- 默认配置时区的周日生成过去七个完整自然日的论文总集；最多 12 篇，精读位最多 6，不以弱相关论文凑数。
- 周报允许重新推荐工作日已发论文；通过 `state.editions` 按窗口去重。成功投递才记录；预览忽略已投递周报标记、不提交。周报论文不写日报 seen。
- 日报/周报使用关键词、画像主要研究对象、领域诊断短语筛选并解释；模型明确判为 off 的条目排除且不消费。
- 世界在变扩为天文/物理/化学/历史考古；目标约 30、最多 36、天文与其他合计 1:1，库存不足互补。近七日且有有效日期，重复链接/标题/ID 去重；新六个公开 RSS 已实际 HTTP/XML 检查。
- R01–R09 已有代码或配置修复及对应回归，详见最新报告。工作流分组的云端效果仍未测试，不要标为生产验收完成。
- 最终本地：107 项 Python 回归、26 项自检、10 项 JS 检查通过；48 个 Python 文件、9 份配置 YAML、109 项 env 映射检查通过。Actions 均固定官方提交 SHA，并配置业务缓存。
- 本轮没有安装依赖、改 `.env`/真实 state、调用真实模型、SMTP、云端部署或 commit/push。保留全部原有未提交改动。

新增核心文件 `editorial.py`（周报/兴趣/新闻策略）、`evidence.py`（共享保守数值规则）、`cache.py`（有限缓存回收）、`tests/test_editions.py`（44 项验收）。固定评测 build→run→check 中 run 会调用模型；它使用冻结输入，不得误当成离线自检运行。

诊断有 run_id 和独立 runs 档案；最近入口只能由对应运行收尾更新。`state_commit_failed` 可能已被 SMTP 接受，排查后再重跑。源码仍有公开服务、模型语义、采集预算、全文内存、跨平台及完整安全审计等未验收边界，见最新报告。

## v0.14.1 审查修复（2026-10-26，承接 v0.14.0 审查）

- 预览不再改写 `digest.json`：只写 `runs/<run_id>.json`，`update_diagnostics` 按 `delivery=="preview"` 跳过"最近一次"指针。改动位于 `pipeline._write_digest_json` / `_write_diagnostic` / `update_diagnostics`。
- `merge_state.py` 不再强依赖 `run_report.json`：缺失/损坏时告警并退回当前日期与时区（`--today`/`--timezone`），workflow 传 `DIGEST_TIMEZONE`；邮件已发但 state 没提交时，下一次仍可能重复投递，需人工核对。
- run_report 新增 `analyzed_papers` / `analysis_failures`；整批分析失败仍降级为单阶段摘要，不阻断发信。
- 读坏的 `state.json` / `feedback.json` 先另存 `.corrupt` 再降级；缓存键补齐 `LLMOptions` 与 `max_chars`；`canonical_id` 剥 `.pdf`。
- 本轮本地验证：113 项 Python 回归、26 项自检、`pip check` 通过；`node` 不在本会话 PATH，JS 契约沿用 v0.14 记录，未复现。没有真实模型/SMTP/云端运行。
- 未修项与理由见 `docs/CHANGE_REPORT_2026-10-26_PATCH.md`；审查全文评分 83/100 仍是修复前基线，不得当作修复后新评分。

## v0.13 复审记录（历史，已由 v0.14 接续修复）

下面保留修复前结论作为证据索引，不表示当前这些问题仍全部开放；当前状态以 v0.14 报告为准。

本次只审阅，未修复业务代码。评分 6.6/10（基线 5.1）；重新执行 63 项回归、26 项自检、10 项 JS 离线检查均通过，仍发现现有测试未覆盖的问题。不要根据上一轮改动清单认定全部验收完成。

- P1 R01：只有雷达时 main 跳过 SMTP，却提交雷达 seen。
- P1 R02：for_you/actions/chain 能显示原文没有的数值事实。
- P2 R03–R08：远端合并恢复已清理数据；脉搏错误字段类型逃逸 fallback；归零分类趋势遗漏；429 graceful 分支失效；并发预览混淆诊断快照；固定评测集与每日流程未闭环。
- P2 R09：验证和投递共用并发组，待执行邮件可能被新验证替换；这是依据 GitHub 规则的推断，未跑真实 Actions。

复现附件：`python -B docs/review_assets/post_fix_probes.py`。它只在临时目录写入并禁网，输出的是**缺陷观察**；退出 0 不等于产品通过。修复时把观察转换成正确行为回归。完整证据、优先级、验收标准和保留限制见最新复审；本轮没有实际发信、模型计费或云端部署。

## v0.13 修复阶段（历史）

本轮从已经有大量未提交 v0.12 改动的工作区继续修复。没有 commit、push、发布或真实发信；`.env` 与实际 `state.json` 未修改。`git diff --stat` 包括用户原有修改，不能把所有差异都归因于本轮。

主要原审阅缺陷已建立对应实现和离线回归：TLS、状态提交、稳定 ID、去重、统计、反馈元数据、完整快照、证据、来源重试、期刊闸门、星级、汇总范围、坏状态、离线预览、公式分组。外部服务与语义准确性仍未验收。

## 不要破坏的约定

- `run_pipeline()` 只准备 pending_state；`main` 成功发信后才 `commit_delivery()`。HTML 预览不能消费已读状态或发送告警邮件。
- canonical arXiv ID 不含版本号；当前明确不做旧论文修订提醒。改策略必须新增迁移和回归测试。
- seen 只记录实际展示内容；observations 用于采集统计，不能混用。
- 周报不按日报 seen 过滤，不把周报论文写入日报 seen；其投递确认由 editions 负责，失败/预览不写标记。
- state v2 清空旧的不可信 daily 计数，保留兼容字段；公开 state 不允许含个人 votes。
- 个人反馈在忽略的 feedback.json；Sheet 是云端票值来源；state 仅保留学习所需论文元数据。
- digest v3 的唯一 Paper 序列化入口是 `contracts.py`，不要再复制一份缺字段的转换逻辑。
- Stage A 引文必须来自实际输入，数值必须能回查；basis 是程序元数据，不能接受模型覆盖。
- “全文”实际是节选；UI 使用“全文节选”，未检出不等于原文未讨论。
- 回放默认断网；`--fetch-fulltext` 才允许 PDF 下载，不会把旧评论升级成新生成评论。
- 领域选择保持主分类优先；`title_keywords` 辅助，`journal_terms` 控制期刊闸门，`title_hint` 只是人工说明。

## 代码地图

| 文件 | 责任 |
|---|---|
| main.py / pipeline.py | 副作用边界、待提交状态、运行报告 |
| identity.py / state.py / storage.py | 稳定身份、采集观察、迁移、原子写与 OS 锁 |
| contracts.py | 完整 Paper/Analysis 快照与兼容读取 |
| analysis.py / llm_client.py | 事实证据、数值回查、缓存、供应商参数、用量 |
| feedback.py / feedback_security.py | 逐维学习、时间合并、签名 |
| fulltext.py | 有界下载、可终止解析、缓存 |
| packs.py / sources/ / journals.py | 领域和来源装配、筛选、身份 |
| tests/test_regressions.py | 关键回归入口，默认禁网 |
| scripts/merge_state.py | Actions 并发远端更新后的状态合并 |
| scripts/google_feedback.gs | 私有 Sheet 反馈后端，云端部署仍待验收 |

## 安全验证命令

```powershell
.\.venv\Scripts\python.exe -B -m arxiv_digest.selfcheck
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -B -m pip check
node tests/check_google_feedback.cjs
```

依赖已安装；不需要为本轮测试安装新包。Windows 沙箱可能无法读取基础 Python 解释器，需使用运行环境允许的执行权限；不要把权限问题误判为虚拟环境损坏。

不读取或输出 `.env` 的实际密钥；不运行 `arxiv_digest.main` 的真实数据流程来“顺手验证”。真实预览会请求网络和模型，即使 `--out` 不发信也可能计费。

## 后续重点

- 阅读 v0.14 改动报告“保留限制”；先完成受控真实模型/SMTP/Actions 联调和人工标注，再评价内容准确率。
- 对比最新验证记录；不要复制历史测试数量作为新执行结果。
- 如改动证据、来源、状态或序列化，增加对应失败路径与往返测试。
- 当前没有 Linux/macOS 实测、完整漏洞扫描、mypy/ruff 或性能基准结果。不要声称已通过。
