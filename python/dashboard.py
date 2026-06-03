#!/usr/bin/env python3
"""OverDrop Web Dashboard v3 — Live observability + full task control."""

import http.server, socketserver, json, os, sys, time, threading, uuid
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from overdrop import FsProtocol, MailBus, MessageType

class SSEManager:
    def __init__(self):
        self._clients = []
        self._lock = threading.Lock()
    def add(self, h):
        with self._lock: self._clients.append(h)
    def remove(self, h):
        with self._lock:
            if h in self._clients: self._clients.remove(h)
    def broadcast(self, event_type, data):
        msg = f"event: {event_type}\ndata: {json.dumps(data, default=str)}\n\n"
        with self._lock:
            dead = []
            for c in self._clients:
                try:
                    c.wfile.write(msg.encode("utf-8"))
                    c.wfile.flush()
                except:
                    dead.append(c)
            for d in dead: self._clients.remove(d)

sse = SSEManager()
HTML = open(os.path.join(os.path.dirname(__file__), "..", "dashboard.html")).read()

_ws = None; _fs = None; _bus = None
_html_cache = None

def get_html():
    """Load HTML from modular dashboard/ or fallback to monolith."""
    global _html_cache
    if _html_cache:
        return _html_cache
    # Try modular dashboard first
    modular = os.path.join(os.path.dirname(__file__), "..", "dashboard", "index.html")
    if os.path.exists(modular):
        _html_cache = open(modular).read()
    else:
        _html_cache = open(os.path.join(os.path.dirname(__file__), "..", "dashboard.html")).read()
    return _html_cache

def get_fs():
    global _fs
    if not _fs: _fs = FsProtocol(str(_ws))
    return _fs

def get_bus():
    global _bus
    if not _bus:
        _bus = MailBus(os.path.join(str(_ws), "overdrop.db"))
        _bus.connect()
    return _bus

def _json_response(handler, data, status=200):
    body = json.dumps(data, default=str).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)

def _read_body(handler):
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    return json.loads(handler.rfile.read(length))

def _find_task_full(task_id):
    """Find task by full ID or prefix across all folders."""
    fs = get_fs()
    for fld in ["inbox", "active", "done", "failed", "blocked", "feedback"]:
        for t in fs.list_tasks(fld):
            if t.id.startswith(task_id) or t.id == task_id:
                return t, fld
    return None, None

# ---- API HANDLERS ----

def api_create_task(handler, body):
    """POST /api/tasks — create new task."""
    fs = get_fs()
    title = body.get("title", "Untitled task")
    assignee = body.get("assignee", None)
    from_agent = body.get("from", "dashboard")
    priority = body.get("priority", 5)
    task_id = fs.submit(title, from_agent=from_agent, assign=assignee, priority=priority)

    bus = get_bus()
    bus.send(MessageType.BROADCAST, sender="dashboard", recipient="@all",
             payload={"event": "task_created", "task_id": task_id, "title": title, "assignee": assignee})
    _json_response(handler, {"ok": True, "task_id": task_id})

def api_claim_task(handler, body, task_id):
    """POST /api/tasks/:id/claim — claim task for agent."""
    fs = get_fs()
    agent = body.get("agent", "dashboard-user")
    task, fld = _find_task_full(task_id)
    if not task:
        return _json_response(handler, {"error": "Task not found"}, 404)
    if fld != "inbox":
        return _json_response(handler, {"error": f"Task is in {fld}, not inbox"}, 400)

    claimed = fs.claim(agent, task.id)
    if not claimed:
        return _json_response(handler, {"error": "Already claimed by another agent"}, 409)

    bus = get_bus()
    bus.send(MessageType.BROADCAST, sender="dashboard", recipient="@all",
             payload={"event": "task_claimed", "agent": agent, "task": task.title})
    _json_response(handler, {"ok": True, "task_id": task.id})

