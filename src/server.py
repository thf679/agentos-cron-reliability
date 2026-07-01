#!/usr/bin/env python3
"""
AgentOS Mission Control — read-only dashboard server.
Stdlib only. Bind 0.0.0.0:51763 (all interfaces for Tailscale access).
Supports both /api/snapshot (for existing HTML) and /api/summary (for v2 dashboard).
"""
import http.server, json, os, sqlite3, sys, time, threading, queue
from datetime import datetime, timezone, timedelta
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", os.path.expanduser("~/.hermes")))
BASE_DIR = Path(__file__).parent
CONTENT_DIR = Path(os.path.expanduser("~/workspace/.hermes/content"))

# ── Timestamp normalization ─────────────────────────────────
def norm_ts(v):
    if v is None: return None
    if isinstance(v, str):
        try: return datetime.fromisoformat(v).strftime("%Y-%m-%dT%H:%M:%SZ")
        except: return v
    if isinstance(v, (int, float)):
        if v > 1e9:
            return datetime.fromtimestamp(v, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return f"t+{int(v)}s"
    return str(v)

# ── SQLite read-only ────────────────────────────────────────
def ro(db):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, check_same_thread=False)
    conn.execute("PRAGMA query_only=1")
    conn.row_factory = sqlite3.Row
    return conn

def q(cursor, sql, params=()):
    cursor.execute(sql, params)
    return [dict(r) for r in cursor.fetchall()]

# ── SSE event bus ───────────────────────────────────────────
sse_clients = []
sse_lock = threading.Lock()

def sse_broadcast(event, data):
    with sse_lock:
        dead = []
        for qq in sse_clients:
            try: qq.put_nowait((event, data))
            except: dead.append(qq)
        for d in dead: sse_clients.remove(d)

BOARD_DB = BASE_DIR / "board.db"

def init_board_db():
    """Create board.db if missing — read-write task board for the Tasks tab."""
    import uuid
    conn = sqlite3.connect(str(BOARD_DB))
    conn.execute("""CREATE TABLE IF NOT EXISTS tasks (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        priority TEXT DEFAULT 'medium',
        notes TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT
    )""")
    # Seed with empty board — first-run creates the table only
    conn.commit()
    conn.close()

def board_list():
    conn = sqlite3.connect(str(BOARD_DB))
    conn.row_factory = sqlite3.Row
    rows = []
    for r in conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall():
        d = dict(zip(r.keys(), r))
        # Normalize: "completed" → "done" for frontend column matching
        if d.get("status") == "completed":
            d["status"] = "done"
        rows.append(d)
    conn.close()
    return rows

def board_create(data):
    import uuid
    conn = sqlite3.connect(str(BOARD_DB))
    conn.row_factory = sqlite3.Row
    task_id = str(uuid.uuid4())[:8]
    title = data.get("title", "Untitled")
    priority = data.get("priority", "medium")
    notes = data.get("notes", "")
    created_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO tasks (id, title, status, priority, notes, created_at) VALUES (?, ?, 'pending', ?, ?, ?)",
        (task_id, title, priority, notes, created_at)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    task = dict(zip(row.keys(), row)) if row else {}
    conn.close()
    return task

def board_update(task_id, data):
    conn = sqlite3.connect(str(BOARD_DB))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if not row:
        conn.close()
        return None
    status = data.get("status", row["status"])
    priority = data.get("priority", row["priority"])
    notes = data.get("notes", row["notes"])
    updated_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE tasks SET status = ?, priority = ?, notes = ?, updated_at = ? WHERE id = ?",
        (status, priority, notes, updated_at, task_id)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    task = dict(zip(row.keys(), row)) if row else {}
    conn.close()
    return task

def board_delete(task_id):
    conn = sqlite3.connect(str(BOARD_DB))
    conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
# ── SDLC Dashboard ───────────────────────────────────────────
def get_sdlc_projects():
    """Per-repo SDLC health: git status, context files, last activity, CI state."""
    import subprocess
    index = HERMES_HOME / "projects" / "index.yaml"
    if not index.exists():
        return {"projects": [], "total": 0, "active": 0}

    try:
        import yaml
        with open(index) as f:
            data = yaml.safe_load(f)
    except Exception:
        return {"projects": [], "total": 0, "active": 0}

    projects_data = data.get("projects", {})
    projects = []
    active_count = 0

    for repo_id, meta in projects_data.items():
        repo_path = HERMES_HOME / "projects" / repo_id
        proj = {
            "id": repo_id,
            "name": meta.get("name", repo_id),
            "description": meta.get("description", ""),
            "status": meta.get("status", "unknown"),
            "tech_stack": meta.get("tech_stack", []),
            "agents": {},
        }

        if meta.get("status") == "active":
            active_count += 1

        # Git status
        if (repo_path / ".git").is_dir():
            try:
                r = subprocess.run(
                    ["git", "-C", str(repo_path), "log", "-1", "--format=%H|%s|%an|%aI"],
                    capture_output=True, text=True, timeout=5
                )
                if r.returncode == 0 and r.stdout.strip():
                    parts = r.stdout.strip().split("|", 3)
                    proj["git"] = {
                        "head": parts[0][:8] if len(parts) > 0 else "?",
                        "message": parts[1][:80] if len(parts) > 1 else "",
                        "author": parts[2] if len(parts) > 2 else "",
                        "committed_at": parts[3] if len(parts) > 3 else "",
                    }
                # Branch
                r2 = subprocess.run(
                    ["git", "-C", str(repo_path), "branch", "--show-current"],
                    capture_output=True, text=True, timeout=5
                )
                if r2.returncode == 0:
                    proj["git"]["branch"] = r2.stdout.strip()
                # Dirty check
                r3 = subprocess.run(
                    ["git", "-C", str(repo_path), "status", "--porcelain"],
                    capture_output=True, text=True, timeout=5
                )
                proj["git"]["dirty"] = bool(r3.stdout.strip())
            except Exception:
                proj["git"] = {"error": "git command failed"}

        # Context files present
        ctx = {}
        for fname in ["AGENTS.md", "ARCHITECTURE.md", "CONTRIBUTING.md"]:
            ctx[fname] = (repo_path / fname).is_file()
        proj["context_files"] = ctx

        # CI state
        ci_state = HERMES_HOME / "ci-state" / f"{repo_id}.json"
        if ci_state.exists():
            try:
                with open(ci_state) as f:
                    cs = json.load(f)
                proj["ci"] = {
                    "last_head": cs.get("last_head", "")[:8],
                    "last_poll": cs.get("last_poll", ""),
                    "branches": len(cs.get("last_branches", [])),
                    "tags": len(cs.get("last_tags", [])),
                }
            except Exception:
                pass

        # Last agent actions from registry
        la = meta.get("last_actions", [])
        if la:
            proj["last_action"] = la[-1]

        projects.append(proj)

    return {"projects": projects, "total": len(projects), "active": active_count}


