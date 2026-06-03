"""
OverDrop — DAG Orchestrator

Executes tasks respecting DAG dependencies.
- needs: dependency requires SUCCESS
- after: dependency requires only COMPLETION (even failure)

Uses SQLite dag_edges table + FsProtocol for task state.
"""

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Callable, Awaitable

from overdrop import FsProtocol, MailBus
from overdrop.types import Task, TaskStatus, MessageType

logger = logging.getLogger("overdrop.dag")


class DagOrchestrator:
    """Execute a DAG of tasks respecting dependencies."""
    
    def __init__(self, fs: FsProtocol, bus: MailBus):
        self.fs = fs
        self.bus = bus
        self._running = False
        self._handlers: dict[str, Callable[[Task], Awaitable[dict]]] = {}
        
        # Ensure dag_edges table exists
        self.bus._conn.executescript("""
            CREATE TABLE IF NOT EXISTS dag_edges (
                from_task TEXT NOT NULL,
                to_task TEXT NOT NULL,
                dep_type TEXT NOT NULL DEFAULT 'needs',
                PRIMARY KEY (from_task, to_task)
            );
        """)
        self.bus._conn.commit()
    
    def add_edge(self, from_task: str, to_task: str, dep_type: str = "needs"):
        """Add a DAG edge between two tasks.
        
        Args:
            from_task: upstream task (must complete first)
            to_task: downstream task (waits for from_task)
            dep_type: 'needs' (requires success) or 'after' (any completion)
        """
        self.bus._conn.execute(
            """INSERT OR IGNORE INTO dag_edges (from_task, to_task, dep_type)
               VALUES (?, ?, ?)""",
            (from_task, to_task, dep_type),
        )
        self.bus._conn.commit()
    
    def can_execute(self, task_id: str) -> bool:
        """Check if all upstream dependencies are satisfied."""
        deps = self.bus._conn.execute(
            """SELECT from_task, dep_type FROM dag_edges WHERE to_task=?""",
            (task_id,),
        ).fetchall()
        
        for row in deps:
            upstream = self.fs.get_task(row["from_task"])
            if not upstream:
                continue
            
            dep_type = row["dep_type"]
            if dep_type == "needs":
                # Must be DONE
                if upstream.status != TaskStatus.DONE:
                    return False
            elif dep_type == "after":
                # Must be in any terminal state (not actively running)
                active_states = (TaskStatus.INBOX, TaskStatus.CLAIMED,
                                TaskStatus.ACTIVE, TaskStatus.BLOCKED,
                                TaskStatus.PENDING_DEP)
                if upstream.status in active_states:
                    return False
        
        return True
    
    def get_blocked_tasks(self) -> list[str]:
        """Get task IDs that are blocked on dependencies."""
        blocked = []
        inbox = self.fs.list_tasks("inbox")
        for task in inbox:
            if not self.can_execute(task.id):
                blocked.append(task.id)
        return blocked
    
    async def run(self, entry_task_ids: list[str], agent_name: str = "orchestrator"):
        """Execute a DAG starting from entry tasks.
        
        Entry tasks must have no upstream dependencies.
        Other tasks execute as their dependencies are satisfied.
        """
        self._running = True
        
        # Mark all non-entry tasks as pending_dependencies
        all_tasks = self._collect_dag(entry_task_ids)
        for tid in all_tasks:
            if tid not in entry_task_ids:
                task = self.fs.get_task(tid)
                if task and task.status == TaskStatus.INBOX:
                    task.status = TaskStatus.PENDING_DEP
                    self.fs._write_task("inbox", task)
        
        # Submit entry tasks
        for tid in entry_task_ids:
            self.bus.send(MessageType.DISPATCH, agent_name,
                         "@workers", {"task_id": tid}, task_id=tid)
        
        # Monitor loop
        while self._running:
            # Check all tasks in inbox — unblock if deps met
            inbox = self.fs.list_tasks("inbox")
            for task in inbox:
                if task.status == TaskStatus.PENDING_DEP and self.can_execute(task.id):
                    task.status = TaskStatus.INBOX
                    self.fs._write_task("inbox", task)
                    self.bus.send(MessageType.DISPATCH, agent_name,
                                 "@workers", {"task_id": task.id}, task_id=task.id)
                    logger.info(f"DAG: unblocked task {task.id[:8]} ({task.title[:30]})")
            
            # Check if all done
            all_done = True
            for tid in all_tasks:
                t = self.fs.get_task(tid)
                if t and t.status not in (TaskStatus.DONE, TaskStatus.FAILED):
                    all_done = False
                    break
            
            if all_done:
                logger.info("DAG: all tasks completed")
                self._running = False
                break
            
            await asyncio.sleep(2.0)
    
    def _collect_dag(self, entry_ids: list[str]) -> set[str]:
        """Collect all tasks in the DAG reachable from entry tasks."""
        all_tasks = set(entry_ids)
        to_process = list(entry_ids)
        
        while to_process:
            tid = to_process.pop()
            downstream = self.bus._conn.execute(
                "SELECT to_task FROM dag_edges WHERE from_task=?",
                (tid,),
            ).fetchall()
            for row in downstream:
                if row["to_task"] not in all_tasks:
                    all_tasks.add(row["to_task"])
                    to_process.append(row["to_task"])
        
        return all_tasks
    
    def stop(self):
        self._running = False