def api_complete_task(handler, body, task_id):
    """POST /api/tasks/:id/done — complete task."""
    fs = get_fs()
    result = body.get("result", {"completed_via": "dashboard"})
    task, fld = _find_task_full(task_id)
    if not task:
        return _json_response(handler, {"error": "Task not found"}, 404)
    if fld not in ("active",):
        return _json_response(handler, {"error": f"Task is in {fld}, not active"}, 400)

    fs.complete(task.id, result=result)
    bus = get_bus()
    bus.send(MessageType.WORKER_DONE, sender="dashboard", recipient=task.from_agent or "hermes",
             payload={"task_id": task.id, "result": result}, task_id=task.id)
    bus.send(MessageType.BROADCAST, sender="dashboard", recipient="@all",
             payload={"event": "task_completed", "task": task.title, "result": result})
    _json_response(handler, {"ok": True, "task_id": task.id})

def api_block_task(handler, body, task_id):
    """POST /api/tasks/:id/block — block task."""
    fs = get_fs()
    reason = body.get("reason", "Blocked from dashboard")
    task, fld = _find_task_full(task_id)
    if not task:
        return _json_response(handler, {"error": "Task not found"}, 404)
    if fld != "active":
        return _json_response(handler, {"error": f"Task is in {fld}, not active"}, 400)

    fs.block(task.id, reason=reason)
    bus = get_bus()
    bus.send(MessageType.BROADCAST, sender="dashboard", recipient="@all",
             payload={"event": "task_blocked", "task": task.title, "reason": reason})
    _json_response(handler, {"ok": True, "task_id": task.id})

def api_unblock_task(handler, body, task_id):
    """POST /api/tasks/:id/unblock — unblock task back to inbox."""
    fs = get_fs()
    task, fld = _find_task_full(task_id)
    if not task:
        return _json_response(handler, {"error": "Task not found"}, 404)
    if fld != "blocked":
        return _json_response(handler, {"error": f"Task is in {fld}, not blocked"}, 400)

    fs.unblock(task.id)
    bus = get_bus()
    bus.send(MessageType.BROADCAST, sender="dashboard", recipient="@all",
             payload={"event": "task_unblocked", "task": task.title})
    _json_response(handler, {"ok": True, "task_id": task.id})

def api_fail_task(handler, body, task_id):
    """POST /api/tasks/:id/fail — fail task."""
    fs = get_fs()
    error = body.get("error", "Failed from dashboard")
    task, fld = _find_task_full(task_id)
    if not task:
        return _json_response(handler, {"error": "Task not found"}, 404)
    if fld != "active":
        return _json_response(handler, {"error": f"Task is in {fld}, not active"}, 400)

    fs.fail(task.id, error=error)
    bus = get_bus()
    bus.send(MessageType.ESCALATE, sender="dashboard", recipient=task.from_agent or "hermes",
             payload={"task_id": task.id, "error": error}, task_id=task.id)
    bus.send(MessageType.BROADCAST, sender="dashboard", recipient="@all",
             payload={"event": "task_failed", "task": task.title, "error": error})
    _json_response(handler, {"ok": True, "task_id": task.id})

def api_delete_task(handler, body, task_id):
    """DELETE /api/tasks/:id — delete task from any folder."""
    fs = get_fs()
    task, fld = _find_task_full(task_id)
    if not task:
        return _json_response(handler, {"error": "Task not found"}, 404)

    path = fs._path(fld, task.id)
    try:
        os.remove(path)
    except FileNotFoundError:
        return _json_response(handler, {"error": "File not found"}, 404)

    bus = get_bus()
    bus.send(MessageType.BROADCAST, sender="dashboard", recipient="@all",
             payload={"event": "task_deleted", "task": task.title, "from": fld})
    _json_response(handler, {"ok": True, "task_id": task.id})

def api_task_details(handler, task_id):
    """GET /api/tasks/:id — full task details."""
    fs = get_fs()
    task, fld = _find_task_full(task_id)
    if not task:
        return _json_response(handler, {"error": "Task not found"}, 404)

    data = {
        "id": task.id,
        "title": task.title,
        "status": task.status.value,
        "folder": fld,
        "from_agent": task.from_agent,
        "assignee": task.assignee,
        "priority": task.priority,
        "max_retries": task.max_retries,
        "retry_count": task.retry_count,
        "context": task.context,
        "result": task.result,
        "parent_task": task.parent_task,
        "group_id": task.group_id,
        "worktree": task.worktree,
        "version": task.version,
        "created_at": task.created_at,
    }
    _json_response(handler, data)

