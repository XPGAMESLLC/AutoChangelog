# Conversation Context (AutoChangelog)

_Last updated: 2026-03-18_

## Repository / Branch
- Repo: `AutoChangelog`
- Active feature branch created: `f-composite-action`
- Goal: release **2.0.0** with Composite Action support while preserving legacy behavior.

## High-Level Decisions
1. Keep old behavior available (legacy path).
2. New primary integration path: **GitHub Composite Action**.
3. Internal-only distribution (no public PyPI requirement right now).
4. Optional AI summary feature added (non-breaking).

## Implemented Changes

### Packaging / Versioning
- `pyproject.toml` bumped to `2.0.0`.
- Added legacy script entrypoint:
  - `generate-release-notes-legacy = releasenotes.legacy_generator:main`

### New Composite Action
- `action.yml` added.
- Supports inputs:
  - `organization`, `repo_name`, `file_name`, `auth_token`, `python_version`
  - optional AI: `ai_summary`, `ai_model`, `ai_max_items`, `ai_api_key`, `ai_base_url`

### Generator split
- `src/releasenotes/generator.py` = v2 generator.
- `src/releasenotes/legacy_generator.py` = preserved old implementation.

### AI Summary (optional)
- CLI flags added to v2 generator:
  - `--ai-summary`
  - `--ai-model`
  - `--ai-max-items`
- Env used:
  - `AI_API_KEY`
  - `AI_BASE_URL` (default OpenAI endpoint)
  - `AI_MODEL` (default model for arg default)
- If AI call fails (e.g., 429/network), changelog generation continues without AI section.

### Time-window behavior updates
- Tag-aware changelog windowing implemented.
- Historical runs resolved via `GITHUB_REF_NAME`.
- Compare links now correctly resolve historical tag pairs (example: `0.17.3...0.17.4`).

## Verified Runs / Outputs
- Current release window output generated in `body.txt`.
- AI summary verified working in `body.txt` after retries.
- Historical tag output generated for Biomes:
  - `body-0.17.4.txt`
  - compare header: `0.17.3...0.17.4`

## Known Operational Notes
- Intermittent API `429` observed for AI summary until quota/rate limit cleared; retries later succeeded.
- PowerShell quoting was sometimes unreliable for one-liners; task/script-based runs were more stable.

## Build Workflow Guidance (Biomes)
- To keep using old release artifact flow, pin the AutoChangelog tarball version in Biomes `build.yaml` instead of "latest".

## Cost Estimate Basis (AI Summary)
- Model discussed: `gpt-5.4-nano`
- Reference rates used:
  - Input: `$0.20 / 1M`
  - Output: `$1.25 / 1M`
- Example estimate (20k in / 1k out): `$0.00525` per run.

## Safety / Secrets
- Do not store API keys/tokens in this file.
- Secrets should remain in `.env` locally and GitHub Actions secrets in CI.
