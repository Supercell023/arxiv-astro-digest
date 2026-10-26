# v0.14：周日研究精选、跨领域资讯与复审修复

日期：2026-10-26，工作区版本 v0.14.0。接续 [v0.13 复审](REVIEW_2026-10-26_POST_FIX.md)，对应用户提出的周末论文总集、扩大资讯兴趣范围以及一并修复审阅问题的要求。

**本轮已实现自动周报、日常兴趣过滤、跨领域资讯选择，并为 R01–R09 落实代码或工作流修复。107 项 Python 回归、26 项自检和 10 项 JavaScript 离线检查通过。真实模型内容质量、SMTP、Actions 与反馈后端部署仍没有验收。**

工作从已有大量未提交修改的工作区继续，没有 reset/commit/push；原 `.env`、实际 `state.json` 与用户已有预览没有改写。当前 Git HEAD 仍是 `b8e56f8`，它不能单独代表本轮工作区。没有安装或升级 Python 依赖，`pyproject.toml` 项目版本升到 0.14.0。历史报告与验证文件保留，新执行记录单独保存在 [VALIDATION_2026-10-26_V014.json](VALIDATION_2026-10-26_V014.json)。

## 1. 新需求与实际行为

| 用户目标 | 实现行为 | 验证范围 |
|---|---|---|
| 周日回顾这一周最相关的论文 | 默认按 DIGEST_TIMEZONE 的周日进入 weekly，回看过去七个完整自然日，不扩到 14 天 | 固定日期窗口、API 参数、实际 pipeline/main 离线回归 |
| 总集能包含工作日推荐过的好论文 | weekly 不使用日报 seen 过滤；使用独立 `state.editions` 确认投递 | 日报已发论文能重新入选；同窗口成功后不重发；失败/预览不记录周报完成 |
| 不让较无关的论文占精读位置 | 分类不再单独作为入选理由；按关键词、画像主要研究对象和领域诊断短语筛选/排序，模型明确判为 off 的论文再排除 | 相关/无关混合样例、单强信号、主题重复计数、模型 off 与 seen 实际提交 |
| 天文为主，同时关注物理、化学、历史 | 新增六个对应 RSS 源，原天文源保留；邮件按四类分区并列出实际数量 | 六个公开地址真实 HTTP/XML 检查；配比和渲染使用隔离样例 |
| 约 30 条、天文与其他领域合计一半一半 | 30 为软目标，丰富时可到 36；其余三类轮转，任何一侧短缺由另一侧有效条目补位 | 15+5+5+5、18+6+6+6、单侧不足、全空、少量库存等回归 |
| 天文新闻少时仍有信息补充 | 资讯可独立触发邮件，不依赖主区有论文；不使用过期/无日期内容凑数 | 只有资讯时的真实 main 渲染、投递顺序与状态测试 |

默认配置不需要补写到旧 `.env`：`WEEKLY_ENABLED=true`、`DIGEST_EDITION=auto`、`WEEKLY_WEEKDAY=6`。已有 `SOURCES_ENABLED`、`ANALYSIS_ENABLED` 等显式开关继续有效；本地既有这两个开关为开启，本轮没有改动其值。

例如 2026-10-25（周日），窗口是配置时区的 `[2026-10-18 00:00, 2026-10-25 00:00)`，显示为 **10-18 至 10-24**。这不宣称所有期刊周末都停更：周日当日内容可进入后续日报，周报只为保证窗口一致而排除当天未结束的自然日。

论文默认上限 12，精读位上限 6；不足时减少数量，不承诺填满。周报 arXiv 预算默认 600，分页数随 page_size 自动计算，期刊候选预算同步提高，然后再次执行窗口检查。**这仍是有上限的采集，不是对全世界上周论文的全量最优排序保证。**

兴趣词来自 ARXIV_KEYWORDS、画像 `work.probes.primary`、领域包 journal_terms（缺失时 title_keywords）。重复出现同一个短语、连字符变体或嵌套短语不会冒充多个独立信号。周报普通正文匹配更严格，但明确个人关键词、主要研究对象或标题中的诊断主题可单独入选。匹配理由写入卡片；它是规则筛选，不是经过人工测量的相关率模型。

“世界在变”正常目标 30；有效候选超过目标两倍时，数量可增加到候选的一半，最多 36。先按 50% 给天文，再在物理/化学/历史间轮转，缺额以剩余有效内容补足。领域标签主要由来源配置决定，交叉学科会有重叠；同标题、同 ID、规范化链接作传递去重，仍不是对所有同事件报道的语义聚类。

新闻按配置时区保留近七日（含运行日），排除无有效日期、过期或未来日期。先筛时效再去重，避免旧长文挤掉新报道；链接去掉片段及常见追踪参数后比较。日常已发送过滤仍按条目身份工作，不保证新来源对同一事件的改写永远不再出现。原题、原链接和来源保留，译文数字不受支持时退回原文。非天文新闻不进入天文学脉搏材料池。