def api_list_all_tasks(handler, params):
    """GET /api/tasks — list all tasks with optional filters."""
    fs = get_fs()
    status_filter = params.get("status", [None])[0]
    agent_filter = params.get("agent", [None])[0]
    search = params.get("q", [None])[0]

    folders = ["inbox", "active", "done", "failed", "blocked", "feedback"]
    if status_filter:
        folders = [status_filter] if status_filter in folders else []

    tasks = []
    for fld in folders:
        for t in fs.list_tasks(fld):
            if agent_filter and t.assignee != agent_filter and t.from_agent != agent_filter:
                continue
            if search and search.lower() not in t.title.lower():
                continue
            tasks.append({
                "id": t.id[:8],
                "full_id": t.id,
                "title": t.title,
                "status": t.status.value,
                "folder": fld,
                "from_agent": t.from_agent,
                "assignee": t.assignee or "-",
                "priority": t.priority,
                "retry_count": t.retry_count,
                "max_retries": t.max_retries,
                "created_at": t.created_at,
                "result": t.result,
            })
    _json_response(handler, tasks)

def _load_known_agents():
    """Load known agents from pre-generated JSON file."""
    json_path = os.path.join(str(_ws), "known_agents.json")
    if not os.path.exists(json_path):
        return {}
    try:
        with open(json_path) as f:
            agents_list = json.load(f)
        return {a["name"]: a for a in agents_list}
    except Exception as e:
        print(f"[ERROR] Loading known_agents.json: {e}")
        return {}

