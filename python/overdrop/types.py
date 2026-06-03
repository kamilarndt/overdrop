"""OverDrop type definitions — shared types for all adapters."""

from dataclasses import dataclass, field
from typing import Any, Optional
from enum import Enum
from datetime import datetime


class MessageType(str, Enum):
    """Typed protocol message types (z Overstory's 8-type system)."""
    DISPATCH = "dispatch"          # delegate a task
    ASK = "ask"                    # blocking question (with reply expectation)
    REPLY = "reply"                # answer to an ask
    ESCALATE = "escalate"          # request help from supervisor
    WORKER_DONE = "worker_done"    # task completed
    MERGE_READY = "merge_ready"    # ready for merge queue
    BROADCAST = "broadcast"        # @all / @builders
    PROGRESS = "progress_update"   # non-blocking status update


class TaskStatus(str, Enum):
    """Full task lifecycle state machine."""
    PENDING_DEP = "pending_dependencies"
    INBOX = "inbox"
    CLAIMED = "claimed"
    ACTIVE = "active"
    BLOCKED = "blocked"
    NEEDS_DECISION = "needs_decision"
    MERGE_READY = "merge_ready"
    NEEDS_REVIEW = "needs_review"
    DONE = "done"
    FAILED = "failed"
    NEEDS_ATTENTION = "needs_attention"


class AgentStatus(str, Enum):
    """Agent lifecycle status (z pi-intercom model)."""
    IDLE = "idle"
    THINKING = "thinking"
    TOOL = "tool"  # tool:<name>
    OFFLINE = "offline"


@dataclass
class Message:
    """A single message on the SQLite Mail Bus."""
    id: str
    type: MessageType
    sender: str
    recipient: str
    payload: dict = field(default_factory=dict)
    reply_to: Optional[str] = None
    task_id: Optional[str] = None
    priority: int = 5
    read: bool = False
    created_at: Optional[str] = None


@dataclass
class Task:
    """A unit of work in the system."""
    id: str
    title: str
    status: TaskStatus = TaskStatus.INBOX
    from_agent: str = ""
    assignee: Optional[str] = None
    context: dict = field(default_factory=dict)
    result: dict = field(default_factory=dict)
    priority: int = 5
    max_retries: int = 3
    retry_count: int = 0
    parent_task: Optional[str] = None
    group_id: Optional[str] = None
    worktree: Optional[str] = None
    version: int = 1
    created_at: Optional[str] = None


class AgentRuntime:
    """Pluggable AgentRuntime interface (adapter contract).
    
    Each agent type (Hermes, Pi, Claude, OpenCode) implements this.
    """
    
    async def spawn(self, task: Task) -> str:
        """Spawn a process/agent for this task. Returns handle."""
        raise NotImplementedError
    
    async def deploy_config(self, worktree_path: str, role: str) -> None:
        """Initialize agent config with guards for this role."""
        raise NotImplementedError
    
    async def enforce_guards(self, role: str) -> None:
        """Apply tool-level guards for this agent's role."""
        raise NotImplementedError
    
    async def parse_transcript(self, stream) -> list[dict]:
        """Parse agent output stream into structured events."""
        raise NotImplementedError
    
    async def interrupt(self, handle: str) -> None:
        """Interrupt a running agent (mid-run steering)."""
        raise NotImplementedError
