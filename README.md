# AutoChangelog

Internal tool for generating release notes from GitHub repository activity.

## v1 Composite Action usage

```yaml
- name: Generate release notes
  uses: XPGAMESLLC/AutoChangelog@v1.0.4
  with:
    organization: XPGAMESLLC
    repo_name: Biomes
    auth_token: ${{ secrets.AUTH_TOKEN }}
```

This creates `body.txt` in the workflow workspace by default.

### Optional Discord webhook notification

```yaml
- name: Generate release notes and send to Discord
  uses: XPGAMESLLC/AutoChangelog@v1.0.4
  with:
    organization: XPGAMESLLC
    repo_name: Biomes
    auth_token: ${{ secrets.AUTH_TOKEN }}
    discord_webhook_url: ${{ secrets.DISCORD_WEBHOOK_URL }}
    discord_role_tag: "<@&123456789012345678>"
    discord_role_tags: "<@&123456789012345678>,<@&987654321098765432>"
```

When `discord_webhook_url` is provided, the action posts changelog text to Discord as:

`<repo_name> [<tag>](<release_page>)`
`
<changelog>`
`
<role_tag(s)>` (optional)

For multiple pings, provide `discord_role_tags` (comma/newline/semicolon separated).

`<version>` is taken from `github.ref_name` (usually your release tag).

Long changelogs are automatically split into multiple Discord messages to stay under webhook limits.

### Use from another repository workflow

You can call this action from another repository (for example `Backrooms`) and use that repository's secrets.

Example snippet for `.github/workflows/build.yml` in the caller repository:

```yaml
name: Build

on:
  push:
    tags:
      - "v*"

jobs:
  changelog:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout caller repository
        uses: actions/checkout@v4

      - name: Generate changelog and notify Discord
        uses: XPGAMESLLC/AutoChangelog@v1.0.4
        with:
          organization: XPGAMESLLC
          repo_name: Backrooms
          auth_token: ${{ secrets.AUTH_TOKEN }}
          discord_webhook_url: ${{ secrets.DISCORD_WEBHOOK_URL }}
          discord_role_tag: "<@&123456789012345678>"
```

### Optional AI summary

```yaml
- name: Generate release notes with AI summary
  uses: XPGAMESLLC/AutoChangelog@v1.0.4
  with:
    organization: XPGAMESLLC
    repo_name: Biomes
    auth_token: ${{ secrets.AUTH_TOKEN }}
    ai_summary: "true"
    ai_api_key: ${{ secrets.AI_API_KEY }}
    # ai_model is optional, defaults to claude-haiku-4-5-20251001
    # ai_model: "claude-haiku-4-5-20251001"
```

## CLI usage

```bash
python -m releasenotes.generator XPGAMESLLC Biomes
```

### CLI with optional AI summary

```bash
# --ai-model is optional, defaults to claude-haiku-4-5-20251001
AI_API_KEY=*** python -m releasenotes.generator XPGAMESLLC Biomes --ai-summary
```

### Local CLI test with Discord webhook

You can run everything locally (generate changelog + send Discord message) by providing org, repo, webhook URL, and optional role tag.

If you have a local `.env` file, `AUTH_TOKEN` and `DISCORD_WEBHOOK_URL` are loaded automatically when running outside GitHub Actions.

PowerShell example:

```powershell
$env:GITHUB_REF_NAME = "v1.2.3"
python -m releasenotes.generator XPGAMESLLC Biomes `
  --file_name body.txt `
  --discord-role-tag "<@&123456789012345678>" `
  --discord-role-tags "<@&987654321098765432>,<@&111122223333444455>"
```

Notes:
- `AUTH_TOKEN` is required to read repo activity.
- `DISCORD_WEBHOOK_URL` can come from `.env` or `--discord-webhook-url`.
- `DISCORD_ROLE_TAG` can come from `.env` or `--discord-role-tag`.
- `DISCORD_ROLE_TAGS` can come from `.env` or `--discord-role-tags` (comma/newline/semicolon separated).
- If `GITHUB_REF_NAME` is not set locally, version falls back to `latest` in the Discord title.

## Legacy CLI

```bash
python -m releasenotes.legacy_generator XPGAMESLLC Biomes
```
