"""
OverDrop — Universal Agent Communication Protocol
Python core: MailBus (SQLite) + FsProtocol (filesystem) + Worktree + AgentRuntime
"""

__version__ = "0.2.0"

from .mailbus import MailBus, Message
from .fsprotocol import FsProtocol
from .types import AgentStatus, MessageType, TaskStatus, AgentRuntime
from .worktree import WorktreeManager, MergeQueue
from .resolver import MockResolver, AiResolver, ConflictContext, create_resolver

__all__ = [
    "MailBus", "Message",
    "FsProtocol",
    "WorktreeManager", "MergeQueue",
    "AgentStatus", "MessageType", "TaskStatus", "AgentRuntime",
    "MockResolver", "AiResolver", "ConflictContext", "create_resolver",
]
