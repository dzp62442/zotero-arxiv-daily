# Upstream Sync + Discord Adaptation Dev Log

## Goal
- Sync fork branch with upstream latest architecture.
- Keep Discord forum output capability.
- Minimize long-term conflict with upstream by integrating into current upstream structure (`src/zotero_arxiv_daily/*`, `config/*`, workflows).

## Current State (2026-03-05)
- Local base is pre-refactor structure (root `main.py`, `paper.py`, `construct_email.py`).
- Local custom feature exists:
  - Discord webhook forum output.
  - `output=email|discord` switch.
  - Empty-paper Discord posting support.

## Development Strategy
1. Merge `upstream/main` into feature branch.
2. Resolve structural conflicts by accepting upstream architecture as base.
3. Re-implement Discord output in upstream execution path:
   - add `src/zotero_arxiv_daily/construct_discord.py`
   - wire output selection in `Executor.run()`
   - add config entries for `executor.output` and `executor.discord_webhook_url`
4. Adapt workflows to pass Discord webhook via environment + `config/custom.yaml`.
5. Update docs with fork delta and config examples.
6. Validate with local checks.

## Risk Points
- Merge conflicts in `README.md` and workflow files.
- Type differences: old `ArxivPaper` vs new `protocol.Paper`.
- Upstream config style migrated to Hydra/OmegaConf.

## Progress
- [x] Create feature branch
- [x] Create development document
- [x] Merge upstream/main
- [x] Integrate Discord module on upstream architecture
- [x] Update workflow/config/docs
- [x] Local verification (syntax-level)

## Implementation Notes
- Resolved merge conflicts in:
  - `.github/workflows/main.yml`
  - `.github/workflows/test.yml`
  - legacy root `main.py` (dropped in favor of upstream `src/` entrypoint)
- Added Discord module at `src/zotero_arxiv_daily/construct_discord.py`.
- Updated `src/zotero_arxiv_daily/executor.py` to route outputs by `executor.output`.
- Added config fields:
  - `executor.output`
  - `executor.discord_webhook_url`
- Updated workflow env pass-through:
  - `OUTPUT_METHOD`
  - `DISCORD_WEBHOOK_URL`
- Verification:
  - `python -m compileall src/zotero_arxiv_daily` passed.
  - `pytest` / `uv` are unavailable in current shell, so full test suite is pending CI/local dev environment.