def get_tool_tracing(limit=20):
    """H4 Observability — recent tool calls with duration, hash, and agent."""
    db = HERMES_HOME / "agent-logs.db"
    if not db.exists():
        return {"entries": [], "slowest": None}

    c = ro(str(db)).cursor()
    rows = q(c, """SELECT id, agent_name, task_description, tool_name,
        duration_ms, tool_hash, created_at, status
        FROM agent_logs WHERE tool_name IS NOT NULL AND tool_name != ''
        ORDER BY created_at DESC LIMIT ?""", (limit,))
    c.connection.close()

    entries = []
    slowest_ms = 0
    slowest_entry = None
    for r in rows:
        if r.get("created_at"):
            r["created_at"] = norm_ts(r["created_at"])
        entries.append(r)
        dur = r.get("duration_ms")
        if isinstance(dur, (int, float)) and dur > slowest_ms:
            slowest_ms = int(dur)
            slowest_entry = r

    # Aggregate: per-agent avg duration, per-tool count
    by_agent = {}
    by_tool = {}
    for e in entries:
        dur = e.get("duration_ms", 0)
        dur_int = int(dur) if isinstance(dur, (int, float)) else 0
        agent = e.get("agent_name", "unknown")
        an = {}; an.setdefault("total", 0); an.setdefault("sum_ms", 0)
        # simpler:
        if agent not in by_agent:
            by_agent[agent] = {"total": 0, "sum_ms": 0}
        by_agent[agent]["total"] += 1
        by_agent[agent]["sum_ms"] += dur_int

        tool = e.get("tool_name", "unknown")
        if tool not in by_tool:
            by_tool[tool] = 0
        by_tool[tool] += 1

    return {
        "entries": entries,
        "count": len(entries),
        "slowest": slowest_entry,
        "by_agent": by_agent,
        "by_tool": by_tool,
    }


def get_health_score():
    """H4 Observability — daily health score: agent activity, error rate, tool stats."""
    db = HERMES_HOME / "agent-logs.db"
    if not db.exists():
        return {"score": 0, "status": "no_data"}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    c = ro(str(db)).cursor()

    # Total tasks today
    total_today = c.execute(
        "SELECT COUNT(*) FROM agent_logs WHERE created_at LIKE ?", (f"{today}%",)
    ).fetchone()[0]

    # Failed tasks today
    failed_today = c.execute(
        "SELECT COUNT(*) FROM agent_logs WHERE created_at LIKE ? AND status='failed'",
        (f"{today}%",)
    ).fetchone()[0]

    # Active agents today
    active_agents = c.execute(
        "SELECT COUNT(DISTINCT agent_name) FROM agent_logs WHERE created_at LIKE ?",
        (f"{today}%",)
    ).fetchone()[0]

    # Agent activity breakdown
    agent_rows = c.execute(
        """SELECT agent_name, COUNT(*) as cnt, 
           SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) as ok,
           SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as fail
           FROM agent_logs WHERE created_at LIKE ? 
           GROUP BY agent_name""",
        (f"{today}%",)
    ).fetchall()

    # Tool stats today
    tool_rows = c.execute(
        """SELECT tool_name, COUNT(*) as cnt, AVG(duration_ms) as avg_ms,
           MAX(duration_ms) as max_ms
           FROM agent_logs WHERE created_at LIKE ? AND tool_name IS NOT NULL AND tool_name != ''
           GROUP BY tool_name""",
        (f"{today}%",)
    ).fetchall()

    c.connection.close()

    # Composite score: 0-100
    # +40pts: any agent activity
    # +30pts: error rate < 20% (or 0 tasks = 30)
    # +20pts: at least 3 agents active
    # +10pts: tool tracing active
    score = 0
    score += min(40, total_today * 5)  # 5 pts per task, max 40
    if total_today == 0:
        error_rate = 0
        score += 30  # no tasks yet = neutral
    else:
        error_rate = failed_today / total_today
        score += 30 if error_rate < 0.2 else max(0, int(30 * (1 - error_rate)))
    score += min(20, active_agents * 7)  # 7 pts per agent, max 20
    score += 10 if tool_rows else 0

    agents_list = []
    for r in agent_rows:
        agents_list.append({
            "name": r[0], "total": r[1], "ok": r[2], "fail": r[3]
        })

    tools_list = []
    for r in tool_rows:
        tools_list.append({
            "name": r[0], "count": r[1],
            "avg_ms": round(r[2], 1) if r[2] else 0,
            "max_ms": r[3]
        })

    return {
        "score": score,
        "status": "healthy" if score >= 70 else "degraded" if score >= 40 else "critical",
        "total_tasks_today": total_today,
        "failed_tasks_today": failed_today,
        "error_rate": round(error_rate, 3),
        "active_agents": active_agents,
        "agents": agents_list,
        "tool_calls_today": len(tool_rows),
        "tools": tools_list,
        "date": today,
    }


def get_sdlc_activity():
    """SDLC agent activity feed from agent-logs.db, enriched with repo/PR fields."""
    db = HERMES_HOME / "agent-logs.db"
    if not db.exists():
        return {"total": 0, "agents": {}, "entries": [], "review_backlog": []}

    sdlc_agents = ("architect", "reviewer", "tester", "devops")
    c = ro(str(db)).cursor()
    rows = q(c, """SELECT id, agent_name, task_description, model_used, status,
        created_at, repo, branch, pr_number, commit_sha
        FROM agent_logs WHERE agent_name IN ('architect','reviewer','tester','devops')
        ORDER BY created_at DESC LIMIT 50""")
    c.connection.close()

    for r in rows:
        if r.get("created_at"):
            r["created_at"] = norm_ts(r["created_at"])

    per_agent = {}
    entries = []
    review_backlog = []
    for e in rows:
        a = e["agent_name"]
        if a not in per_agent:
            per_agent[a] = {"total": 0, "completed": 0, "failed": 0}
        per_agent[a]["total"] += 1
        if e["status"] == "completed":
            per_agent[a]["completed"] += 1
        elif e["status"] in ("failed", "error"):
            per_agent[a]["failed"] += 1
        entries.append(e)

        # Flag review tasks without completion
        if a == "reviewer" and e["status"] not in ("completed", "failed", "error"):
            review_backlog.append(e)

    return {
        "total": len(rows),
        "agents": per_agent,
        "entries": entries,
        "review_backlog": review_backlog,
    }


def get_sdlc_content():
    """SDLC content library: architecture docs, test reports, review history by repo."""
    sdlc_dir = CONTENT_DIR / "sdlc"
    result = {"repos": {}, "total_files": 0}

    if not sdlc_dir.is_dir():
        # Check for SDLC content in standard content dirs
        # Look for architecture/test/review files in any agent dir
        for agent_dir in sorted(CONTENT_DIR.iterdir()):
            if not agent_dir.is_dir():
                continue
            agent_name = agent_dir.name
            files = []
            for f in sorted(agent_dir.glob("*.md")):
                stat = f.stat()
                try:
                    first_line = f.read_text().split("\n")[0].strip()
                    title = first_line.lstrip("#").strip() if first_line.startswith("#") else f.stem
                except Exception:
                    title = f.stem
                files.append({
                    "filename": f.name,
                    "title": title,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    "agent": agent_name,
                })
            if files:
                result["repos"][agent_name] = files
                result["total_files"] += len(files)
        return result

    # SDLC content dir exists — scan by repo subdirs
    for repo_dir in sorted(sdlc_dir.iterdir()):
        if not repo_dir.is_dir():
            continue
        repo_name = repo_dir.name
        cats = {}
        for cat in ["architecture", "tests", "reviews", "deployments"]:
            cat_dir = repo_dir / cat
            if cat_dir.is_dir():
                files = []
                for f in sorted(cat_dir.glob("*.md")):
                    stat = f.stat()
                    try:
                        first_line = f.read_text().split("\n")[0].strip()
                        title = first_line.lstrip("#").strip() if first_line.startswith("#") else f.stem
                    except Exception:
                        title = f.stem
                    files.append({
                        "filename": f.name,
                        "title": title,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                    })
                if files:
                    cats[cat] = files
                    result["total_files"] += len(files)
        if cats:
            result["repos"][repo_name] = cats
    return result