def _save_known_agents(agents_dict):
    """Save known agents to JSON file."""
    json_path = os.path.join(str(_ws), "known_agents.json")
    try:
        with open(json_path, "w") as f:
            json.dump(list(agents_dict.values()), f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[ERROR] Saving known_agents.json: {e}")

# Available models
AVAILABLE_MODELS = [
    {"id": "router://deepseek-v4-flash", "name": "DeepSeek V4 Flash", "provider": "router", "speed": "fast", "cost": "low"},
    {"id": "router://deepseek-v4-pro", "name": "DeepSeek V4 Pro", "provider": "router", "speed": "medium", "cost": "medium"},
    {"id": "cloudflare:@cf/meta/llama-3.3-70b-instruct-fp8-fast", "name": "Llama 3.3 70B (CF)", "provider": "cloudflare", "speed": "fast", "cost": "free"},
    {"id": "cloudflare:@cf/meta/llama-4-scout-17b-16e-instruct", "name": "Llama 4 Scout 17B (CF)", "provider": "cloudflare", "speed": "fast", "cost": "free"},
    {"id": "cloudflare:@cf/moonshotai/kimi-k2.5", "name": "Kimi K2.5 (CF)", "provider": "cloudflare", "speed": "medium", "cost": "free"},
    {"id": "cloudflare:@cf/qwen/qwen2.5-coder-32b-instruct", "name": "Qwen 2.5 Coder 32B (CF)", "provider": "cloudflare", "speed": "medium", "cost": "free"},
    {"id": "groq:llama-3.3-70b-versatile", "name": "Llama 3.3 70B (Groq)", "provider": "groq", "speed": "fast", "cost": "free"},
    {"id": "groq:qwen/qwen3-32b", "name": "Qwen 3 32B (Groq)", "provider": "groq", "speed": "fast", "cost": "free"},
    {"id": "groq:llama-3.1-8b-instant", "name": "Llama 3.1 8B (Groq)", "provider": "groq", "speed": "fastest", "cost": "free"},
    {"id": "opencode:deepseek-v4-flash", "name": "DeepSeek V4 Flash (OC)", "provider": "opencode", "speed": "fast", "cost": "low"},
    {"id": "opencode:deepseek-v4-pro", "name": "DeepSeek V4 Pro (OC)", "provider": "opencode", "speed": "medium", "cost": "medium"},
    {"id": "opencode-zen:deepseek-v4-flash-free", "name": "DeepSeek V4 Flash Free", "provider": "opencode-zen", "speed": "fast", "cost": "free"},
    {"id": "opencode-zen:nemotron-3-super-free", "name": "Nemotron 3 Super Free", "provider": "opencode-zen", "speed": "fast", "cost": "free"},
    {"id": "openrouter:nvidia/nemotron-3-super-120b-a12b:free", "name": "Nemotron 3 Super 120B", "provider": "openrouter", "speed": "medium", "cost": "free"},
    {"id": "openrouter:openai/gpt-4.1-nano", "name": "GPT-4.1 Nano", "provider": "openrouter", "speed": "fast", "cost": "low"},
    {"id": "openrouter:google/gemini-2.5-flash", "name": "Gemini 2.5 Flash", "provider": "openrouter", "speed": "fast", "cost": "low"},
]

def api_models(handler):
    """GET /api/models — list available models."""
    _json_response(handler, AVAILABLE_MODELS)

def api_update_agent_model(handler, body, agent_name):
    """POST /api/agents/:name/model — update agent's model."""
    model_id = body.get("model")
    if not model_id:
        return _json_response(handler, {"error": "model field required"}, 400)

    known = _load_known_agents()
    if agent_name not in known:
        return _json_response(handler, {"error": f"Agent '{agent_name}' not found"}, 404)

    known[agent_name]["model"] = model_id
    _save_known_agents(known)

    # Broadcast update
    sse.broadcast("agent_model", {"agent": agent_name, "model": model_id})
    _json_response(handler, {"ok": True, "agent": agent_name, "model": model_id})

def api_agents(handler):
    """GET /api/agents — intelligent agent list with roles and status."""
    fs = get_fs()
    known = _load_known_agents()
    agents = {}

    # Start with known agents from manifest
    for name, info in known.items():
        agents[name] = {
            **info,
            "tasks_active": 0, "tasks_done": 0, "tasks_failed": 0, "tasks_total": 0,
            "current_task": None, "last_active": None, "live_status": "idle",
        }

    # Add Hermes (not in manifest)
    if "hermes" not in agents:
        agents["hermes"] = {
            "name": "hermes", "role": "coordinator", "type": "hermes",
            "description": "Hermes Agent — główny orkiestrator AI, narzędzia, pamięć",
            "capabilities": ["orchestration", "tools", "memory", "cron", "delegation"],
            "model": "current", "manifest_status": "active",
            "subagents": [], "tasks_active": 0, "tasks_done": 0,
            "tasks_failed": 0, "tasks_total": 0, "current_task": None,
            "last_active": None, "live_status": "idle",
        }

    # Update with live task data
    for fld in ["inbox", "active", "done", "failed", "blocked", "feedback"]:
        for t in fs.list_tasks(fld):
            if t.assignee:
                a = t.assignee
                if a not in agents:
                    agents[a] = {
                        "name": a, "role": "executor", "type": "unknown",
                        "description": "", "capabilities": [], "model": "",
                        "manifest_status": "none", "subagents": [],
                        "tasks_active": 0, "tasks_done": 0, "tasks_failed": 0,
                        "tasks_total": 0, "current_task": None,
                        "last_active": None, "live_status": "idle",
                    }
                agents[a]["tasks_total"] += 1
                if t.status.value in ("active", "claimed"):
                    agents[a]["tasks_active"] += 1
                    agents[a]["live_status"] = "thinking"
                    agents[a]["current_task"] = t.title[:40]
                    agents[a]["last_active"] = t.created_at
                elif t.status.value == "done":
                    agents[a]["tasks_done"] += 1
                elif t.status.value == "failed":
                    agents[a]["tasks_failed"] += 1

    # Sort: active first, then by type priority, then by task count
    type_order = {"orchestrator": 0, "hermes": 1, "pipeline": 2, "specialist": 3, "factory": 4, "monitoring": 5, "unknown": 9}
    result = sorted(agents.values(), key=lambda a: (
        -a["tasks_active"],
        type_order.get(a.get("type", "unknown"), 9),
        -a["tasks_total"],
    ))
    _json_response(handler, result)


def api_merge_queue(handler):
    """GET /api/merge-queue — list merge queue entries."""
    try:
        db_path = os.path.join(str(_ws), "overdrop.db")
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        # Auto-create table if missing
        conn.execute("""CREATE TABLE IF NOT EXISTS merge_queue (
            task_id TEXT PRIMARY KEY, branch TEXT NOT NULL, worktree TEXT NOT NULL,
            agent_id TEXT NOT NULL, priority INTEGER DEFAULT 5, status TEXT DEFAULT 'pending',
            conflict_level INTEGER DEFAULT 0, error_log TEXT,
            created_at TEXT DEFAULT (datetime('now')), merged_at TEXT
        )""")
        conn.commit()
        rows = conn.execute(
            "SELECT * FROM merge_queue WHERE status IN ('pending', 'dry_run', 'resolving') ORDER BY priority DESC, created_at ASC"
        ).fetchall()
        conn.close()
        result = [dict(r) for r in rows]
        _json_response(handler, result)
    except Exception as e:
        _json_response(handler, {"error": str(e)}, 500)


def api_trigger_merge(handler, task_id):
    """POST /api/merge-queue/:id/trigger — enqueue task for merge."""
    try:
        db_path = os.path.join(str(_ws), "overdrop.db")
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        
        # Check if already in queue
        existing = conn.execute("SELECT * FROM merge_queue WHERE task_id=?", (task_id,)).fetchone()
        if existing:
            conn.close()
            _json_response(handler, {"error": "Already in queue", "status": existing["status"]}, 409)
            return
        
        # Get task info from filesystem
        fs = get_fs()
        task = None
        for status in ["active", "done", "inbox"]:
            for t in fs.list_tasks(status):
                if t.id == task_id:
                    task = t
                    break
            if task:
                break
        
        if not task:
            conn.close()
            _json_response(handler, {"error": "Task not found"}, 404)
            return
        
        branch = f"od/{task.assignee or 'unknown'}/{task_id[:8]}"
        worktree_path = f"/tmp/overdrop-worktrees/od-{task_id[:8]}-{task.assignee or 'unknown'}"
        
        conn.execute(
            "INSERT OR REPLACE INTO merge_queue (task_id, branch, worktree, agent_id, priority, status) VALUES (?, ?, ?, ?, ?, 'pending')",
            (task_id, branch, worktree_path, task.assignee or "unknown", task.priority)
        )
        conn.commit()
        conn.close()
        
        _json_response(handler, {"ok": True, "task_id": task_id})
    except Exception as e:
        _json_response(handler, {"error": str(e)}, 500)


def api_process_merge(handler, task_id):
    """POST /api/merge-queue/:id/process — process a pending merge."""
    try:
        db_path = os.path.join(str(_ws), "overdrop.db")
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        
        row = conn.execute("SELECT * FROM merge_queue WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            conn.close()
            _json_response(handler, {"error": "Not in queue"}, 404)
            return
        
        if row["status"] != "pending":
            conn.close()
            _json_response(handler, {"error": f"Cannot process: status is {row['status']}"}, 409)
            return
        
        # Update status to processing
        conn.execute("UPDATE merge_queue SET status='dry_run' WHERE task_id=?", (task_id,))
        conn.commit()
        conn.close()
        
        # Broadcast SSE update
        sse.broadcast("merge_queue", {"task_id": task_id, "status": "dry_run", "action": "process"})
        
        _json_response(handler, {"ok": True, "task_id": task_id, "status": "dry_run"})
    except Exception as e:
        _json_response(handler, {"error": str(e)}, 500)


def api_cancel_merge(handler, task_id):
    """POST /api/merge-queue/:id/cancel — cancel a merge request."""
    try:
        db_path = os.path.join(str(_ws), "overdrop.db")
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        
        row = conn.execute("SELECT * FROM merge_queue WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            conn.close()
            _json_response(handler, {"error": "Not in queue"}, 404)
            return
        
        if row["status"] not in ("pending", "dry_run"):
            conn.close()
            _json_response(handler, {"error": f"Cannot cancel: status is {row['status']}"}, 409)
            return
        
        conn.execute("UPDATE merge_queue SET status='cancelled' WHERE task_id=?", (task_id,))
        conn.commit()
        conn.close()
        
        # Broadcast SSE update
        sse.broadcast("merge_queue", {"task_id": task_id, "status": "cancelled", "action": "cancel"})
        
        _json_response(handler, {"ok": True, "task_id": task_id})
    except Exception as e:
        _json_response(handler, {"error": str(e)}, 500)


def api_retry_merge(handler, task_id):
    """POST /api/merge-queue/:id/retry — retry a failed merge."""
    try:
        db_path = os.path.join(str(_ws), "overdrop.db")
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        
        row = conn.execute("SELECT * FROM merge_queue WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            conn.close()
            _json_response(handler, {"error": "Not in queue"}, 404)
            return
        
        if row["status"] not in ("conflict", "failed"):
            conn.close()
            _json_response(handler, {"error": f"Cannot retry: status is {row['status']}"}, 409)
            return
        
        conn.execute("UPDATE merge_queue SET status='pending', error_log=NULL WHERE task_id=?", (task_id,))
        conn.commit()
        conn.close()
        
        # Broadcast SSE update
        sse.broadcast("merge_queue", {"task_id": task_id, "status": "pending", "action": "retry"})
        
        _json_response(handler, {"ok": True, "task_id": task_id})
    except Exception as e:
        _json_response(handler, {"error": str(e)}, 500)


# ---- HTTP HANDLER ----

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        p = parsed.path.rstrip("/")
        params = parse_qs(parsed.query)

        if p in ("", "/"):
            self._html()
        elif p.startswith("/css/") or p.startswith("/js/"):
            self._static(p)
        elif p == "/events":
            self._sse()
        elif p == "/api/tasks":
            task_id = params.get("id", [None])[0]
            if task_id:
                api_task_details(self, task_id)
            else:
                api_list_all_tasks(self, params)
        elif p.startswith("/api/tasks/") and p.count("/") == 3:
            task_id = p.split("/")[3]
            api_task_details(self, task_id)
        elif p == "/api/agents":
            api_agents(self)
        elif p == "/api/models":
            api_models(self)
        elif p == "/api/merge-queue":
            api_merge_queue(self)
        elif p.startswith("/api/merge-queue/") and p.endswith("/trigger"):
            task_id = p.split("/")[3]
            api_trigger_merge(self, task_id)
        elif p.startswith("/api/merge-queue/") and p.endswith("/process"):
            task_id = p.split("/")[3]
            api_process_merge(self, task_id)
        elif p.startswith("/api/merge-queue/") and p.endswith("/cancel"):
            task_id = p.split("/")[3]
            api_cancel_merge(self, task_id)
        elif p.startswith("/api/merge-queue/") and p.endswith("/retry"):
            task_id = p.split("/")[3]
            api_retry_merge(self, task_id)
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urlparse(self.path)
        p = parsed.path.rstrip("/")
        body = _read_body(self)

        if p == "/api/tasks":
            api_create_task(self, body)
        elif "/claim" in p:
            task_id = p.split("/")[3]
            api_claim_task(self, body, task_id)
        elif "/done" in p:
            task_id = p.split("/")[3]
            api_complete_task(self, body, task_id)
        elif "/block" in p and "unblock" not in p:
            task_id = p.split("/")[3]
            api_block_task(self, body, task_id)
        elif "/unblock" in p:
            task_id = p.split("/")[3]
            api_unblock_task(self, body, task_id)
        elif "/fail" in p:
            task_id = p.split("/")[3]
            api_fail_task(self, body, task_id)
        elif p.startswith("/api/agents/") and p.endswith("/model"):
            agent_name = p.split("/")[3]
            api_update_agent_model(self, body, agent_name)
        elif p.startswith("/api/merge-queue/") and p.endswith("/trigger"):
            task_id = p.split("/")[3]
            api_trigger_merge(self, task_id)
        elif p.startswith("/api/merge-queue/") and p.endswith("/process"):
            task_id = p.split("/")[3]
            api_process_merge(self, task_id)
        elif p.startswith("/api/merge-queue/") and p.endswith("/cancel"):
            task_id = p.split("/")[3]
            api_cancel_merge(self, task_id)
        elif p.startswith("/api/merge-queue/") and p.endswith("/retry"):
            task_id = p.split("/")[3]
            api_retry_merge(self, task_id)
        else:
            self.send_error(404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        p = parsed.path.rstrip("/")
        if p.startswith("/api/tasks/"):
            task_id = p.split("/")[3]
            api_delete_task(self, {}, task_id)
        else:
            self.send_error(404)

    def _html(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(get_html().encode())

    def _static(self, path):
        """Serve static files from dashboard/ directory."""
        file_path = os.path.join(os.path.dirname(__file__), "..", "dashboard", path.lstrip("/"))
        if not os.path.exists(file_path):
            self.send_error(404)
            return
        content_type = "text/css" if path.endswith(".css") else "application/javascript"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(open(file_path, "rb").read())

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.flush()
        sse.add(self)
        try:
            while True:
                time.sleep(15)
                self.wfile.write(b": heartbeat\n\n")
                self.wfile.flush()
        except:
            pass
        finally:
            sse.remove(self)

def poller():
    """Background poller — broadcasts live state every 2s."""
    cycle = 0
    while True:
        cycle += 1
        try:
            fs = get_fs()
            st = {
                "inbox": len(fs.list_tasks("inbox")),
                "active": len(fs.list_tasks("active")),
                "done": len(fs.list_tasks("done")),
                "failed": len(fs.list_tasks("failed")),
                "blocked": len(fs.list_tasks("blocked")),
                "cycle": cycle,
            }
            sse.broadcast("stats", st)

            tasks = []
            for fld in ["active", "inbox", "done", "failed", "blocked", "feedback"]:
                for t in fs.list_tasks(fld)[:5]:
                    tasks.append({
                        "id": t.id[:8],
                        "full_id": t.id,
                        "title": t.title[:50],
                        "status": t.status.value,
                        "from": t.from_agent,
                        "assignee": t.assignee or "-",
                        "priority": t.priority,
                        "retry_count": t.retry_count,
                        "max_retries": t.max_retries,
                        "duration": "active" if t.status.value in ("active", "claimed") else "-",
                    })
            sse.broadcast("tasks", tasks)

            agents = {}
            for t in fs.list_tasks("active"):
                name = t.assignee or "unknown"
                agents[name] = {"name": name, "status": "thinking", "since": str(time.time()), "ttl": 10000}
            for name, info in agents.items():
                sse.broadcast("agents", info)

            try:
                bus = get_bus()
                msgs = bus.poll("@all", unread_only=True, limit=10)
                for m in msgs:
                    sse.broadcast("log", {
                        "time": m.created_at or "",
                        "actor": m.sender,
                        "type": m.type.value,
                        "msg": json.dumps(m.payload)[:80],
                    })
                    bus.mark_read(m.id)
            except:
                pass

        except Exception as e:
            print(f"Poller: {e}")
        time.sleep(2)

class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

def serve(workspace=None, port=7737):
    global _ws
    _ws = Path(workspace or ".overdrop").resolve()
    _ws.mkdir(parents=True, exist_ok=True)
    for f in ["inbox", "active", "done", "failed", "blocked", "feedback"]:
        (_ws / f).mkdir(exist_ok=True)
    threading.Thread(target=poller, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"⬡ OverDrop Dashboard → http://localhost:{port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n👋")
        srv.shutdown()

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("workspace", nargs="?", default=".overdrop")
    p.add_argument("--port", "-p", type=int, default=7737)
    a = p.parse_args()
    serve(a.workspace, a.port)