## 2. 使用与配置

完整变量表见 [CONFIG.md](CONFIG.md)；`.env.example` 已增加 13 个变量，Actions 同名 Variables 均已映射。当前配置总计 109 个环境变量入口已做静态映射核对。

```powershell
# 周报实际抓取与模型预览：不发信，但可能产生模型费用
python -m arxiv_digest.main --edition weekly --out weekly.html

# 强制日报；auto 为默认，周日在配置时区切换 weekly
python -m arxiv_digest.main --edition daily --out daily.html

# 仅回放既有录制数据，不实时请求模型或 PDF
python scripts/build_preview.py --replay digest.json --out replay.html

# 固定输入评测：build 访问 arXiv，run 调用配置模型，check 只读
python scripts/eval_analysis.py build --count 20
python scripts/eval_analysis.py run --out eval-digest.json
python scripts/eval_analysis.py check --digest eval-digest.json
```

本轮没有执行前两条真实业务命令或真实 eval run。离线回归使用注入客户端验证同样的选择/提交/渲染路径，不向当前真实账号发送测试数据。

周报只有成功投递才写 `state.editions`；预览即使本期已投递仍可再次生成。周报论文不会写入日报 seen；资讯和雷达继续记录实际展示条目。日程和脉搏是辅助上下文，不单独触发空日报。若周报无相关论文但有新资讯，仍可以发送资讯邮件并确认该期投递，不会制造论文卡片。

## 3. R01–R09 修复与验收

“通过”指下面给定的本地验收；不把静态工作流修改当作云端运行成功。

| ID | 修复 | 对应回归 / 验收 | 当前边界 |
|---|---|---|---|
| R01 雷达未发消费 | `PipelineResult.has_deliverable` 包含非空 radar；空白分支标 skipped | `test_radar_only_is_delivered_before_consumption`、`test_radar_only_failed_delivery_does_not_consume` | SMTP 为模拟，投递顺序与 seen 已验证 |
| R02 展示字段无依据数字 | 共用 `evidence.py`；核验 for_you、actions、chain、阅读理由、相关理由、红旗说明、术语释义和单阶段摘要/总览；新闻译文也检查 | `test_all_visible_assessment_paths_reject_unsupported_numeric_claims`、建议预算与单阶段摘要回归 | 允许“建议抽查 3 篇”这类明确任务数量，但后续测量数字仍校验；不能证明单位/语义正确或发现所有中文数词幻觉 |
| R03 合并恢复过期状态 | `merge_states` 合并后执行统一 prune；merge CLI 从成功运行报告接收业务日期/时区 | `test_merge_preserves_valid_editions_and_retention_invariants` 与原并发合并测试 | 过期材料/观察、5000 seen 上限、周报记录及幂等性本地验证 |
| R04 脉搏异常字段逃逸 | headline_evidence 先校验数组类型，整个 coerce 放进 fallback 范围 | 坏类型枚举与 `test_bad_pulse_type_does_not_abort_actual_pipeline` | 结构异常局部降级，不代表真实模型内容可靠 |
| R05 归零趋势遗漏 | 使用两周分类并集，包括 10→0 | `test_decline_to_zero_is_in_sample_trend` | 表示采集到零，不是全量投稿零；缺测/限额不提供完整总体保证 |
| R06 graceful 配置失效 | 其他来源仍可继续；无可投递内容时保留原限流异常 | `test_rate_limit_graceful_true_false_and_preview` | 覆盖 true/false/out，未向真实 arXiv 发起限流测试 |
| R07 诊断归属错配 | 每轮 run_id、独立 runs 文件；只有归属匹配才更新最近入口；记录失败阶段 | 实际两个 main 交错调用；render/state_commit 失败报告回归 | 最新入口属于最近运行，历史 A/B 文件分别保留；本地档案无自动保留期 |
| R08 评测集合不闭环 | build 冻结完整原文；run 按集合全部分析，不抓另一批论文、不套每日配额；清除旧分析防失败冒充成功 | 5 篇超出默认 4 精读配额的完整 run→check；旧集合缺输入时模型调用前拒绝 | 样例为模拟模型；正式 20 篇人工审核集与准确率仍需建立 |
| R09 验证替换待发邮件 | workflow 对 schedule/dispatch 使用投递组，push/PR 使用验证组 | YAML 结构与分组/上传参数回归 | 已按 [GitHub 并发规则](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency) 分开；真实排队场景待验收 |

关键实现见 [editorial.py](../arxiv_digest/editorial.py)、[pipeline.py](../arxiv_digest/pipeline.py)、[main.py](../arxiv_digest/main.py)、[evidence.py](../arxiv_digest/evidence.py)、[state.py](../arxiv_digest/state.py)、[eval_analysis.py](../scripts/eval_analysis.py) 及 [新增验收测试](../tests/test_editions.py)。

## 4. 额外工程改进