def get_sdlc_review_queue():
    """Pending review backlog from agent-logs.db and project registry."""
    activity = get_sdlc_activity()
    reviews = activity.get("review_backlog", [])

    # Also check for repos without recent reviews
    projects_data = get_sdlc_projects()
    stale_repos = []
    for proj in projects_data.get("projects", []):
        la = proj.get("last_action", {})
        if la.get("agent") != "reviewer":
            stale_repos.append({
                "repo": proj["id"],
                "name": proj["name"],
                "last_review": None,
                "note": "No review activity found",
            })

    return {
        "pending_reviews": reviews,
        "stale_repos": stale_repos,
        "total_pending": len(reviews),
        "total_stale": len(stale_repos),
    }


def content_list():
    """Return all content files grouped by agent, with metadata."""
    agents = ["orchestrator", "analyst", "writer", "marketer", "coder"]
    result = {}
    for agent in agents:
        agent_dir = CONTENT_DIR / agent
        files = []
        if agent_dir.is_dir():
            for f in sorted(agent_dir.glob("*.md")):
                stat = f.stat()
                # Read first line as title (strip # prefix)
                try:
                    first_line = f.read_text().split("\n")[0].strip()
                    title = first_line.lstrip("#").strip() if first_line.startswith("#") else first_line[:80]
                except Exception:
                    title = f.stem
                files.append({
                    "filename": f.name,
                    "title": title,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                })
        result[agent] = files
    return result

def content_get(agent, filename):
    """Retrieve a specific content file. Returns {content, agent, filename}."""
    filepath = (CONTENT_DIR / agent / filename).resolve()
    if not str(filepath).startswith(str(CONTENT_DIR.resolve())):
        return None  # path traversal guard
    if not filepath.is_file():
        return None
    try:
        content = filepath.read_text()
        stat = filepath.stat()
        return {
            "agent": agent,
            "filename": filename,
            "content": content,
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        }
    except Exception:
        return None

def content_save(agent, filename, content):
    """Save content to a file. Creates agent dir if missing."""
    agent_dir = CONTENT_DIR / agent
    if agent not in ("orchestrator", "analyst", "writer", "marketer", "coder"):
        return False
    filepath = (agent_dir / filename).resolve()
    if not str(filepath).startswith(str(CONTENT_DIR.resolve())):
        return False
    try:
        agent_dir.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content)
        return True
    except Exception:
        return False

def get_gateway():
    p = HERMES_HOME / "gateway_state.json"
    if not p.exists(): return {}
    d = json.loads(p.read_text())
    for k in ("start_time","updated_at"):
        if k in d: d[k] = norm_ts(d[k])
    if "platforms" in d:
        for plat in d["platforms"].values():
            if "updated_at" in plat: plat["updated_at"] = norm_ts(plat["updated_at"])
    return d

def get_sessions():
    db = HERMES_HOME / "state.db"
    if not db.exists(): return []
    c = ro(str(db)).cursor()
    rows = q(c, """SELECT id,source,model,started_at,ended_at,end_reason,
        message_count,tool_call_count,input_tokens,output_tokens,
        cache_read_tokens,estimated_cost_usd,title FROM sessions
        ORDER BY started_at DESC LIMIT 20""")
    c.connection.close()
    for r in rows:
        for k in ("started_at","ended_at"):
            if r.get(k): r[k] = norm_ts(r[k])
    return rows

def get_messages(limit=30):
    db = HERMES_HOME / "state.db"
    if not db.exists(): return []
    c = ro(str(db)).cursor()
    rows = q(c, """SELECT id,session_id,role,coalesce(substr(content,1,200),'') as preview,
        tool_name,timestamp,token_count,finish_reason
        FROM messages WHERE active=1 ORDER BY id DESC LIMIT ?""", (limit,))
    c.connection.close()
    for r in rows:
        if r.get("timestamp"): r["timestamp"] = norm_ts(r["timestamp"])
    return rows

def get_kanban_tasks(limit=50):
    db = HERMES_HOME / "kanban.db"
    if not db.exists(): return []
    c = ro(str(db)).cursor()
    rows = q(c, """SELECT id,title,status,priority,assignee,created_by,
        created_at,started_at,completed_at,consecutive_failures,goal_mode
        FROM tasks ORDER BY created_at DESC LIMIT ?""", (limit,))
    c.connection.close()
    for t in rows:
        for k in ("created_at","started_at","completed_at"):
            if t.get(k): t[k] = norm_ts(t[k])
    return rows

def get_kanban_stats():
    db = HERMES_HOME / "kanban.db"
    if not db.exists(): return {"total": 0, "stats": {}, "tasks": []}
    c = ro(str(db)).cursor()
    stats = {r["status"]: r["n"] for r in q(c, "SELECT status,count(*) n FROM tasks GROUP BY status")}
    total = q(c, "SELECT count(*) n FROM tasks")
    c.connection.close()
    return {"total": total[0]["n"] if total else 0, "stats": stats, "tasks": get_kanban_tasks()}

def get_logs():
    db = HERMES_HOME / "agent-logs.db"
    if not db.exists(): return []
    c = ro(str(db)).cursor()
    rows = q(c, "SELECT id,agent_name,task_description,model_used,status,created_at FROM agent_logs ORDER BY created_at DESC LIMIT 50")
    c.connection.close()
    for r in rows:
        if r.get("created_at"): r["created_at"] = norm_ts(r["created_at"])
    return rows

def get_logs_activity():
    logs = get_logs()
    per_agent = {}
    entries = []
    for e in logs:
        a = e["agent_name"]
        if a not in per_agent:
            per_agent[a] = {"total": 0, "completed": 0, "failed": 0}
        per_agent[a]["total"] += 1
        if e["status"] == "completed":
            per_agent[a]["completed"] += 1
        elif e["status"] in ("failed", "error"):
            per_agent[a]["failed"] += 1
        # Build feed entry for frontend activity stream
        entries.append({
            "agent_name": e["agent_name"],
            "task_description": e["task_description"],
            "status": e["status"],
            "created_at": e["created_at"],
            "model": e.get("model_used"),
        })
    return {"total": len(logs), "per_agent": per_agent, "entries": entries}

