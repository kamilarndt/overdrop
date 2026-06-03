"""
OverDrop — Hermes AgentRuntime (Python native adapter)

Hermes is a special case: it's written in Python, so it can use
the OverDrop core natively without going through CLI subprocess.

This adapter shows how any Python agent integrates with OverDrop.
"""

import asyncio
import logging
from typing import Optional, Callable, Awaitable
from overdrop import MailBus, FsProtocol
from overdrop.types import (
    Task, TaskStatus, MessageType, Message, AgentRuntime as AgentRuntimeInterface,
)

logger = logging.getLogger("overdrop.hermes")


class HermesAdapter(AgentRuntimeInterface):
    """Adapts Hermes (Python agent) to OverDrop protocol.
    
    Hermes can:
    - Directly read/write SQLite Mail Bus
    - Directly use FsProtocol for task state
    - Run AgentLoop natively (no CLI overhead)
    - Act as a broker/coordinator for other agents
    """
    
    def __init__(self, workspace: str = "./workspace", db_path: str = None):
        self.fs = FsProtocol(workspace)
        self.bus = MailBus(db_path or f"{workspace}/overdrop.db")
        self.bus.connect()
        self._running = False
        self._handlers: dict[str, Callable[[Task], Awaitable[dict]]] = {}
        self._poll_interval = 2.0  # seconds
    
    def register_handler(self, task_type: str,
                         handler: Callable[[Task], Awaitable[dict]]):
        """Register a handler for task types (tag-based routing)."""
        self._handlers[task_type] = handler
    
    async def start_loop(self, agent_name: str, filter_tags: list[str] = None):
        """Start the main agent loop.
        
        Polls inbox -> claims tasks -> runs handler -> submits result.
        """
        self._running = True
        logger.info(f"Hermes agent '{agent_name}' starting loop")
        
        while self._running:
            # 1. Poll inbox for tasks
            tasks = self.fs.list_tasks("inbox")
            
            for task in tasks:
                # Filter by tags if specified
                ctx_tags = task.context.get("tags", [])
                if filter_tags and not any(t in ctx_tags for t in filter_tags):
                    continue
                
                # 2. Claim task (atomic os.rename)
                claimed = self.fs.claim(agent_name, task.id)
                if not claimed:
                    continue  # someone else claimed it
                
                logger.info(f"Claimed task: {task.id} ({task.title})")
                
                # 3. Find handler
                handler = self._find_handler(task)
                if not handler:
                    logger.warning(f"No handler for {task.id}, failing")
                    self.fs.fail(task.id, error="No handler registered")
                    continue
                
                # 4. Execute handler
                try:
                    if asyncio.iscoroutinefunction(handler):
                        result = await handler(task)
                    else:
                        result = handler(task)
                    
                    # 5. Complete
                    self.fs.complete(task.id, result=result)
                    self.bus.send(
                        MessageType.WORKER_DONE,
                        sender=agent_name,
                        recipient=task.from_agent,
                        payload={"task_id": task.id, "result": result},
                        task_id=task.id,
                    )
                    logger.info(f"Completed task: {task.id}")
                    
                except Exception as e:
                    logger.error(f"Task {task.id} failed: {e}")
                    self.fs.fail(task.id, error=str(e))
                    self.bus.send(
                        MessageType.ESCALATE,
                        sender=agent_name,
                        recipient=task.from_agent,
                        payload={"task_id": task.id, "error": str(e)},
                        task_id=task.id,
                    )
            
            # 6. Check for new messages
            msgs = self.bus.poll(agent_name, unread_only=True)
            for msg in msgs:
                await self._handle_message(msg, agent_name)
            
            await asyncio.sleep(self._poll_interval)
    
    def stop(self):
        """Stop the agent loop."""
        self._running = False
    
    async def _handle_message(self, msg: Message, agent_name: str):
        """Handle incoming Mail Bus messages."""
        self.bus.mark_read(msg.id)
        
        if msg.type == MessageType.ASK:
            # Handle blocking ask — find answer and reply
            logger.info(f"Ask from {msg.sender}: {msg.payload}")
            # Agent should process this in its own turn
            # For now, send auto-reply
            self.bus.reply(msg.id, agent_name, {
                "status": "received",
                "message": f"Ask '{msg.payload.get('question')}' received",
            })
    
    async def spawn(self, task: Task) -> str:
        """Hermes doesn't need to spawn — runs natively."""
        return f"hermes:{task.id}"
    
    async def deploy_config(self, worktree_path: str, role: str) -> None:
        """No-op for Hermes — config is in Python code."""
        pass
    
    async def enforce_guards(self, role: str) -> None:
        """Implement role-based tool enforcement in Python."""
        allowed_ops = {
            "scout": ["read", "grep", "find", "ls"],
            "builder": ["read", "write", "edit", "bash"],
            "reviewer": ["read", "grep", "find", "diff"],
            "coordinator": ["send_message", "broadcast", "create_task"],
        }
        logger.info(f"Hermes guards enforced for role '{role}': "
                     f"{allowed_ops.get(role, ['read'])}")
    
    async def parse_transcript(self, stream) -> list[dict]:
        """No CLI transcript — Hermes events are Python objects."""
        return []
    
    async def interrupt(self, handle: str) -> None:
        """Interrupt a running handler."""
        logger.warning(f"Interrupt requested for {handle}")
        self._running = False
    
    def _find_handler(self, task: Task) -> Optional[Callable]:
        """Find best handler for task based on context tags."""
        task_type = task.context.get("type", "default")
        return self._handlers.get(task_type) or self._handlers.get("default")
    
    def __del__(self):
        self.bus.close()
