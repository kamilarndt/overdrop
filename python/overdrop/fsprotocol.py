"""
OverDrop — Filesystem Protocol (DropSite-style)

Task state machine using filesystem folders + atomic os.rename().
Zero dependencies — any agent can participate by reading/writing JSON files.

Folders:
  inbox/     - new tasks (pending)
  active/    - claimed tasks (in progress)
  done/      - completed tasks
  failed/    - failed tasks (max_retries exhausted)
  blocked/   - tasks waiting on external dependencies
  feedback/  - tasks needing human/decision input
  
All operations are based on atomic os.rename() for POSIX safety.
"""

import json
import os
import uuid
import time
import glob
from datetime import datetime, timezone
from typing import Optional, Callable
from .types import Task, TaskStatus


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    return str(uuid.uuid4())


class FsProtocol:
    """Filesystem-based task state machine.
    
    Every task is a JSON file that moves through folders:
    inbox -> active -> done | failed | blocked | feedback
    
    os.rename() is atomic on POSIX = natural locking.
    """
    
    def __init__(self, workspace_dir: str):
        self.root = os.path.abspath(workspace_dir)
        self._ensure_dirs()
    
    def _ensure_dirs(self):
        """Create workspace directory structure."""
        for folder in ["inbox", "active", "done", "failed", "blocked", "feedback"]:
            os.makedirs(os.path.join(self.root, folder), exist_ok=True)
    
    # ---- WRITE OPERATIONS ----
    
    def submit(self, title: str, from_agent: str, assign: str = None,
               context: dict = None, priority: int = 5,
               max_retries: int = 3) -> str:
        """Submit a new task. Creates JSON file in inbox/."""
        task_id = _uuid()
        task = Task(
            id=task_id,
            title=title,
            status=TaskStatus.INBOX,
            from_agent=from_agent,
            assignee=assign,
            context=context or {},
            priority=priority,
            max_retries=max_retries,
            created_at=_now(),
        )
        self._write_task("inbox", task)
        return task_id
    
    def claim(self, agent: str, task_id: str) -> Optional[Task]:
        """Claim a task from inbox. Atomic os.rename() — first wins.
        
        Returns the Task if claimed, None if someone else got it first.
        """
        src = self._path("inbox", task_id)
        dst = self._path("active", task_id)
        try:
            os.rename(src, dst)
        except FileNotFoundError:
            return None  # someone else claimed it
        
        task = self._read_task(dst)
        task.assignee = agent
        task.status = TaskStatus.CLAIMED
        self._write_task("active", task)
        return task
    
    def complete(self, task_id: str, result: dict = None):
        """Mark a task as done."""
        task = self._move_task("active", "done", task_id)
        if task:
            task.status = TaskStatus.DONE
            task.result = result or {}
            self._write_task("done", task)
    
    def fail(self, task_id: str, error: str = None):
        """Mark a task as failed. Retry if max_retries not exhausted."""
        task = self._read_task(self._path("active", task_id))
        if not task:
            return
        
        task.retry_count += 1
        task.result = {**task.result, "error": error}
        
        if task.retry_count < task.max_retries:
            # Move back to inbox for retry
            self._move_task("active", "inbox", task_id)
            task.status = TaskStatus.INBOX
            self._write_task("inbox", task)
        else:
            self._move_task("active", "failed", task_id)
            task.status = TaskStatus.FAILED
            self._write_task("failed", task)
    
    def block(self, task_id: str, reason: str = None):
        """Block a task waiting on external dependency."""
        task = self._move_task("active", "blocked", task_id)
        if task:
            task.status = TaskStatus.BLOCKED
            task.result["blocked_reason"] = reason
            self._write_task("blocked", task)
    
    def unblock(self, task_id: str):
        """Move a blocked task back to inbox for retry."""
        self._move_task("blocked", "inbox", task_id)
        task = self._read_task(self._path("inbox", task_id))
        if task:
            task.status = TaskStatus.INBOX
            self._write_task("inbox", task)
    
    def request_feedback(self, task_id: str, question: str = None,
                         options: list = None):
        """Request human/decision feedback. Task goes to feedback/."""
        task = self._move_task("active", "feedback", task_id)
        if task:
            task.status = TaskStatus.NEEDS_DECISION
            task.result["question"] = question
            task.result["options"] = options or []
            self._write_task("feedback", task)
    
    def provide_feedback(self, task_id: str, decision: str):
        """Provide feedback — move task back to active."""
        self._move_task("feedback", "active", task_id)
        task = self._read_task(self._path("active", task_id))
        if task:
            task.result["decision"] = decision
            task.status = TaskStatus.ACTIVE
            self._write_task("active", task)
    
    # ---- READ OPERATIONS ----
    
    def list_tasks(self, folder: str, limit: int = 100) -> list[Task]:
        """List tasks in a specific folder, newest first."""
        pattern = os.path.join(self.root, folder, "*.json")
        files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)[:limit]
        tasks = []
        for f in files:
            t = self._read_task(f)
            if t:
                tasks.append(t)
        return tasks
    
    def get_task(self, task_id: str) -> Optional[Task]:
        """Find a task by ID across all folders."""
        for folder in ["inbox", "active", "done", "failed", "blocked", "feedback"]:
            task = self._read_task(self._path(folder, task_id))
            if task:
                return task
        return None
    
    def reap_stale(self, timeout_s: int = 300) -> list[str]:
        """Move stuck active tasks back to inbox.
        
        If an agent crashes mid-task, the file stays in active/.
        This reaper moves it back to inbox after timeout.
        """
        reaped = []
        now = time.time()
        for f in glob.glob(os.path.join(self.root, "active", "*.json")):
            if now - os.path.getmtime(f) > timeout_s:
                task_id = os.path.splitext(os.path.basename(f))[0]
                try:
                    os.rename(f, os.path.join(self.root, "inbox", f"{task_id}.json"))
                    reaped.append(task_id)
                except FileNotFoundError:
                    pass
        return reaped
    
    # ---- INTERNAL ----
    
    def _path(self, folder: str, task_id: str) -> str:
        return os.path.join(self.root, folder, f"{task_id}.json")
    
    def _write_task(self, folder: str, task: Task):
        path = self._path(folder, task.id)
        data = {
            "id": task.id,
            "title": task.title,
            "status": task.status.value,
            "from_agent": task.from_agent,
            "assignee": task.assignee,
            "context": task.context,
            "result": task.result,
            "priority": task.priority,
            "max_retries": task.max_retries,
            "retry_count": task.retry_count,
            "parent_task": task.parent_task,
            "group_id": task.group_id,
            "worktree": task.worktree,
            "version": task.version,
            "created_at": task.created_at or _now(),
        }
        # Atomic write: write .tmp then os.replace (atomic on POSIX)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp, path)
    
    def _read_task(self, path: str) -> Optional[Task]:
        try:
            with open(path) as f:
                data = json.load(f)
            return Task(
                id=data["id"],
                title=data.get("title", ""),
                status=TaskStatus(data.get("status", "inbox")),
                from_agent=data.get("from_agent", ""),
                assignee=data.get("assignee"),
                context=data.get("context", {}),
                result=data.get("result", {}),
                priority=data.get("priority", 5),
                max_retries=data.get("max_retries", 3),
                retry_count=data.get("retry_count", 0),
                parent_task=data.get("parent_task"),
                group_id=data.get("group_id"),
                worktree=data.get("worktree"),
                version=data.get("version", 1),
                created_at=data.get("created_at"),
            )
        except (FileNotFoundError, json.JSONDecodeError):
            return None
    
    def _move_task(self, src_folder: str, dst_folder: str,
                   task_id: str) -> Optional[Task]:
        """Move a task file between folders atomically."""
        src = self._path(src_folder, task_id)
        dst = self._path(dst_folder, task_id)
        try:
            os.rename(src, dst)
            return self._read_task(dst)
        except FileNotFoundError:
            return None