def get_agents():
    """Per-agent stats: last task, 7-day daily counts, success rate, model info.
    Used by the Agents tab."""
    logs = get_logs()
    agent_keys = ["orchestrator", "analyst", "writer", "marketer", "coder"]
    agent_info = {
        "orchestrator": {"name": "Orchestrator", "role": "System-wide coordinator\nTop-level control layer", "platform": "Telegram"},
        "analyst":      {"name": "Analyst",      "role": "Deep research specialist\n5+ sources, trend intelligence", "platform": "Discord"},
        "writer":       {"name": "Writer",       "role": "SEO content + blog writer\nBilingual EN/DE, 800+ words", "platform": "Discord"},
        "marketer":     {"name": "Marketer",     "role": "Marketing strategist\n30/60/90 plans, monetization", "platform": "Discord"},
        "coder":        {"name": "Coder",        "role": "Full-stack developer\nReact, APIs, automation", "platform": "Discord"},
    }
    now = datetime.now(timezone.utc)
    agents = {}
    total_logs = len(logs)
    for key in agent_keys:
        info = agent_info[key]
        agent_logs = [e for e in logs if e["agent_name"] == key]
        completed = sum(1 for e in agent_logs if e["status"] == "completed")
        failed = sum(1 for e in agent_logs if e["status"] in ("failed", "error"))
        total = len(agent_logs)
        success_pct = round(completed / total * 100, 1) if total > 0 else 0
        last = agent_logs[0] if agent_logs else None

        # 7-day daily counts
        from collections import defaultdict
        daily = defaultdict(int)
        for e in agent_logs:
            try:
                ts = datetime.fromisoformat(e["created_at"].replace("Z", "+00:00"))
                day_key = ts.strftime("%Y-%m-%d")
                daily[day_key] += 1
            except Exception:
                pass
        # Fill last 7 days
        day_list = []
        for i in range(6, -1, -1):
            d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
            day_list.append(daily.get(d, 0))

        # Status classification
        status = "dormant"
        if total > 0:
            status = "active" if any(
                e["status"] == "completed" for e in agent_logs[:3]
            ) else "idle"

        agents[key] = {
            "id": key,
            "name": info["name"],
            "role": info["role"],
            "platform": info["platform"],
            "total": total,
            "completed": completed,
            "failed": failed,
            "success_pct": success_pct,
            "status": status,
            "last_task": last["task_description"] if last else None,
            "last_at": last["created_at"] if last else None,
            "model": last.get("model_used") if last and "model_used" in last else None,
            "daily": day_list,
            "share": round(total / total_logs * 100, 1) if total_logs > 0 else 0,
        }
    return agents

def get_activity_by_day():
    """7-day daily activity counts across all agents for the ThinBar chart."""
    logs = get_logs()
    from collections import defaultdict
    daily = defaultdict(int)
    for e in logs:
        try:
            ts = datetime.fromisoformat(e["created_at"].replace("Z", "+00:00"))
            daily[ts.strftime("%Y-%m-%d")] += 1
        except Exception:
            pass
    now = datetime.now(timezone.utc)
    result = []
    for i in range(6, -1, -1):
        d = (now - timedelta(days=i)).strftime("%Y-%m-%d")
        result.append(daily.get(d, 0))
    return result

def get_sessions_summary():
    rows = get_sessions()
    msg_total = sum(r.get("message_count", 0) or 0 for r in rows)
    tok = {"input": 0, "output": 0, "cache": 0}
    for r in rows:
        tok["input"] += r.get("input_tokens", 0) or 0
        tok["output"] += r.get("output_tokens", 0) or 0
        tok["cache"] += r.get("cache_read_tokens", 0) or 0
    return {"session_count": len(rows), "message_count": msg_total, "token_totals": tok}

def get_cron_jobs():
    """Read cron jobs from ~/.hermes/cron/jobs.json."""
    p = HERMES_HOME / "cron" / "jobs.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
        raw = data.get("jobs", [])
    except Exception:
        return []

    def describe_schedule(sched):
        """Convert cron/interval schedules to plain English."""
        if isinstance(sched, dict):
            kind = sched.get("kind", "")
            if kind == "interval":
                mins = sched.get("minutes", 1)
                if mins < 60:
                    return f"Runs every {mins} minute{'s' if mins != 1 else ''}"
                hours = mins // 60
                mins_rem = mins % 60
                if mins_rem == 0:
                    return f"Runs every {hours} hour{'s' if hours != 1 else ''}"
                return f"Runs every {hours}h {mins_rem}m"
            elif kind == "cron":
                expr = sched.get("expr", "")
                # Simple parsing of common patterns
                parts = expr.split()
                if len(parts) == 5:
                    minute, hour, dom, month, dow = parts
                    if hour.isdigit() and minute.isdigit():
                        h = int(hour)
                        m = int(minute)
                        time_str = f"{h:02d}:{m:02d}"
                        if dom == "1" and month == "*":
                            return f"Runs monthly on the 1st at {time_str}"
                        if dom == "*" and month == "*" and dow == "*":
                            return f"Runs daily at {time_str}"
                        if dow != "*":
                            day_names = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"]
                            try:
                                day = day_names[int(dow)]
                                return f"Runs on {day}s at {time_str}"
                            except:
                                pass
                return f"Cron: {expr}"
        return sched.get("display", str(sched)) if isinstance(sched, dict) else str(sched)

    jobs = []
    for r in raw:
        name = r.get("name", "Unnamed")
        no_agent = r.get("no_agent", False)
        # System = script-only, no LLM. Hermes = LLM-driven or mixed.
        label = "system" if no_agent else "hermes"

        jobs.append({
            "id": r.get("id"),
            "name": name,
            "schedule_display": r.get("schedule_display") or describe_schedule(r.get("schedule", {})),
            "schedule_desc": describe_schedule(r.get("schedule", {})),
            "next_run_at": r.get("next_run_at"),
            "last_run_at": r.get("last_run_at"),
            "last_status": r.get("last_status"),
            "state": r.get("state"),
            "enabled": r.get("enabled", True),
            "label": label,
        })
    return jobs

def get_cron_health():
    """Return cron health summary: total/failed/healthy counts + failure details.
    Reads ~/.hermes/cron/jobs.json with atomic retry on JSON parse error.
    """
    p = HERMES_HOME / "cron" / "jobs.json"
    if not p.exists():
        return {
            "total_jobs": 0, "healthy": 0, "failed": 0, "paused": 0,
            "failures": [], "summary": "No cron jobs configured"
        }
    raw = []
    for attempt in range(2):
        try:
            data = json.loads(p.read_text())
            raw = data.get("jobs", [])
            break
        except json.JSONDecodeError:
            if attempt == 0:
                time.sleep(0.1)
            else:
                return {
                    "total_jobs": 0, "healthy": 0, "failed": 0, "paused": 0,
                    "failures": [], "summary": "jobs.json unreadable",
                    "error": "JSON parse error"
                }

    healthy = 0
    failed_jobs = []
    failures = []
    paused = 0

    for r in raw:
        enabled = r.get("enabled", True)
        last_status = r.get("last_status")
        if not enabled:
            paused += 1
            continue
        if last_status == "error":
            failed_jobs.append(r)
            # Build descriptive error from available fields
            last_error = r.get("last_error", "")
            if not last_error:
                # Derive error description
                last_message = r.get("last_message", "")
                if "timeout" in str(r.get("last_output", "")).lower() or "timeout" in str(last_message).lower():
                    last_error = "Script timed out"
                elif "ConnectError" in str(r.get("last_output", "")) or "ConnectError" in str(last_message):
                    last_error = "Delivery failed: httpx.ConnectError"
                else:
                    last_error = "Unknown error"
            failures.append({
                "job_id": r.get("id"),
                "name": r.get("name", "Unnamed"),
                "last_status": last_status,
                "last_run_at": r.get("last_run_at"),
                "last_error": last_error,
            })
        else:
            healthy += 1

    return {
        "total_jobs": len(raw),
        "healthy": healthy,
        "failed": len(failures),
        "paused": paused,
        "failures": failures,
        "summary": f"{len(failures)} of {len(raw)} jobs have errors" if failures else "All jobs healthy",
    }


