# Contributing — agentos-cron-reliability

## PR Process
1. Branch from main: feat/drill4-{section}
2. Open PR referencing issue(s) with Closes #N
3. CI must pass
4. At least 1 approving review required
5. Squash-merge into main

## Review Checklist
- [ ] Bash scripts use set -euo pipefail
- [ ] All network operations have timeouts
- [ ] No hardcoded credentials
- [ ] Cron job child_timeout_seconds set explicitly
- [ ] Delivery fallback paths tested
- [ ] Dashboard endpoint returns valid JSON
- [ ] No regression on existing functionality
