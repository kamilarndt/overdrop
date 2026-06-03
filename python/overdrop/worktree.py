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
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable

from .mailbus import MailBus
from .types import MessageType

logger = logging.getLogger("overdrop.worktree")


@dataclass
class MergeRequest:
    """A request to merge a worktree back to the main repo."""
    task_id: str
    branch: str
    worktree_path: str
    agent_id: str
    priority: int = 5
    status: str = "pending"  # pending | dry_run | resolving | merged | failed
    conflict_level: int = 0  # 0=auto, 1=simple, 2=ai, 3=human
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
        
        # Use custom root or temp dir
        if worktree_root:
            self._wt_root = Path(worktree_root)
        else:
            wt_root = tempfile.gettempdir()
            self._wt_root = Path(wt_root) / "overdrop-worktrees"
        
        if not (self.repo / ".git").exists():
            raise ValueError(f"Not a git repository: {self.repo}")
    
    def create(self, task_id: str, agent_id: str) -> str:
        """Create an isolated git worktree for a task.
        
        Returns the worktree path.
        """
        branch_name = f"od/{agent_id}/{task_id[:8]}"
        wt_name = f"od-{task_id[:8]}-{agent_id}"
        
        # Use configured worktree root
        worktree_root = self._wt_root
        worktree_root.mkdir(exist_ok=True)
        wt_path = worktree_root / wt_name
        
        # Check if branch already exists
        result = subprocess.run(
            ["git", "branch", "--list", branch_name],
            cwd=self.repo, capture_output=True, text=True,
        )
        
        if branch_name in result.stdout:
            # Branch exists — reuse
            logger.info(f"Reusing existing worktree: {branch_name}")
            if wt_path.exists():
                self._worktrees[task_id] = str(wt_path)
                return str(wt_path)
        
        # Create branch from HEAD of base
        subprocess.run(
            ["git", "branch", branch_name, self.base_branch],
            cwd=self.repo, check=True, capture_output=True, text=True,
        )
        
        # Create worktree
        subprocess.run(
            ["git", "worktree", "add", str(wt_path), branch_name],
            cwd=self.repo, check=True, capture_output=True, text=True,
        )
        
        self._worktrees[task_id] = str(wt_path)
        logger.info(f"Worktree created: {wt_path} (branch: {branch_name})")
        return str(wt_path)
    
    def get_path(self, task_id: str) -> Optional[str]:
        """Get the worktree path for a task."""
        return self._worktrees.get(task_id)
    
    def remove(self, task_id: str):
        """Remove a worktree and its branch after merge completion."""
        wt_path = self._worktrees.pop(task_id, None)
        if not wt_path or not os.path.exists(wt_path):
            return
        
        # Remove worktree (git worktree remove handles branch cleanup)
        subprocess.run(
            ["git", "worktree", "remove", "--force", wt_path],
            cwd=self.repo, capture_output=True, text=True,
        )
        
        # Clean up directory if still exists
        try:
            shutil.rmtree(wt_path, ignore_errors=True)
        except:
            pass
        
        logger.info(f"Worktree cleaned: {task_id}")
    
    def list_all(self) -> dict[str, str]:
        """List all active worktrees."""
        return dict(self._worktrees)
    
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
    2. AI-assisted resolution (LLM analyzes conflict) — complex cases
    3. Human escalation (manual resolution) — unresolvable
    
    Backed by SQLite for crash safety.
    """
    
    def __init__(self, repo_path: str, base_branch: str = "main",
                 db_path: str = None,
                 ai_resolver: Optional[Callable] = None):
        self.repo = Path(repo_path).resolve()
        self.base_branch = base_branch
        self.bus = MailBus(db_path or f"{repo_path}/.overdrop/overdrop.db")
        self.bus.connect()
        self.ai_resolver = ai_resolver
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
            """INSERT OR REPLACE INTO merge_queue (task_id, branch, worktree, agent_id, priority, status)
               VALUES (?, ?, ?, ?, ?, 'pending')""",
            (task_id, branch, worktree_path, agent_id, priority),
        )
        self.bus._conn.commit()
        
        self._queue.append(req)
        self._queue.sort(key=lambda r: (r.priority, r.created_at))  # 1=highest
        
        logger.info(f"Enqueued merge: {task_id[:8]} ({agent_id})")
        return req
    
    async def process_next(self) -> Optional[MergeRequest]:
        """Process the next item in the merge queue (FIFO with priority)."""
        if not self._queue:
            self._load_from_db()
            if not self._queue:
                return None
        
        req = self._queue.pop(0)
        logger.info(f"Processing merge: {req.task_id[:8]}")
        
        # 1. Dry-run
        success, error = self._dry_run_merge(req)
        if success:
            logger.info(f"Auto-merge successful: {req.task_id[:8]}")
            req.status = "merged"
            self._update_db(req)
            return req
        
        # 2. Check conflict complexity
        conflict_level = self._analyze_conflict(error)
        req.conflict_level = conflict_level
        req.error = error
        
        if conflict_level <= 1:
            # Simple conflict — try AI resolution
            if self.ai_resolver:
                logger.info(f"Attempting AI resolution: {req.task_id[:8]}")
                resolved = await self.ai_resolver(req, error)
                if resolved:
                    req.status = "merged"
                    self._update_db(req)
                    return req
            
            # AI failed or not available
            req.status = "conflict"
            req.conflict_level = 3  # escalate to human
            self._update_db(req)
            return req
        
        # 3. Complex conflict — escalate
        req.status = "conflict"
        self._update_db(req)
        return req
    
    def _dry_run_merge(self, req: MergeRequest) -> tuple[bool, Optional[str]]:
        """Attempt to merge the worktree branch into base.
        
        Returns (success, error_message).
        """
        try:
            # First, checkout base branch
            subprocess.run(
                ["git", "checkout", self.base_branch],
                cwd=self.repo, check=True, capture_output=True, text=True,
            )
            
            # Pull only if remote exists
            result = subprocess.run(
                ["git", "remote"],
                cwd=self.repo, capture_output=True, text=True,
            )
            if result.stdout.strip():
                subprocess.run(
                    ["git", "pull", "origin", self.base_branch],
                    cwd=self.repo, check=True, capture_output=True, text=True,
                )
            
            # Attempt merge
            result = subprocess.run(
                ["git", "merge", "--no-ff", "--no-commit", req.branch],
                cwd=self.repo, capture_output=True, text=True,
            )
            
            if result.returncode == 0:
                # Success
                subprocess.run(
                    ["git", "commit", "-m", f"Merge: {req.branch} (task {req.task_id[:8]})"],
                    cwd=self.repo, check=True, capture_output=True, text=True,
                )
                return True, None
            else:
                # Conflict
                subprocess.run(
                    ["git", "merge", "--abort"],
                    cwd=self.repo, capture_output=True,
                )
                return False, result.stderr[:2000]
                
        except subprocess.CalledProcessError as e:
            return False, str(e)
    
    def _analyze_conflict(self, error: str) -> int:
        """Analyze conflict severity.
        
        Returns 0-3:
          0 = auto-resolved (no conflict)
          1 = simple (few files, same area — AI can handle)
          2 = moderate (multiple files — AI may struggle)
          3 = complex (many files, semantic conflict — human needed)
        """
        if not error:
            return 0
        
        lines = error.count("\n")
        conflict_files = error.count("CONFLICT")
        
        if conflict_files <= 2:
            return 1
        elif conflict_files <= 5:
            return 2
        else:
            return 3
    
    def _load_from_db(self):
        """Load pending merge requests from SQLite."""
        rows = self.bus._conn.execute(
            "SELECT * FROM merge_queue WHERE status='pending' ORDER BY priority DESC, created_at ASC"
        ).fetchall()
        
        for row in rows:
            self._queue.append(MergeRequest(
                task_id=row["task_id"],
                branch=row["branch"],
                worktree_path=row["worktree"],
                agent_id=row["agent_id"],
                priority=row["priority"],
                status=row["status"],
                conflict_level=row.get("conflict_level", 0),
                error=row.get("error_log"),
                created_at=row["created_at"],
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
    
    def get_status(self, task_id: str) -> Optional[dict]:
        """Get merge status for a task."""
        row = self.bus._conn.execute(
            "SELECT * FROM merge_queue WHERE task_id=?", (task_id,)
        ).fetchone()
        if not row:
            return None
        return dict(row)
    
    def close(self):
        self.bus.close()