def get_cpu_pct():
    """Get CPU percentage from `ps -eo pcpu=` — per-process stats are live in proot
    even though aggregate /proc/stat is frozen. Clamped to 0–100."""
    try:
        import subprocess
        r = subprocess.run(
            ["ps", "-eo", "pcpu="],
            capture_output=True, text=True, timeout=3
        )
        if r.returncode == 0:
            total = sum(float(x) for x in r.stdout.strip().split() if x)
            return round(min(total, 100.0), 1)   # cap multi-core overshoot
    except Exception:
        pass
    return None

def get_system():
    """Basic system stats from /proc (Linux). Returns cpu_pct, ram_pct, disk_pct
    as 0–100 numeric percentages for the Overview UI bar widths."""
    info = {}
    info["cpu_pct"] = get_cpu_pct()
    # Load averages
    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
            info["load_1m"] = float(parts[0])
            info["load_5m"] = float(parts[1])
    except: pass
    # Memory — compute ram_pct as 0–100 numeric
    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                if ":" in line:
                    k, v = line.split(":", 1)
                    mem[k.strip()] = int(v.strip().split()[0])
            total = mem.get("MemTotal", 0)
            avail = mem.get("MemAvailable", 0)
            if not avail:
                avail = mem.get("MemFree", 0) + mem.get("Buffers", 0) + mem.get("Cached", 0)
            info["ram_total"] = f"{total // 1024} MB"
            info["ram_used"] = f"{(total - avail) // 1024} MB"
            info["ram_pct"] = round(((total - avail) / total) * 100, 1) if total else 0
    except: pass
    # Disk — compute disk_pct as 0–100 numeric
    try:
        s = os.statvfs("/")
        info["disk_total"] = f"{s.f_frsize * s.f_blocks // (1024**3)} GB"
        info["disk_used"] = f"{s.f_frsize * (s.f_blocks - s.f_bavail) // (1024**3)} GB"
        total_bytes = s.f_frsize * s.f_blocks
        avail_bytes = s.f_frsize * s.f_bavail
        info["disk_pct"] = round(((total_bytes - avail_bytes) / total_bytes) * 100, 1) if total_bytes else 0
    except: pass
    return info

def get_cron_health():
    """Return cron health summary: total/failed/healthy counts + failure details.
    Reads ~/.hermes/cron/jobs.json with atomic retry on JSON parse error.
    """
    p = HERMES_HOME / "cron" / "jobs.json"
    if not p.exists():
        return {
            "total_jobs": 0, "healthy": 0, "failed": 0, "paused": 0,
            "failures": [], "summary": "No cron jobs configured"
        }
    raw = []
    for attempt in range(2):
        try:
            data = json.loads(p.read_text())
            raw = data.get("jobs", [])
            break
        except json.JSONDecodeError:
            if attempt == 0:
                time.sleep(0.1)
            else:
                return {
                    "total_jobs": 0, "healthy": 0, "failed": 0, "paused": 0,
                    "failures": [], "summary": "jobs.json unreadable",
                    "error": "JSON parse error"
                }

    healthy = 0
    failed_jobs = []
    failures = []
    paused = 0

    for r in raw:
        enabled = r.get("enabled", True)
        last_status = r.get("last_status")
        if not enabled:
            paused += 1
            continue
        if last_status == "error":
            failed_jobs.append(r)
            # Build descriptive error from available fields
            last_error = r.get("last_error", "")
            if not last_error:
                # Derive error description
                last_message = r.get("last_message", "")
                if "timeout" in str(r.get("last_output", "")).lower() or "timeout" in str(last_message).lower():
                    last_error = "Script timed out"
                elif "ConnectError" in str(r.get("last_output", "")) or "ConnectError" in str(last_message):
                    last_error = "Delivery failed: httpx.ConnectError"
                else:
                    last_error = "Unknown error"
            failures.append({
                "job_id": r.get("id"),
                "name": r.get("name", "Unnamed"),
                "last_status": last_status,
                "last_run_at": r.get("last_run_at"),
                "last_error": last_error,
            })
        else:
            healthy += 1

    return {
        "total_jobs": len(raw),
        "healthy": healthy,
        "failed": len(failures),
        "paused": paused,
        "failures": failures,
        "summary": f"{len(failures)} of {len(raw)} jobs have errors" if failures else "All jobs healthy",
    }



def harness_health():
    """AgentOS Harness Health — live system checks.
    Returns dict with channel_bindings_ok, skills_exist, cron_jobs_active,
    backup_age_hours, hardening_phase, system_score.
    """
    import re
    result = {}
    config_path = Path(os.path.expanduser("~/workspace/.hermes/config.yaml"))

    # ── channel_bindings_ok ──
    channel_bindings_ok = False
    try:
        import yaml
        if config_path.exists():
            with open(config_path) as f:
                cfg = yaml.safe_load(f)
            discord = cfg.get('discord', {})
            bindings = discord.get('channel_skill_bindings', [])
            free = str(discord.get('free_response_channels', ''))
            if isinstance(bindings, list) and free:
                channel_ids = [cid.strip() for cid in free.split(',') if cid.strip()]
                binding_ids = [b['id'] for b in bindings if 'id' in b]
                if len(binding_ids) == 8 and all(bid in channel_ids for bid in binding_ids):
                    channel_bindings_ok = True
    except Exception:
        pass
    result['channel_bindings_ok'] = channel_bindings_ok

    # ── skills_exist ──
    skills_dir = Path(os.path.expanduser('~/workspace/.hermes/skills'))
    try:
        skill_dirs = sorted(skills_dir.glob('agentos/agentos-*'))
        result['skills_exist'] = len([d for d in skill_dirs if d.is_dir()])
    except Exception:
        result['skills_exist'] = 0

    # ── cron_jobs_active ──
    cron_jobs = get_cron_jobs()
    result["cron_jobs_active"] = len([j for j in cron_jobs if j.get("enabled", True)])
    result["cron_healthy"] = len([j for j in cron_jobs if j.get("enabled", True) and j.get("last_status") != "error"])
    failed_jobs = [j for j in cron_jobs if j.get("enabled", True) and j.get("last_status") == "error"]
    result["cron_failed"] = len(failed_jobs)

    # ── backup_age_hours ──
    backup_dir = Path(os.path.expanduser("~/workspace/backups/hermes-config"))
    result["backup_age_hours"] = None
    try:
        if backup_dir.is_dir():
            backups = sorted(backup_dir.glob("hermes-backup-*.tar.gz"),
                             key=lambda p: p.stat().st_mtime, reverse=True)
            if backups:
                newest = backups[0]
                age_sec = time.time() - newest.stat().st_mtime
                result["backup_age_hours"] = round(age_sec / 3600.0, 2)
    except Exception:
        pass

    # ── hardening_phase ──
    roadmap_path = Path(os.path.expanduser(
        "~/workspace/.hermes/okf_memory/backlogs/agentos-hardening-roadmap.md"))
    result["hardening_phase"] = "None"
    max_phase = 0
    phase_names = {}
    try:
        if roadmap_path.exists():
            content = roadmap_path.read_text()
            current_phase_num = 0
            current_phase_name = ""
            for line in content.split("\n"):
                h_match = re.match(r"^##\s+H(\d+)\s*[—–-]\s*(.+)", line)
                if h_match:
                    current_phase_num = int(h_match.group(1))
                    current_phase_name = "H" + h_match.group(1) + " — " + h_match.group(2).strip()
                    phase_names[current_phase_num] = current_phase_name
                # Check for completed items: | ✅ | or ✅ with pipes
                if current_phase_num > 0 and "✅" in line and "|" in line:
                    max_phase = max(max_phase, current_phase_num)
            if max_phase > 0 and max_phase in phase_names:
                result["hardening_phase"] = phase_names[max_phase]
    except Exception:
        pass

    # ── system_score (weighted 0-100) ──
    score = 0.0
    if channel_bindings_ok:
        score += 30
    # skills_exist: 0=0, 1-2=10, 3-4=15, 5+=20
    skills = result["skills_exist"]
    if skills >= 5:
        score += 20
    elif skills >= 3:
        score += 15
    elif skills >= 1:
        score += 10
    # cron_jobs_active: 0=0, 1-2=10, 3+=20
    cron_n = result["cron_jobs_active"]
    if cron_n >= 3:
        score += 20
    elif cron_n >= 1:
        score += 10
    # Penalty for failed cron jobs
    if result["cron_failed"] > 0:
        score -= 10
    # backup_fresh: <24h=30, <48h=20, <168h=10, else=0
    backup_h = result["backup_age_hours"]
    if backup_h is not None:
        if backup_h < 24:
            score += 30
        elif backup_h < 48:
            score += 20
        elif backup_h < 168:
            score += 10
    result["system_score"] = int(round(score))

    return result


