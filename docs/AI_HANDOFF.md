# 给下一位 AI 的交接说明

最后更新：2026-10-26，**v0.14.0 工作区**。先读最新 [周报/资讯改动与验收报告](CHANGE_REPORT_2026-10-26_V014.md)、[v0.14 验证记录](VALIDATION_2026-10-26_V014.json) 和 [配置说明](CONFIG.md)。然后按需回看 [v0.13 复审](REVIEW_2026-10-26_POST_FIX.md)、[原审阅基线](REVIEW_2026-10-26.md) 与 [v0.13 改动报告](CHANGE_REPORT_2026-10-26.md)。`PROJECT_REVIEW.md` 是早期历史材料。

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