- 运行诊断包含 edition、日期窗口、篇数、各领域资讯数量、模型排除数量和 run_id；pipeline/render/SMTP/state_commit 失败都能留下本次记录，异常文本不直接写入失败报告。邮件已接受但状态失败明确标 `state_commit_failed`，重跑前需要检查，仍不保证 exactly-once。
- 最近快照和报告之外，独立档案位于同目录下 `runs/<stem>-<run_id>.json`。预览与发送交错时各自保留正确状态；没有把另一轮快照改成 committed。自定义 DIGEST_JSON_PATH 及独立档案均纳入 Actions artifact。
- 全部 7 处 Actions uses 固定为从官方仓库标签解析的 40 位提交 SHA（保持现有大版本）；新增业务缓存的官方 cache action 也固定。没有声称因此完成供应链审计。
- 本地 extract/fulltext 已知哈希文件按 30 天与 256 MiB 回收，只处理可再生缓存，不删除任意命名文件；每轮开始清理，新增文件仍可能使运行中占用暂时超过阈值。Actions 配置对应缓存恢复，云端效果未实测。
- 本机反馈服务限制 32 个并发连接和 10 秒读取超时，异常释放名额；默认仍只监听本机。测试未进行真实攻击/压测，不把此实现当作完整公网防护。
- 固定评测产物记录模型名、参数、prompt 版本、来源文本/hash，以及画像/领域包文件 hash。冻结输入不等于模型完全确定性，也不等于已有人类 gold labels。

## 5. 本轮实际验证

| 项目 | 最终结果 | 说明 |
|---|---|---|
| Python 回归 | **107/107 通过** | 原 63 + 新增 44；禁网、临时文件、模拟模型/SMTP；真实 pypdf 子进程等原边界继续运行 |
| 自检 | **26/26 通过** | 额外 socket 阻断；翻译扩展时发现并恢复“不要换算”约束后通过 |
| JS 反馈契约 | **10 项通过** | Node 内存 Google 替身，无真实 Google 服务 |
| pip check | 退出 0，无冲突 | 不是漏洞扫描；23 项固定依赖与安装版本一致 |
| 语法和配置 | 48 个 Python 文件、9 份配置 YAML 解析成功 | workflow 也解析成功；109 个变量无缺映射，7 处 Action 使用均固定 SHA |
| 新增公开 RSS | **6/6 HTTP 200，XML 可解析** | 使用 15 秒请求超时和 5 MiB 上限，未抓全文、未调用模型；返回条数仅代表检查时 feed 内容 |

公开源检查时：Phys.org 物理 30 条、化学 30 条；ScienceDaily 物理 60 条、化学 60 条、考古 60 条；Smithsonian 历史 17 条。**这些不是邮件最终选中条数，更不是内容正确性评价。** 来源入口见 [Phys.org RSS](https://phys.org/feeds/)、[ScienceDaily RSS](https://www.sciencedaily.com/newsfeeds.htm)、[Smithsonian RSS](https://www.smithsonianmag.com/rss/)；应用保留来源与原文链接，不下载整篇新闻来填满邮件。

测试过程中出现过翻译提示词约束缺失，以及去重排序改变后暴露的跨源传递去重错误；均修复后重跑，没有删除断言来隐藏失败。最终结果单独记录，不覆盖历史 v0.13 的 63 项/26 项记录。

## 6. 保留限制与下一步

1. 只在 Windows / Python 3.11.9 实测。Linux/macOS、真实 Actions 队列/缓存/权限、SMTP 和 Google 后端均未验收；本地修改也尚未发布到远端定时任务。
2. 本轮只实际访问六个新公开 RSS 和官方 Actions 标签元数据；没有请求真实模型，没有实际 SMTP 投递，没有触发云端工作流。
3. 没有覆盖率、性能基准、相关性准确率、幻觉率或长期阅读效用测量，不以测试数量代替这些指标。
4. 数值检查是保守规则，可能拒绝合法表述，也无法证明物理单位/科学解释、语义相关性与报道可靠性。日报/周报实际效果仍需少量受控论文和人工逐句审核。
5. arXiv/Crossref/RSS 采集和 Zotero 获取有上限；全文仍是有页数/时间限制的节选，没有跨平台内存硬上限。来源停服、URL 变化及媒体转载不可完全消除。
6. 没有做完整依赖漏洞、许可证、历史秘密扫描、mypy/lint 或公网负载测试。版本固定降低漂移，并不证明组件安全。
7. 运行档案本地没有自动清理，生产使用需明确保存范围；个人反馈仍应保持私有。新增周报状态延伸 v2，可读旧状态，但旧版本代码不认识 editions，回滚可能丢掉周报防重记录。

建议下一步先使用 `--edition weekly --out` 对少量真实材料做内容验收，再单独验证 SMTP、Actions 和反馈。真实命令会使用当前配置并可能计费；本轮没有将离线样例包装成真实周报成品或生产验收。