def build_snapshot():
    """Full snapshot for the /api/snapshot endpoint."""
    gw = get_gateway()
    # Compute uptime from gateway start_time (monotonic procfs clock inside proot)
    uptime = None
    try:
        raw = gw.get("start_time")
        if isinstance(raw, str) and raw.startswith("t+"):
            uptime = raw   # already normalised to "t+NNNs"
        elif isinstance(raw, (int, float)):
            uptime = f"t+{int(raw)}s"
    except Exception:
        uptime = None

    return {
        "gateway": {
            "state": gw.get("gateway_state", "?"),
            "active_agents": gw.get("active_agents", 0),
            "pid": gw.get("pid"),
            "platforms": gw.get("platforms", {}),
            "updated_at": gw.get("updated_at"),
            "uptime": uptime,
        },
        "vps_health": get_system(),
        "activity": get_logs_activity(),
        "activity_by_day": get_activity_by_day(),
        "agents": get_agents(),
        "sessions": get_sessions_summary(),
        "cron_jobs": get_cron_jobs(),
        "cron_health": get_cron_health(),
        "kanban": get_kanban_stats(),
        "harness": harness_health(),
        "health": {
            "status": "healthy" if all([
                (HERMES_HOME / f).exists() for f in
                ("gateway_state.json", "state.db", "kanban.db", "agent-logs.db")
            ]) else "degraded"
        }
    }

def build_summary():
    """Summary for the /api/summary v2 dashboard."""
    return {
        "health": {"status": "healthy" if all([
            (HERMES_HOME / f).exists() for f in
            ("gateway_state.json", "state.db", "kanban.db", "agent-logs.db")
        ]) else "degraded",
            "checks": {f"{n}": "ok" if (HERMES_HOME / p).exists() else "missing"
                for n, p in [("gateway_json","gateway_state.json"),("state_db","state.db"),
                             ("kanban_db","kanban.db"),("agent_logs_db","agent-logs.db")]}},
        "gateway": get_gateway(),
        "sessions": get_sessions(),
        "messages": get_messages(),
        "kanban": get_kanban_stats(),
        "logs": get_logs(),
    }

# ── SSE handler ─────────────────────────────────────────────
class SSEHandler:
    """Handles /events SSE endpoint."""
    def __init__(self, wfile):
        self.wfile = wfile
        self.queue = queue.Queue(maxsize=20)
        with sse_lock:
            sse_clients.append(self.queue)

    def send(self, event, data):
        msg = f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
        try: self.wfile.write(msg.encode()); self.wfile.flush()
        except: self.cleanup()

    def cleanup(self):
        with sse_lock:
            if self.queue in sse_clients:
                sse_clients.remove(self.queue)

