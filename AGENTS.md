# Working context

- Start with `docs/AI_HANDOFF.md`, `docs/CHANGE_REPORT_2026-10-26_V014.md` and `docs/VALIDATION_2026-10-26_V014.json`. Older review/change reports are historical evidence.
- The workspace already had uncommitted changes before the repair. Preserve unrelated work; do not reset or overwrite it.
- Keep tests offline. Use `python -B -m arxiv_digest.selfcheck` and `python -B -m unittest discover -s tests -v`.
- Never print credentials, commit `.env`, or put personal feedback votes into the tracked `state.json`.
- `run_pipeline` prepares delivery; only `commit_delivery` after successful sending consumes items. Preview must not consume state.
- Weekly editions intentionally reselect daily-sent papers and use `state.editions` for delivery deduplication. Do not filter weekly papers by daily `seen` or mark them into it; preview/failure must not mark the edition delivered.
- Keep snapshot conversions in `arxiv_digest/contracts.py`; add roundtrip/consumer tests when changing the contract.
- Update the handoff and change report with actual validation results and remaining limitations. Do not label untested external services or scientific content as verified.
