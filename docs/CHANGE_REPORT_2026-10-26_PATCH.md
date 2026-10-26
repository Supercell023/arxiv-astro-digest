# v0.14.1 审查修复报告（2026-10-26）

基线提交：`5e52f83 docs: 补入 v0.14.0 审查报告与未跟踪的测试/脚本（审查基线）`
审查全文：`docs/REVIEW_2026-10-26_V2.md`（83/100，P0=0、P1=0、P2=5、P3=12；其中 P2-2 复查后判为非缺陷）

本轮只修代码与测试，**没有**调用真实模型、SMTP、Zotero/Crossref/arXiv 或云端服务；也未改 `.env`、真实 `state.json`、`digest.json`。

## 已修复

| 编号 | 问题 | 修复 | 回归 |
| --- | --- | --- | --- |
| P2-1 | `--out` 预览会覆盖 `digest.json`，反馈/回放读到预览内容 | `pipeline._write_digest_json(..., update_latest=not preview)`；预览只写 `runs/<run_id>.json`，`update_diagnostics` 按 `delivery=="preview"` 不更新"最近一次"指针 | `DeliveryTests.test_preview_does_not_write_production_digest`、`test_editions.test_preview_interleave_keeps_delivered_snapshot_and_per_run_history` |
| P2-3 | `merge_state.py` 依赖 best-effort 的 `run_report.json`，缺失即崩，state 不提交 | `_business_window()` 捕获 `OSError/ValueError/KeyError/TypeError` 并告警，新增 `--today`/`--timezone`，workflow 传 `DIGEST_TIMEZONE` | `IntegrationBoundaryTests.test_merge_state_falls_back_when_report_missing`（缺失 + 坏 JSON 两种） |
| P2-4 | 精读评估失败只写日志，run_report 无痕迹 | 新增 `analyzed_papers` / `analysis_failures` 与 warning | `DeliveryTests.test_analysis_failures_are_reported` |
| P3-1 | Stage A 缓存键不含 `LLMOptions` | 缓存键拼入 `asdict(options)` 的 JSON | `EvidenceTests.test_extract_cache_key_includes_call_options` |
| P3-2 | 全文缓存键不含 `max_chars` | 键改为 `v2:{url}:{max_pages}:{max_chars}` | 既有缓存用例 |
| P3-3 | `canonical_id` 不剥 `.pdf` | 末尾 `\.pdf$` 去后缀 | `StateTests.test_canonical_arxiv_and_doi_identity` |
| P3-4 | 读坏 `state.json` / `feedback.json` 直接清空 | `storage.quarantine()` 先另存 `.corrupt` 再降级 | `StateTests.test_corrupt_state_file_is_quarantined` |
| P3-5 | `save_state` 只捕 `OSError`，NaN 的 `ValueError` 会击穿 | 捕获 `(OSError, ValueError)` | 既有状态用例 |
| P3-6 | `prune_cache` 对 symlink 环的 `RuntimeError` 会打断管线 | 单文件异常捕获扩为 `(OSError, RuntimeError)` | 既有缓存用例 |
| P3-7 | 死代码/未用导入 | 删除 `_primary_categories` 与 `record_day` 导入；`journals` UA 改用 `__version__`（0.14.1） | `test_package_version_matches_pyproject` |
| P3-8 | `FEEDBACK_URL` 无签名密钥仍生成投票链接 | 启动时告警（签名校验本身已有） | 无独立用例（纯 warning） |

## 未修（有意保留）

- P3-9 `state.json` 仍在版本控制中：定时投递需要跨运行持久化去重；已确认其中只有 seen/daily/observations 等计数，**不含个人反馈票**。
- P3-10 单篇分析失败只 warning：属既有降级策略，本轮只补计数与整批失败告警。
- P3-11 `pyproject.toml` 的 `mypy strict` 未接 CI：已加注释指向审查报告，接 lint 需另开依赖与门禁。
- P3-12 周报显式模式的跨午夜重复投递：cron 固定 05:30 UTC 不触发，属手动触发的已知边界，暂不改 `edition.key` 语义。

## 本轮验证（本地实测）

- `.venv\Scripts\python.exe -B -m unittest discover -s tests -q` → **Ran 113 tests / OK**（原 107 + 新增 6）。
- `.venv\Scripts\python.exe -B -m arxiv_digest.selfcheck` → **26 项全部通过**。
- `.venv\Scripts\python.exe -B -m pip check` → `No broken requirements found.`
- `node` 不在本会话 PATH，`tests/check_google_feedback.cjs` 的 10 项 JS 契约**本轮未复现**（沿用 v0.14 验证记录，不重复声明通过）。

## 剩余限制

- 没有真实投递、云端 Actions、Apps Script 部署或真实数据端到端验证；`state_commit_failed` 仍可能重复投递，排查后再重跑。
- 模型输出质量、科学结论与人工相关性标注仍未验收；全文仍是节选，`basis` 不能证明论文语义。
- 新增用例只覆盖上述修复点，不改变 v0.14 已验证的其他边界。