# ── HTTP handler ─────────────────────────────────────────────
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/" or p == "/index.html":
            return self._serve_file("index.html")
        if p == "/v2":
            return self._serve_file("dashboard.html")
        if p == "/tokens.css":
            return self._serve_file("tokens.css")
        if p == "/components.js":
            return self._serve_file("components.js")
        if p == "/refresh.js":
            return self._serve_file("refresh.js")
        if p == "/api/health":
            return self._json({"ok": True, "time": datetime.now(timezone.utc).isoformat()})
        if p == "/api/cron/health":
            return self._json(get_cron_health())
        if p == "/api/harness":
            return self._json(harness_health())
        if p == "/api/snapshot":
            return self._json(build_snapshot())
        if p == "/api/summary":
            return self._json(build_summary())
        if p == "/api/board":
            return self._json(board_list())
        if p == "/api/gateway":
            return self._json(get_gateway())
        if p == "/api/sessions":
            return self._json(get_sessions())
        if p == "/api/messages":
            return self._json(get_messages())
        if p == "/api/kanban":
            return self._json(get_kanban_stats())
        if p == "/api/logs":
            return self._json(get_logs())
        if p == "/api/agents":
            return self._json(get_agents())
        if p == "/api/system":
            return self._json(get_system())
        if p == "/api/content":
            return self._json(content_list())
        if p == "/api/sdlc/projects":
            return self._json(get_sdlc_projects())
        if p == "/api/sdlc/activity":
            return self._json(get_sdlc_activity())
        if p == "/api/sdlc/content":
            return self._json(get_sdlc_content())
        if p == "/api/sdlc/reviews":
            return self._json(get_sdlc_review_queue())
        if p == "/api/tools":
            return self._json(get_tool_tracing(limit=20))
        if p == "/api/health-score":
            return self._json(get_health_score())
        if p == "/api/cron/health":
            return self._json(get_cron_health())
        if p == "/api/content/get":
            qs = {}
            if "?" in self.path:
                for kv in self.path.split("?", 1)[1].split("&"):
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        qs[k] = v
            agent = qs.get("agent", "")
            filename = qs.get("file", "")
            if not agent or not filename:
                return self._json({"error": "missing ?agent= and ?file="}, 400)
            result = content_get(agent, filename)
            if result is None:
                return self._json({"error": "file not found"}, 404)
            return self._json(result)
        if p == "/events":
            return self._sse()
        self._json({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(body)
        except Exception:
            return self._json({"error": "invalid JSON"}, 400)

        p = self.path.split("?")[0]
        # Parse query string for ?id=
        qs = {}
        if "?" in self.path:
            for kv in self.path.split("?", 1)[1].split("&"):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    qs[k] = v

        if p == "/api/board/create":
            task = board_create(data)
            return self._json(task, 201)
        if p == "/api/board/update":
            task_id = qs.get("id")
            if not task_id:
                return self._json({"error": "missing ?id="}, 400)
            task = board_update(task_id, data)
            if task is None:
                return self._json({"error": "task not found"}, 404)
            return self._json(task)
        if p == "/api/board/delete":
            task_id = qs.get("id")
            if not task_id:
                return self._json({"error": "missing ?id="}, 400)
            board_delete(task_id)
            return self._json({"ok": True})
        if p == "/api/content/save":
            agent = data.get("agent", "")
            filename = data.get("filename", "")
            content = data.get("content", "")
            if not agent or not filename:
                return self._json({"error": "missing agent or filename"}, 400)
            ok = content_save(agent, filename, content)
            if not ok:
                return self._json({"error": "save failed"}, 400)
            sse_broadcast("content", {"action": "save", "agent": agent, "filename": filename})
            return self._json({"ok": True})
        self._json({"error": "not found"}, 404)

    def _serve_file(self, filename):
        fpath = BASE_DIR / filename
        if fpath.suffix == ".html":
            ct = "text/html; charset=utf-8"
        elif fpath.suffix == ".css":
            ct = "text/css"
        elif fpath.suffix == ".js":
            ct = "application/javascript"
        else:
            ct = "application/octet-stream"
        try:
            body = fpath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ct)
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(body)
        except FileNotFoundError:
            self._json({"error": f"{filename} not found"}, 404)

    def _json(self, data, status=200):
        body = json.dumps(data, default=str, indent=2).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        sse = SSEHandler(self.wfile)
        try:
            # Send initial snapshot
            sse.send("snapshot", build_snapshot())
            # Keep alive, wait for data
            while True:
                try:
                    event, data = sse.queue.get(timeout=15)
                    sse.send(event, data)
                except queue.Empty:
                    # Send keepalive comment
                    try: self.wfile.write(b": keepalive\n\n"); self.wfile.flush()
                    except: break
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            sse.cleanup()

    def log_message(self, format, *args):
        pass  # silent

if __name__ == "__main__":
    # Init board database
    init_board_db()
    # Write v2 dashboard if it doesn't exist
    v2 = BASE_DIR / "dashboard.html"
    if not v2.exists():
        v2.write_text("""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AgentOS MC v2</title><style>
*{box-sizing:border-box;margin:0;padding:0}html{font-size:14px}
body{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;line-height:1.5}
header{background:#161b22;border-bottom:1px solid #21262d;padding:1rem 1.5rem;display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:.5rem}
h1{font-size:1.2rem;color:#58a6ff;display:flex;align-items:center;gap:.5rem}
.dot{width:10px;height:10px;border-radius:50%;display:inline-block}
.dot-ok{background:#3fb950}.dot-err{background:#f85149}
.meta{font-size:.78rem;color:#8b949e;display:flex;gap:1rem;align-items:center}
.grid{padding:1.25rem;display:grid;grid-template-columns:1fr 1fr;gap:1rem;max-width:1400px;margin:0 auto}
.full{grid-column:1/-1}
.card{background:#161b22;border:1px solid #21262d;border-radius:8px;overflow:hidden}
.hd{padding:.6rem 1rem;border-bottom:1px solid #21262d;font-size:.8rem;font-weight:600;color:#8b949e;text-transform:uppercase;letter-spacing:.05em;display:flex;justify-content:space-between;align-items:center}
.bd{padding:.6rem 1rem}.nopad{padding:0}
.badge{display:inline-block;padding:.12em .55em;border-radius:10px;font-size:.72rem;font-weight:600}
.bg-ok{background:#122d1e;color:#3fb950}.bg-warn{background:#2e240f;color:#d29922}
.bg-err{background:#2d141b;color:#f85149}.bg-info{background:#162838;color:#58a6ff}
.bg-muted{background:#1c2128;color:#8b949e}
table{width:100%;border-collapse:collapse;font-size:.78rem}
th{text-align:left;padding:.4rem .6rem;color:#8b949e;font-weight:600;font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;border-bottom:1px solid #21262d;background:#0d1117}
td{padding:.35rem .6rem;border-bottom:1px solid #21262d30}tr:hover td{background:#1c212840}
.mono{font-family:'SF Mono','Fira Code',monospace;font-size:.75rem}
.trunc{max-width:240px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.num{text-align:right;font-variant-numeric:tabular-nums}
.plt-row{display:flex;align-items:center;gap:.5rem;padding:.3rem 0;border-bottom:1px solid #21262d30}
.plt-row:last-child{border-bottom:none}.plat-name{font-weight:600;min-width:65px}
.c-ok{color:#3fb950}.c-err{color:#f85149}
.msg-line{padding:.3rem .6rem;border-bottom:1px solid #21262d30;font-size:.78rem}
.msg-line:hover{background:#1c212840}
.r-user{color:#3fb950}.r-assistant{color:#58a6ff}.r-tool{color:#d29922}
.msg-role{display:inline-block;min-width:50px;font-weight:600;font-size:.7rem;text-transform:uppercase;letter-spacing:.04em}
.msg-meta{color:#8b949e;font-size:.68rem;margin-left:.4rem}
.empty{color:#8b949e;font-style:italic;padding:1rem;text-align:center}
.err-state{color:#f85149;padding:1rem;text-align:center}
footer{text-align:center;padding:.8rem;color:#484f58;font-size:.72rem;border-top:1px solid #21262d;margin-top:1rem}
@media(max-width:800px){.grid{grid-template-columns:1fr}.full{grid-column:1}}
</style></head><body>
<header><h1><span id="dot" class="dot dot-ok"></span> AgentOS Mission Control v2</h1>
<div class="meta"><span id="ts">—</span>
<select id="ref" onchange="setRefresh()" style="background:#21262d;color:#c9d1d9;border:1px solid #30363d;padding:2px 6px;border-radius:4px;font-size:.78rem">
<option value="5">5s</option><option value="10" selected>10s</option><option value="30">30s</option><option value="60">60s</option><option value="0">off</option></select></div></header>
<div class="grid">
<div class="card"><div class="hd">Gateway <span id="gw-badge" class="badge bg-muted">—</span></div><div class="bd" id="gw"></div></div>
<div class="card"><div class="hd">Agent Logs</div><div class="bd" id="ls"></div></div>
<div class="card full"><div class="hd">Sessions <span id="se-cnt" class="badge bg-muted">—</span></div><div class="nopad" id="se"></div></div>
<div class="card full"><div class="hd">Kanban <span id="kb-cnt" class="badge bg-muted">—</span></div><div class="nopad" id="kb"></div></div>
<div class="card full"><div class="hd">Recent Messages</div><div class="nopad" id="msg" style="max-height:380px;overflow-y:auto"></div></div>
</div><footer>AgentOS Mission Control v2 · Read-only</footer>
<script>
let timer=null,secs=10;
function bad(c,t){return '<span class="badge bg-'+c+'">'+esc(t)+'</span>'}
function esc(s){return s==null?'\\u2014':String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')}
function ts(v){if(!v||v==='None')return'\\u2014';if(v.startsWith('t+'))return v;try{return new Date(v).toLocaleString(void 0,{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit',second:'2-digit'})}catch(e){return v}}
async function load(){try{const r=await fetch('/api/summary');const d=await r.json();document.getElementById('dot').className='dot '+(d.health?.status==='healthy'?'dot-ok':'dot-err');document.getElementById('ts').textContent=new Date().toLocaleTimeString();gw(d.gateway);se(d.sessions);msg(d.messages);kb(d.kanban);lg(d.logs)}catch(e){document.getElementById('dot').className='dot dot-err';document.getElementById('ts').textContent='error: '+e.message}}
function gw(d){if(!d||d.error){document.getElementById('gw').innerHTML='<div class="err-state">'+esc(d?.error)+'</div>';return}const gb=document.getElementById('gw-badge');gb.className='badge '+(d.gateway_state==='running'?'bg-ok':'bg-err');gb.textContent=d.gateway_state||'unknown';let h='<div style="display:flex;gap:1.2rem;flex-wrap:wrap;margin-bottom:.4rem">';h+='<div><span style="color:#8b949e">PID</span> '+esc(d.pid)+'</div>';h+='<div><span style="color:#8b949e">Agents</span> '+esc(d.active_agents)+'</div>';h+='<div><span style="color:#8b949e">Updated</span> '+ts(d.updated_at)+'</div></div>';if(d.platforms)for(const[n,i]of Object.entries(d.platforms)){const ok=i.state==='connected';h+='<div class="plt-row"><span class="plat-name">'+esc(n)+'</span>';h+='<span class="'+(ok?'c-ok':'c-err')+'">'+esc(i.state)+'</span>';h+='<span style="color:#8b949e;font-size:.72rem">'+ts(i.updated_at)+'</span></div>'}document.getElementById('gw').innerHTML=h}
function se(d){document.getElementById('se-cnt').textContent=(d||[]).length;if(!d||d.length===0){document.getElementById('se').innerHTML='<div class="empty">No sessions</div>';return}let h='<table><thead><tr><th>Title</th><th>Model</th><th>Started</th><th>Msgs</th><th>Tools</th><th>Tokens in</th><th>Cost</th></tr></thead><tbody>';for(const s of d)h+='<tr><td class="trunc" title="'+esc(s.title)+'">'+esc(s.title)+'</td><td class="mono">'+esc(s.model)+'</td><td>'+ts(s.started_at)+'</td><td class="num">'+esc(s.message_count)+'</td><td class="num">'+esc(s.tool_call_count)+'</td><td class="num">'+Number(s.input_tokens||0).toLocaleString()+'</td><td class="num mono">'+(s.estimated_cost_usd?'$'+Number(s.estimated_cost_usd).toFixed(4):'\\u2014')+'</td></tr>';h+='</tbody></table>';document.getElementById('se').innerHTML=h}
function msg(d){if(!d||d.length===0){document.getElementById('msg').innerHTML='<div class="empty">No messages</div>';return}let h='';for(const m of d)h+='<div class="msg-line"><span class="msg-role r-'+m.role+'">'+esc(m.role)+'</span> '+esc(m.preview||'')+'<span class="msg-meta">'+ts(m.timestamp)+'</span></div>';document.getElementById('msg').innerHTML=h}
function kb(d){if(!d||d.error){document.getElementById('kb').innerHTML='<div class="empty">'+esc(d?.error||'No kanban data')+'</div>';return}document.getElementById('kb-cnt').textContent=d.total||0;if(!d.tasks||d.tasks.length===0){document.getElementById('kb').innerHTML='<div class="empty">Board is empty</div>';return}let h='<table style="margin:.6rem 1rem;width:calc(100% - 2rem)"><thead><tr><th>ID</th><th>Title</th><th>Status</th><th>Priority</th><th>Assignee</th><th>Created</th></tr></thead><tbody>';for(const t of d.tasks)h+='<tr><td class="mono trunc" style="max-width:160px" title="'+esc(t.id)+'">'+esc(t.id)+'</td><td class="trunc">'+esc(t.title)+'</td><td>'+bad(t.status==='done'?'ok':'warn',t.status)+'</td><td class="num">'+esc(t.priority)+'</td><td>'+esc(t.assignee)+'</td><td>'+ts(t.created_at)+'</td></tr>';h+='</tbody></table>';document.getElementById('kb').innerHTML=h}
function lg(d){if(!d||d.length===0){document.getElementById('ls').innerHTML='<div class="empty">No logs</div>';return}const by={};for(const e of d)by[e.agent_name]=(by[e.agent_name]||0)+1;let h='<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(100px,1fr));gap:.6rem">';h+='<div><div style="font-size:1.4rem;font-weight:700;color:#58a6ff;text-align:center">'+d.length+'</div><div style="font-size:.68rem;color:#8b949e;text-transform:uppercase;letter-spacing:.04em;text-align:center">Total</div></div>';for(const[a,n]of Object.entries(by))h+='<div><div style="font-size:1.4rem;font-weight:700;color:#58a6ff;text-align:center">'+n+'</div><div style="font-size:.68rem;color:#8b949e;text-transform:uppercase;letter-spacing:.04em;text-align:center">'+esc(a)+'</div></div>';h+='</div>';if(d[0])h+='<div style="margin-top:.4rem;font-size:.72rem;color:#8b949e">Latest: <strong style="color:#c9d1d9">'+esc(d[0].agent_name)+'</strong> \\u2014 '+esc(d[0].task_description)+'</div>';document.getElementById('ls').innerHTML=h}
function setRefresh(){secs=parseInt(document.getElementById('ref').value)||0;if(timer)clearInterval(timer);if(secs>0)timer=setInterval(load,secs*1000)}
load().then(setRefresh);
</script></body></html>""")

    # Background SSE push — rebuilds snapshot every 5s and broadcasts
    def sse_ticker():
        while True:
            time.sleep(5)
            try: sse_broadcast("snapshot", build_snapshot())
            except Exception: pass
    threading.Thread(target=sse_ticker, daemon=True).start()

    srv = http.server.ThreadingHTTPServer(("0.0.0.0", 51763), H)
    print(f"AgentOS Mission Control → http://0.0.0.0:51763", file=sys.stderr)
    print(f"  /api/snapshot  — full snapshot for v1 dashboard", file=sys.stderr)
    print(f"  /api/summary   — summary for v2 dashboard (/v2)", file=sys.stderr)
    print(f"  /api/system    — live CPU/RAM/Disk only", file=sys.stderr)
    print(f"  /api/content   — list library content by agent", file=sys.stderr)
    print(f"  /api/content/get — get specific content file", file=sys.stderr)
    print(f"  /api/content/save — save content file", file=sys.stderr)
    print(f"  /events        — SSE live updates (5s ticker)", file=sys.stderr)
    try: srv.serve_forever()
    except KeyboardInterrupt: srv.shutdown()

