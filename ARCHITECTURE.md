# ARCHITECTURE.md — agentos-cron-reliability

> **SDLC Drill #4 — Phase A: Architecture & Diagnosis**
> Repository: `thf679/agentos-cron-reliability`
> Milestone: [#1 — Drill #4: Cron Job Reliability Fix](https://github.com/thf679/agentos-cron-reliability/milestone/1)

---

## §1 — Root Cause Analysis

### 1.1 Daily Hermes Config Backup (`a4615d2afcb8`)

| Field | Value |
|-------|-------|
| **Job** | Daily Hermes Config Backup |
| **Script** | `~/workspace/.hermes/scripts/backup-hermes.sh` |
| **Last status** | `error` |
| **Last run** | 2026-07-01 04:02 UTC |
| **Evidence** | Script timed out; manual run at 09:01 succeeded (12MB tar.gz) |

**Root cause:** `backup-hermes.sh` line 52 executes `git bundle create ... --all` on the `okf_memory` repository (`.git` size: 256MB, 221 commits). This command takes 90–120 seconds in the proot environment. The Hermes cron scheduler applies a default child timeout of ~60s, which kills the script mid-execution.

```
# Line 52 — the bottleneck:
git bundle create "${TMPDIR}/hermes/okf_memory.bundle" --all
```

**Secondary issue:** Line 62 of `backup-hermes.sh` includes `dashboard.html` in the backup file list. This file was deleted in Drill #2 and no longer exists. The script handles this gracefully (`[ -f ]` check logs a warning) but produces noise in the backup output.

**Fix design:** See §2.

### 1.2 Monthly Log Cleanup (`8b095c51ae48`)

| Field | Value |
|-------|-------|
| **Job** | Monthly Log Cleanup |
| **Script** | `~/workspace/.hermes/agents/_shared/cleanup-logs.sh` |
| **Last status** | `error` |
| **Last run** | 2026-07-01 03:00 UTC |
| **Evidence** | `httpx.ConnectError: All connection attempts failed` |

**Root cause:** The cleanup script (`bash cleanup-logs.sh`) ran correctly — it deleted 0 rows (nothing older than 30 days) with 19 rows retained and no data loss. However, the cron job's delivery target was set to `telegram` only. At 03:00 UTC, the Telegram API was unreachable (`httpx.ConnectError`). Hermes marks the **entire** job as `error` when delivery fails, even if the script itself succeeded.

**Fix design:** See §3.

---

## §2 — Backup Script Hardening Design

### Target file
`scripts/backup-hermes.sh` (repo copy) / `~/workspace/.hermes/scripts/backup-hermes.sh` (production)

### Changes

| # | Change | Line | Rationale |
|---|--------|------|-----------|
| B1 | Replace `git bundle --all` with `git bundle --since=7.days` | 52 | `--all` on 256MB repo takes 90-120s; `--since=7.days` targets only recent commits (~5-15s) |
| B2 | Wrap git bundle with `timeout 120` | 52 | Prevents runaway bundle from blocking cron; 120s is generous for incremental |
| B3 | Remove `dashboard.html` from file list | 62 | File deleted in Drill #2; produces noise |
| B4 | Set `child_timeout_seconds: 180` | cron config | Gives the whole script headroom (tar+gzip+bundle+cp) |
| B5 | Preserve `--all` as weekly manual option | n/a | Mitigate `--since` reachability gap (see §5.1) |

### Before/After

```diff
-  git bundle create "${TMPDIR}/hermes/okf_memory.bundle" --all 2>/dev/null || echo "⚠️ git bundle failed"
+  timeout 120 git bundle create "${TMPDIR}/hermes/okf_memory.bundle" --since=7.days 2>/dev/null || echo "⚠️ git bundle failed"
```

```diff
-  for f in refresh.js index.html dashboard.html tokens.css components.js; do
+  for f in refresh.js index.html tokens.css components.js; do
```

### Expected impact
- Bundle creation: 90-120s → 5-15s (daily incremental)
- Cron timeout: default ~60s → explicit 180s
- Exit code: 0 on success, non-zero only on real failure
- Archive size: unchanged (same tar.gz structure)

---

## §3 — Delivery Resilience Pattern

### Target
Cron job `8b095c51ae48` (Monthly Log Cleanup) + `scripts/cleanup-logs.sh`

### Design

**Pattern: `deliver: local,telegram`**

Instead of a single delivery target, the cron job delivers to a local file first (always succeeds), then attempts Telegram delivery. The local file provides durable evidence even when Telegram is unreachable.

```
┌─────────────┐    ┌──────────┐    ┌──────────┐
│ Script runs │───▶│ Save to  │───▶│ Try      │
│ successfully│    │ local    │    │ Telegram │
└─────────────┘    │ file     │    └────┬─────┘
                   └──────────┘         │
                      ✅ Always      ┌──┴──────────┐
                      succeeds      │ Success?     │
                                    │ Yes → done   │
                                    │ No  → file   │
                                    │ still exists │
                                    └──────────────┘
```

### Changes

| # | Change | Where | Rationale |
|---|--------|-------|-----------|
| C1 | `deliver: local,telegram` | Cron job config | Local always succeeds; Telegram is best-effort |
| C2 | `child_timeout_seconds: 120` | Cron job config | Script runs under 5s; 120s covers DB + delivery |
| C3 | Post-cleanup verification | `cleanup-logs.sh` | Print DB row count before/after; logged by local delivery |

### Local output path
```
~/workspace/.hermes/cron/output/8b095c51ae48.txt
```

### Behavior
- Script runs → output captured → saved to local file ✅
- Telegram attempted → success → both local file + Telegram message exist
- Telegram failed → local file still exists, job marked with delivery warning (not error)

---

## §4 — Cron Health Dashboard Widget

### `/api/cron/health` Endpoint

**Route:** `GET /api/cron/health`
**Source:** `src/server.py` — new function `get_cron_health()` added after `get_cron_jobs()`

**Response spec:**

```json
{
  "total_jobs": 11,
  "healthy": 9,
  "failed": 2,
  "paused": 0,
  "failures": [
    {
      "job_id": "a4615d2afcb8",
      "name": "Daily Hermes Config Backup",
      "last_status": "error",
      "last_run_at": "2026-07-01T04:02:28Z",
      "last_error": "Script timed out (>60s)"
    }
  ],
  "summary": "2 of 11 jobs have errors"
}
```

**Implementation notes:**
- Reads `~/workspace/.hermes/cron/jobs.json` directly
- Atomic read with retry (open → read → parse; retry once on JSON parse error)
- `healthy` = jobs with `last_status: ok` or null
- `failed` = jobs with `last_status: error`
- `paused` = jobs where `enabled: false`
- `last_error` derived from `last_status` — descriptive strings for known failure modes

### Dashboard Changes

**File:** `src/index.html`

Two integration points:

#### 4a. Cron Health Card (new, below Overview stats)

Uses existing `GlassCard` component. Shows:
- Total jobs / Healthy / Failed / Paused counts
- Each failed job as a row with job name, last error, and time ago
- Card turns amber when `failed > 0`

#### 4b. Harness Health Row Update (modify existing)

The existing "Cron Jobs" row in the Harness Health card (around line 621 of `index.html`) is updated:
- Before: shows `{cron_jobs_active} active` (simple count)
- After: shows `{healthy}/{total} healthy` with color: green if all healthy, red if any failed
- The row text indicates active + failed jobs

### Architecture decision: why inside Harness card, not separate endpoint in snapshot?

The cron health data is fetched by the frontend as part of `/api/snapshot` → `cron_health` field (or via a separate fetch to `/api/cron/health`). The `harness_health()` function in `server.py` already reads `jobs.json` to count active jobs. We extend it to include the health breakdown:

```python
# Add to harness_health() return:
"cron_health": get_cron_health_summary()
```

Where `get_cron_health_summary()` returns `{total, healthy, failed, paused, summary}` without the full failure array. The `/api/cron/health` endpoint returns the full detail.

---

## §5 — Known Pitfalls & Mitigations

### 5.1 `git bundle --since=7.days` reachability gap

**Risk:** `--since` filters by commit date, not reachability. An ancient commit referenced by a recent tag won't be included.

**Mitigation:**
- Keep `--all` as a manual weekly option (run `backup-hermes.sh --full` on Sundays)
- Daily incremental captures 99%+ of daily changes
- Full recovery from a weekly `--all` + daily incrementals is possible

### 5.2 `deliver: local,telegram` double-delivery

**Risk:** When Telegram succeeds, both local file and Telegram message exist. This is intentional redundancy.

**Mitigation:**
- Local output directory `~/workspace/.hermes/cron/output/` retains only last 7 outputs per job
- No action needed — redundancy is the design goal

### 5.3 Cron health race condition

**Risk:** `jobs.json` may be mid-write when `/api/cron/health` reads it, causing JSON parse error.

**Mitigation:**
```python
def get_cron_health():
    for attempt in range(2):
        try:
            with open(jobs_path) as f:
                data = json.loads(f.read())
            return build_health_response(data)
        except json.JSONDecodeError:
            if attempt == 0:
                time.sleep(0.1)
            else:
                return {"error": "jobs.json unreadable", "total_jobs": 0, ...}
```

### 5.4 Dashboard shows stale status until next cron run

**Risk:** After fixes are deployed, the dashboard still shows "2 failed" until the next cron run cycle.

**Mitigation:**
- Dashboard card notes "based on last run"
- Health status auto-updates after next run (backup: daily at 04:00, cleanup: monthly on 1st)

---

## §6 — Implementation Plan

### File Map

| File | Changes | Issue |
|------|---------|-------|
| `scripts/backup-hermes.sh` | B1, B2, B3 | #5 |
| `scripts/cleanup-logs.sh` | C3 (verification logging) | #6 |
| `src/server.py` | Add `/api/cron/health` endpoint + `get_cron_health()` | #7 |
| `src/index.html` | Add Cron Health card + update Harness row | #7 |
| `tests/test_backup.sh` | Automated backup test | #8 |
| `tests/test_cleanup.sh` | Automated cleanup test | #8 |
| `tests/test_cron_health.js` | Endpoint test | #8 |
| `ARCHITECTURE.md` | This document | #1 |

### Branch Strategy

| Branch | Changes | PR |
|--------|---------|-----|
| `feat/drill4-backup-fix` | `backup-hermes.sh` | PR #2 |
| `feat/drill4-cleanup-fix` | `cleanup-logs.sh` | PR #3 |
| `feat/drill4-cron-health` | `server.py` + `index.html` | PR #4 |
| `feat/drill4-tests` | `tests/` | PR #5 |

### Verification Checklist

- [ ] `backup-hermes.sh` completes in <120s with exit 0
- [ ] `git bundle --since=7.days` produces valid bundle (`git bundle verify`)
- [ ] No `dashboard.html` warnings in backup output
- [ ] `cleanup-logs.sh` exits 0; callers get output via local file
- [ ] `/api/cron/health` returns correct counts
- [ ] Dashboard Cron Health card loads without errors
- [ ] Harness Health Cron Jobs row shows health status
- [ ] All 11 cron jobs show `last_status: ok` after first run cycle

---

## §7 — Issue Map

| # | Title | Phase | Labels |
|---|-------|-------|--------|
| #1 | Document root cause for backup + cleanup failures | A | `phase-a:architect` `diagnosis` |
| #2 | Design backup script hardening | A | `phase-a:architect` `backup` |
| #3 | Design delivery resilience pattern | A | `phase-a:architect` `delivery` |
| #4 | Design cron health dashboard widget | A | `phase-a:architect` `dashboard` |
| #5 | Implement backup script fix | B | `phase-b:coder` `backup` |
| #6 | Implement log cleanup delivery fix | B | `phase-b:coder` `cleanup` |
| #7 | Implement cron health dashboard | B | `phase-b:coder` `dashboard` |
| #8 | Write test suite | D | `phase-d:tester` `test` |
| #9 | CI/CD + deploy + close milestone | E | `phase-e:devops` `deploy` |

---

*Document version: 1.0 — 2026-07-01*
*Author: Architect (deepseek-v4-pro) — SDLC Drill #4, Phase A*
