"""
OverDrop — Git Worktrees + Merge Queue

Isolated worktree per task so agents never conflict on the same filesystem.
FIFO merge queue with conflict resolution pipeline.

Workflow:
1. Agent claims task → worktree created automatically
2. Agent works in isolated worktree
3. Agent completes work → moves to MERGE_READY
4. Merge queue processes FIFO: dry-run → resolve → merge → cleanup
5. Conflicts resolved: auto → AI-assisted → human

Uses git directly for worktree operations.
"""

import logging
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable

from .mailbus import MailBus
from .types import MessageType

logger = logging.getLogger("overdrop.worktree")


@dataclass
class MergeRequest:
    """A request to merge a worktree back into the main repo."""
    task_id: str
    branch: str
    worktree_path: str
    agent_id: str
    priority: int = 5
    status: str = "pending"  # pending | dry_run | resolving | merged | conflict | failed | cancelled
    conflict_level: int = 0  # 0=auto, 1=simple, 2=moderate, 3=complex
    error: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class WorktreeManager:
    """Manages git worktrees for isolated agent work.

    Each task gets its own worktree automatically.
    """

    def __init__(self, repo_path: str, base_branch: str = "main", worktree_root: str = None):
        self.repo = Path(repo_path).resolve()
        self.base_branch = base_branch
        self._worktrees: dict[str, str] = {}  # task_id → worktree_path

        if worktree_root:
            self._wt_root = Path(worktree_root)
        else:
            self._wt_root = Path(tempfile.gettempdir()) / "overdrop-worktrees"

        if not (self.repo / ".git").exists():
            raise ValueError(f"Not a git repository: {self.repo}")

    def create(self, task_id: str, agent_id: str) -> str:
        """Create an isolated git worktree for a task.

        Returns the worktree path.
        """
        branch_name = f"od/{agent_id}/{task_id[:8]}"
        wt_name = f"od-{task_id[:8]}-{agent_id}"
        self._wt_root.mkdir(exist_ok=True)
        wt_path = self._wt_root / wt_name

        # Check if branch already exists
        result = subprocess.run(
            ["git", "branch", "--list", branch_name],
            cwd=self.repo, capture_output=True, text=True,
        )
        if branch_name in result.stdout:
            logger.info(f"Reusing existing worktree: {branch_name}")
            if wt_path.exists():
                self._worktrees[task_id] = str(wt_path)
                return str(wt_path)

        # Create branch from HEAD of base
        r = subprocess.run(
            ["git", "branch", branch_name, self.base_branch],
            cwd=self.repo, capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"git branch failed: {r.stderr}")

        # Create worktree
        r = subprocess.run(
            ["git", "worktree", "add", str(wt_path), branch_name],
            cwd=self.repo, capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(f"git worktree add failed: {r.stderr}")

        self._worktrees[task_id] = str(wt_path)
        logger.info(f"Worktree created: {wt_path} (branch: {branch_name})")
        return str(wt_path)

    def get_path(self, task_id: str) -> Optional[str]:
        """Get the worktree path for a task."""
        return self._worktrees.get(task_id)

    def remove(self, task_id: str):
        """Remove a worktree and its branch after merge completion."""
        wt_path = self._worktrees.pop(task_id, None)
        if not wt_path:
            return

        # Remove worktree via git
        subprocess.run(
            ["git", "worktree", "remove", "--force", str(wt_path)],
            cwd=self.repo, capture_output=True, text=True,
        )

        # Fallback: remove directory if still exists
        if os.path.exists(wt_path):
            try:
                shutil.rmtree(wt_path, ignore_errors=True)
            except OSError:
                pass

        logger.info(f"Worktree removed: {task_id}")

    def list_all(self) -> dict[str, str]:
        """List all active worktrees."""
        return dict(self._worktrees)

    def cleanup_stale_worktrees(self, timeout_s: int = 3600) -> list[str]:
        """Remove worktrees older than timeout (orphaned by crashes).

        Returns list of removed task_ids.
        """
        removed = []
        now = time.time()

        for task_id, wt_path in list(self._worktrees.items()):
            if not os.path.exists(wt_path):
                self._worktrees.pop(task_id, None)
                removed.append(task_id)
                continue

            try:
                age = now - os.path.getmtime(wt_path)
                if age > timeout_s:
                    logger.warning(f"Stale worktree: {task_id} ({age:.0f}s old)")
                    self.remove(task_id)
                    removed.append(task_id)
            except OSError:
                pass

        if removed:
            logger.info(f"Cleaned {len(removed)} stale worktrees")
        return removed

    def commit_changes(self, task_id: str, message: str, agent_id: str):
        """Commit all changes in a worktree."""
        wt_path = self._worktrees.get(task_id)
        if not wt_path:
            raise ValueError(f"No worktree for task {task_id}")

        subprocess.run(
            ["git", "add", "-A"],
            cwd=wt_path, check=True, capture_output=True, text=True,
        )

        author = f"{agent_id} <overdrop@agents.local>"
        subprocess.run(
            ["git", "commit", "--author", author, "-m", message],
            cwd=wt_path, check=True, capture_output=True, text=True,
        )

        logger.info(f"Changes committed in {task_id}: {message[:50]}...")


class MergeQueue:
    """FIFO merge queue for git worktrees.

    Conflict resolution pipeline:
    1. Auto-merge (git merge) — most cases
    2. Tier 1: Simple rebase for few-file conflicts
    3. Tier 2: AI-assisted resolution (LLM callback)
    4. Tier 3: Human escalation (manual resolution)

    Backed by SQLite for crash safety.
    """

    def __init__(self, repo_path: str, base_branch: str = "main",
                 db_path: str = None,
                 ai_resolver: Optional[Callable] = None,
                 fs=None):
        self.repo = Path(repo_path).resolve()
        self.base_branch = base_branch
        self.bus = MailBus(db_path or f"{repo_path}/.overdrop/overdrop.db")
        self.bus.connect()
        self.ai_resolver = ai_resolver
        self._fs = fs  # Optional FsProtocol for task status updates
        self._queue: list[MergeRequest] = []
        self._init_db()

    def _init_db(self):
        """Ensure merge_queue table exists."""
        self.bus._conn.executescript("""
            CREATE TABLE IF NOT EXISTS merge_queue (
                task_id     TEXT PRIMARY KEY,
                branch      TEXT NOT NULL,
                worktree    TEXT NOT NULL,
                agent_id    TEXT NOT NULL,
                priority    INTEGER DEFAULT 5,
                status      TEXT DEFAULT 'pending',
                conflict_level INTEGER DEFAULT 0,
                error_log   TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                merged_at   TEXT
            );
        """)

    def enqueue(self, task_id: str, branch: str, worktree_path: str,
                agent_id: str, priority: int = 5) -> MergeRequest:
        """Add a worktree to the merge queue."""
        req = MergeRequest(
            task_id=task_id,
            branch=branch,
            worktree_path=worktree_path,
            agent_id=agent_id,
            priority=priority,
        )

        self.bus._conn.execute(
            """INSERT OR REPLACE INTO merge_queue
               (task_id, branch, worktree, agent_id, priority, status)
               VALUES (?, ?, ?, ?, ?, 'pending')""",
            (task_id, branch, worktree_path, agent_id, priority),
        )
        self.bus._conn.commit()

        self._queue.append(req)
        self._queue.sort(key=lambda r: (r.priority, r.created_at))

        logger.info(f"Enqueued merge: {task_id[:8]} ({agent_id})")
        return req

    def process_next(self) -> Optional[MergeRequest]:
        """Process the next item in the merge queue (FIFO with priority).

        Full pipeline:
        1. Dry-run merge (git merge --no-commit --no-ff)
        2. If clean → auto-commit (Tier 0)
        3. If conflicts → analyze severity
           - Tier 1 (≤2 files): try rebase
           - Tier 2 (≤5 files): AI resolver callback
           - Tier 3 (>5 files): escalate to human
        4. On success: update FsProtocol, cleanup worktree
        5. On failure: mark failed, cleanup worktree
        """
        if not self._queue:
            self._load_from_db()
            if not self._queue:
                return None

        req = self._queue.pop(0)
        logger.info(f"Processing merge: {req.task_id[:8]} ({req.branch})")

        try:
            # Step 1: Dry-run merge
            req.status = "dry_run"
            self._update_db(req)

            success, error = self._dry_run_merge(req)

            if success:
                # Tier 0: Auto-merge succeeded
                logger.info(f"Auto-merge successful: {req.task_id[:8]}")
                req.status = "merged"
                self._update_db(req)
                self._cleanup_after_merge(req, success=True)
                self._notify("merge_completed", req)
                return req

            # Step 2: Analyze conflict
            conflict_level = self._analyze_conflict(error)
            req.conflict_level = conflict_level
            req.error = error
            logger.info(f"Conflict level {conflict_level}: {req.task_id[:8]}")

            # Step 3: Resolve based on level
            resolved = self._resolve_conflict(req, error)

            if resolved:
                req.status = "merged"
                self._update_db(req)
                self._cleanup_after_merge(req, success=True)
                self._notify("merge_completed", req)
            else:
                req.status = "conflict"
                self._update_db(req)
                self._notify("merge_failed", req)

        except Exception as e:
            logger.error(f"Merge processing failed: {req.task_id[:8]}: {e}", exc_info=True)
            req.status = "failed"
            req.error = str(e)
            self._update_db(req)
            self._cleanup_after_merge(req, success=False)
            self._notify("merge_failed", req)

        return req

    def cleanup_after_merge(self, task_id: str, success: bool):
        """Public method: cleanup worktree after merge operation.

        Args:
            task_id: Task to cleanup
            success: Whether merge succeeded
        """
        # Find the request in queue or DB
        req = None
        for r in self._queue:
            if r.task_id == task_id:
                req = r
                break

        if not req:
            row = self.bus._conn.execute(
                "SELECT * FROM merge_queue WHERE task_id=?", (task_id,)
            ).fetchone()
            if row:
                r = dict(row)
                req = MergeRequest(
                    task_id=r["task_id"], branch=r["branch"],
                    worktree_path=r["worktree"], agent_id=r["agent_id"],
                    priority=r["priority"], status=r["status"],
                )

        if req:
            self._cleanup_after_merge(req, success)

    def cancel(self, task_id: str) -> bool:
        """Cancel a pending merge request."""
        self._queue = [r for r in self._queue if r.task_id != task_id]

        row = self.bus._conn.execute(
            "SELECT status FROM merge_queue WHERE task_id=?", (task_id,)
        ).fetchone()
        if not row:
            return False
        if row["status"] not in ("pending", "dry_run"):
            return False

        self.bus._conn.execute(
            "UPDATE merge_queue SET status='cancelled' WHERE task_id=?", (task_id,)
        )
        self.bus._conn.commit()
        logger.info(f"Cancelled merge: {task_id[:8]}")
        return True

    def retry(self, task_id: str) -> bool:
        """Retry a failed or conflicted merge request."""
        row = self.bus._conn.execute(
            "SELECT status FROM merge_queue WHERE task_id=?", (task_id,)
        ).fetchone()
        if not row:
            return False
        if row["status"] not in ("conflict", "failed"):
            return False

        self.bus._conn.execute(
            "UPDATE merge_queue SET status='pending', error_log=NULL WHERE task_id=?",
            (task_id,),
        )
        self.bus._conn.commit()
        self._load_from_db()
        logger.info(f"Retried merge: {task_id[:8]}")
        return True

    def list_all(self) -> list[dict]:
        """List all merge requests (any status)."""
        rows = self.bus._conn.execute(
            "SELECT * FROM merge_queue ORDER BY priority DESC, created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def list_pending(self) -> list[dict]:
        """List all pending merge requests."""
        rows = self.bus._conn.execute(
            "SELECT * FROM merge_queue WHERE status='pending' ORDER BY priority DESC, created_at ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_status(self, task_id: str) -> Optional[dict]:
        """Get merge status for a task."""
        row = self.bus._conn.execute(
            "SELECT * FROM merge_queue WHERE task_id=?", (task_id,)
        ).fetchone()
        if not row:
            return None
        return dict(row)

    # ── Private: Conflict Resolution ──────────────────────────────────

    def _resolve_conflict(self, req: MergeRequest, error: str) -> bool:
        """Resolve conflict based on severity level.

        Levels:
        0 = auto-resolved (handled by dry-run)
        1 = simple (≤2 files) → Tier 1: rebase
        2 = moderate (≤5 files) → Tier 2: AI resolver
        3 = complex (>5 files) → Tier 3: human escalation
        """
        if req.conflict_level == 0:
            return True

        # Tier 1: Simple conflict — try rebase
        if req.conflict_level == 1:
            logger.info(f"Tier 1 resolution (rebase): {req.task_id[:8]}")
            if self._try_rebase(req):
                return True

        # Tier 2: Moderate — AI resolution
        if req.conflict_level <= 2 and self.ai_resolver:
            logger.info(f"Tier 2 resolution (AI): {req.task_id[:8]}")
            try:
                req.status = "resolving"
                self._update_db(req)

                resolved = self.ai_resolver(req, error)
                if resolved:
                    logger.info(f"AI resolved conflict: {req.task_id[:8]}")
                    success, _ = self._dry_run_merge(req)
                    return success
            except Exception as e:
                logger.error(f"AI resolver failed: {e}")

        # Tier 3: Human escalation
        logger.warning(f"Escalating to human: {req.task_id[:8]} (level {req.conflict_level})")
        return False

    def _try_rebase(self, req: MergeRequest) -> bool:
        """Try to rebase the worktree branch onto base (Tier 1 resolution)."""
        try:
            # Check if remote exists
            r = subprocess.run(
                ["git", "remote"],
                cwd=self.repo, capture_output=True, text=True,
            )
            has_remote = bool(r.stdout.strip())

            if has_remote:
                subprocess.run(
                    ["git", "fetch", "origin", self.base_branch],
                    cwd=self.repo, capture_output=True, text=True,
                )
                base_ref = f"origin/{self.base_branch}"
            else:
                base_ref = self.base_branch

            # Attempt rebase
            result = subprocess.run(
                ["git", "rebase", base_ref, req.branch],
                cwd=self.repo, capture_output=True, text=True,
            )

            if result.returncode == 0:
                # Rebase succeeded — now merge
                subprocess.run(
                    ["git", "checkout", self.base_branch],
                    cwd=self.repo, capture_output=True, text=True,
                )
                result = subprocess.run(
                    ["git", "merge", "--no-ff", req.branch],
                    cwd=self.repo, capture_output=True, text=True,
                )
                if result.returncode == 0:
                    logger.info(f"Rebase + merge succeeded: {req.task_id[:8]}")
                    return True

            # Rebase failed — abort
            subprocess.run(
                ["git", "rebase", "--abort"],
                cwd=self.repo, capture_output=True,
            )
            return False

        except Exception as e:
            logger.error(f"Rebase failed: {e}")
            subprocess.run(
                ["git", "rebase", "--abort"],
                cwd=self.repo, capture_output=True,
            )
            return False

    def _dry_run_merge(self, req: MergeRequest) -> tuple[bool, Optional[str]]:
        """Attempt to merge the worktree branch into base.

        Returns (success, error_message).
        """
        try:
            # Checkout base branch (don't raise on failure)
            r = subprocess.run(
                ["git", "checkout", self.base_branch],
                cwd=self.repo, capture_output=True, text=True,
            )
            if r.returncode != 0:
                return False, f"checkout failed: {r.stderr}"

            # Pull only if remote exists
            r = subprocess.run(
                ["git", "remote"],
                cwd=self.repo, capture_output=True, text=True,
            )
            if r.stdout.strip():
                subprocess.run(
                    ["git", "pull", "origin", self.base_branch],
                    cwd=self.repo, capture_output=True, text=True,
                )

            # Attempt merge
            result = subprocess.run(
                ["git", "merge", "--no-ff", "--no-commit", req.branch],
                cwd=self.repo, capture_output=True, text=True,
            )

            if result.returncode == 0:
                # Success — commit
                r = subprocess.run(
                    ["git", "commit", "-m",
                     f"Merge: {req.branch} (task {req.task_id[:8]})"],
                    cwd=self.repo, capture_output=True, text=True,
                )
                if r.returncode != 0:
                    return False, f"commit failed: {r.stderr}"
                return True, None
            else:
                # Conflict — abort
                subprocess.run(
                    ["git", "merge", "--abort"],
                    cwd=self.repo, capture_output=True,
                )
                error_msg = result.stderr or result.stdout or "Merge conflict (no details)"
                return False, error_msg[:2000]

        except subprocess.CalledProcessError as e:
            logger.error(f"git command failed: {e}")
            return False, str(e)

    def _analyze_conflict(self, error: str) -> int:
        """Analyze conflict severity.

        Returns 0-3:
          0 = auto-resolved (no conflict)
          1 = simple (≤2 files)
          2 = moderate (≤5 files)
          3 = complex (>5 files)
        """
        if not error:
            return 0

        conflict_files = error.count("CONFLICT")

        if conflict_files <= 2:
            return 1
        elif conflict_files <= 5:
            return 2
        else:
            return 3

    # ── Private: Cleanup & Notifications ──────────────────────────────

    def _cleanup_after_merge(self, req: MergeRequest, success: bool):
        """Cleanup worktree and update task status after merge operation."""
        # Remove worktree directory
        wt_path = req.worktree_path
        if os.path.exists(wt_path):
            try:
                shutil.rmtree(wt_path, ignore_errors=True)
                logger.info(f"Worktree cleaned: {req.task_id[:8]}")
            except OSError as e:
                logger.error(f"Failed to clean worktree: {e}")

        # Update task status in FsProtocol if available
        if self._fs:
            try:
                task = self._fs._read_task(self._fs._path("active", req.task_id))
                if not task:
                    task = self._fs._read_task(self._fs._path("done", req.task_id))
                if task:
                    if success:
                        task.status = "done"
                        task.result = {
                            **task.result,
                            "merged": True,
                            "branch": req.branch,
                        }
                    else:
                        task.status = "failed"
                        task.result = {
                            **task.result,
                            "merged": False,
                            "error": req.error,
                        }
                    self._fs._write_task("done" if success else "failed", task)
            except Exception as e:
                logger.error(f"Failed to update task status: {e}")

    def _notify(self, event: str, req: MergeRequest):
        """Send notification via MailBus."""
        msg_type = MessageType.BROADCAST if event == "merge_completed" else MessageType.ESCALATE
        recipient = "@all" if event == "merge_completed" else req.agent_id

        self.bus.send(
            msg_type, sender="merge-queue", recipient=recipient,
            payload={
                "event": event,
                "task_id": req.task_id,
                "branch": req.branch,
                "conflict_level": req.conflict_level,
                "error": (req.error[:500] if req.error else None),
            },
        )

    # ── Private: DB Operations ────────────────────────────────────────

    def _load_from_db(self):
        """Load pending merge requests from SQLite."""
        rows = self.bus._conn.execute(
            "SELECT * FROM merge_queue WHERE status='pending' "
            "ORDER BY priority DESC, created_at ASC"
        ).fetchall()

        for row in rows:
            r = dict(row)
            self._queue.append(MergeRequest(
                task_id=r["task_id"],
                branch=r["branch"],
                worktree_path=r["worktree"],
                agent_id=r["agent_id"],
                priority=r["priority"],
                status=r["status"],
                conflict_level=r.get("conflict_level", 0),
                error=r.get("error_log"),
                created_at=r["created_at"],
            ))

    def _update_db(self, req: MergeRequest):
        """Update merge request status in SQLite."""
        self.bus._conn.execute(
            """UPDATE merge_queue
               SET status=?, conflict_level=?, error_log=?
               WHERE task_id=?""",
            (req.status, req.conflict_level, req.error, req.task_id),
        )
        if req.status == "merged":
            self.bus._conn.execute(
                "UPDATE merge_queue SET merged_at=datetime('now') WHERE task_id=?",
                (req.task_id,),
            )
        self.bus._conn.commit()

    def close(self):
        self.bus.close()
