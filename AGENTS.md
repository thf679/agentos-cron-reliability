# agentos-cron-reliability — Project Context

## Architecture
- Hermes cron reliability hardening — fixes production issues in backup + cleanup scripts
- Cron health dashboard widget for Mission Control Overview tab
- No new pip dependencies — pure bash + stdlib Python + HTML/CSS/JS
- Touches production files: backup-hermes.sh, cleanup-logs.sh, server.py, index.html

## Environment
- Host: Termux + proot-distro Ubuntu on Android (aarch64)
- Cron: Hermes cron scheduler (hermes cron), no system crontabs
- 11 cron jobs active, 2 failing (backup timeout + cleanup delivery)

## Coding Standards
- Bash: set -euo pipefail, no unquoted variables, explicit timeouts
- Python: stdlib only, PEP 8, type hints
- JS/CSS: vanilla, design-token-based, no framework

## Key Commands
- Manual backup: bash ~/workspace/.hermes/scripts/backup-hermes.sh
- Manual cleanup: bash ~/workspace/.hermes/agents/_shared/cleanup-logs.sh
- Lint: make lint-bash lint-py lint-js
- Test: make test

## Model Routing
- Architect: deepseek-v4-pro
- Reviewer: deepseek-v4-pro
- Tester: kimi-k2.7-code
- DevOps: kimi-k2.7-code
- Coder (Claude Code): kimi-k2.7-code
