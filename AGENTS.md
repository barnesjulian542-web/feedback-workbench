# Working in this project

## Read first

- Read `CODEX_HANDOFF.md` for project context and preservation requirements.
- Read `QUALITY_ITERATION_V2.md` for the prompt changes, all regression rounds, final acceptance review, and known R02 cause inference.
- Read `CONNECTION_STABILITY.md` when working on startup, proxy routing, or disconnections.
- Read `GITHUB_SETUP.md` when working on repository connection or publishing.
- Check current code and actual result files before relying on a historical document.

## Environment and data

- Reuse the existing Windows project and `.venv\Scripts\python.exe`.
- Read only the configuration needed from `.env`; never print credentials or commit the file.
- Preserve the configured business model and `DATA_DIR`. The checked configuration uses `qwen-plus` and `data_real_v1`.
- Preserve original feedback, requirements, SQLite checkpoints, baseline evaluation files, and every regression round. Use new source IDs for experiments.
- Local databases, environment files, backups, virtual environments, and runtime logs stay outside Git according to `.gitignore`.

## Commands

- Start the local app: `.\.venv\Scripts\python.exe start_app.py --port 8502`.
- Quality workflow/UI tests: `.\.venv\Scripts\python.exe -m unittest test_workflow test_app -v`.
- Connection adapter tests: `.\.venv\Scripts\python.exe -m unittest test_connection -v`.
- Paid model regression: `.\.venv\Scripts\python.exe quality_smoke_test.py`.
- Real page verification adds a feedback record; use `verify_clarification_page.py --source-id <new-id>` only when needed. Stop at human review.

## Quality and cost

- Current unified prompt is in `analysis_prompt.py`; `workflow.py` records its revision and hash.
- An explicit single request is not blocked by missing engineering details or final acceptance thresholds. Multiple active requests still require scope clarification.
- Acceptance criteria stay within the primary request and the evidence supplied. Unknown parameters remain pending confirmation; do not invent metrics, standards, features, or implementations.
- Run checks appropriate to the change. Use local tests before paid calls; do not repeat paid regression when no model-affecting change or unresolved failure justifies it.
- Preserve failed runs, raw outputs, and unchanged case expectations. Automatic passing does not replace acceptance review or prove real-user accuracy.
- Do not submit final business approvals on the user's behalf or re-analyze the original baseline records.

## Communication

- Explain changes in Chinese with concrete commands, file locations, measured validation, and remaining limitations.
- Report known quality problems candidly; do not describe character-bigram retrieval as vector RAG or model-selected tools.
