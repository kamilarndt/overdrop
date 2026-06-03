"""
OverDrop — Claude Code Adapter (Python)

Integrates Claude Code CLI with OverDrop protocol.

How it works:
1. Polls OverDrop FsProtocol for new tasks
2. Spawns Claude Code as headless subprocess (CLI flags)
3. Streams NDJSON output for live observability
4. Injects guards via .claude/settings.local.json
5. Reports completion back via Mail Bus

Based on Overstory's ClaudeRuntime adapter pattern.
"""

import asyncio
import json
import logging
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Callable, AsyncIterator

from overdrop import FsProtocol, MailBus
from overdrop.types import (
    Task, TaskStatus, MessageType, AgentRuntime as AgentRuntimeInterface,
)

logger = logging.getLogger("overdrop.claude")


class ClaudeAdapter(AgentRuntimeInterface):
    """Adapts Claude Code CLI to OverDrop protocol.
    
    Spawns Claude as headless subprocess with --output-format stream-json
    for real-time NDJSON event streaming.
    """
    
    def __init__(self, workspace: str = "./workspace", 
                 claude_bin: str = "claude",
                 db_path: str = None):
        self.fs = FsProtocol(workspace)
        self.bus = MailBus(db_path or f"{workspace}/overdrop.db")
        self.bus.connect()
        self.claude_bin = claude_bin
        self._running = False
        self._poll_interval = 3.0
        self._handlers: dict[str, Callable] = {}
        
        # Track active subprocesses for interrupt
        self._active_processes: dict[str, subprocess.Popen] = {}
    
    async def spawn(self, task: Task) -> str:
        """Spawn a Claude Code subprocess for this task.
        
        Returns a handle string.
        """
        # Create work directory
        workdir = tempfile.mkdtemp(prefix=f"overdrop-claude-{task.id[:8]}-")
        
        # Deploy guard config
        await self.deploy_config(workdir, task.context.get("role", "worker"))
        
        # Build the prompt
        context_files = task.context.get("files", [])
        context_str = " ".join(context_files) if context_files else ""
        
        cmd = (
            f"{self.claude_bin} -p "
            f"--output-format stream-json "
            f"--allowedTools read,write,edit,bash,grep "
            f"-w {shlex.quote(workdir)} "
        )
        
        if context_str:
            cmd += f"{shlex.quote(context_str)} "
        
        cmd += f"\"{task.title}\""
        
        logger.info(f"Claude spawn: {cmd[:120]}...")
        
        # Spawn
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        
        handle = f"claude:{task.id}"
        self._active_processes[handle] = process
        
        # Start background NDJSON parser
        asyncio.create_task(self._parse_output(process, task.id, handle))
        
        return handle
    
    async def deploy_config(self, worktree_path: str, role: str = "worker"):
        """Deploy Claude guard config into .claude/settings.local.json."""
        claude_dir = Path(worktree_path) / ".claude"
        claude_dir.mkdir(exist_ok=True)
        
        # Guards restrict Claude's tools based on role
        guards = {
            "scout": {"allowedTools": ["Read", "Glob", "Search"]},
            "worker": {"allowedTools": ["Read", "Write", "Edit", "Bash", "Glob", "Search"]},
            "reviewer": {"allowedTools": ["Read", "Glob", "Search", "Bash"]},
        }
        
        config = {
            "permissions": guards.get(role, guards["worker"]),
            "allowReading": True,
            "allowWriting": role in ("worker", "builder"),
            "allowBash": role in ("worker", "reviewer"),
        }
        
        config_path = claude_dir / "settings.local.json"
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)
        
        logger.info(f"Claude guards deployed for role '{role}' at {config_path}")
    
    async def enforce_guards(self, role: str) -> None:
        """Already handled in deploy_config."""
        pass
    
    def parse_transcript(self, stream) -> list[dict]:
        """Parse Claude NDJSON output into structured events."""
        events = []
        for line in stream:
            try:
                event = json.loads(line)
                events.append(event)
            except json.JSONDecodeError:
                continue
        return events
    
    async def interrupt(self, handle: str) -> None:
        """Interrupt a running Claude subprocess."""
        proc = self._active_processes.get(handle)
        if proc:
            logger.warning(f"Interrupting Claude process {handle}")
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10)
            except asyncio.TimeoutError:
                proc.kill()
            del self._active_processes[handle]
    
    async def _parse_output(self, process, task_id: str, handle: str):
        """Parse NDJSON stream from Claude subprocess.
        
        Yields ParsedEvent for web dashboard / observability.
        """
        events_buffer = []
        
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            
            line = line.decode("utf-8").strip()
            if not line:
                continue
            
            try:
                event = json.loads(line)
                event_type = event.get("type", "unknown")
                
                parsed = {
                    "type": event_type,
                    "content": event,
                    "task_id": task_id,
                    "timestamp": __import__("datetime").datetime.now().isoformat(),
                }
                events_buffer.append(parsed)
                
                # Log important events
                if event_type in ("text", "error"):
                    text = event.get("text", event.get("error", ""))[:100]
                    logger.debug(f"[Claude {task_id[:8]}] {event_type}: {text}")
                    
            except json.JSONDecodeError:
                pass
        
        # Process exited
        self._active_processes.pop(handle, None)
        
        # If Claude completed successfully, mark task as done
        return_code = await process.wait()
        
        if return_code == 0:
            self.fs.complete(task_id, result={
                "events_count": len(events_buffer),
                "handle": handle,
            })
            self.bus.send(
                MessageType.WORKER_DONE,
                sender="claude",
                recipient="coordinator",
                payload={"task_id": task_id, "events": len(events_buffer)},
                task_id=task_id,
            )
            logger.info(f"Claude task {task_id[:8]} completed successfully")
        else:
            stderr = (await process.stderr.read()).decode("utf-8")[:500]
            self.fs.fail(task_id, error=stderr)
            self.bus.send(
                MessageType.ESCALATE,
                sender="claude",
                recipient="coordinator",
                payload={"task_id": task_id, "error": stderr[:200]},
                task_id=task_id,
            )
            logger.error(f"Claude task {task_id[:8]} failed: {stderr[:100]}")
        
        return events_buffer
    
    async def agent_loop(self, agent_name: str = "claude"):
        """Main agent loop: polls inbox → spawns Claude → reports back."""
        self._running = True
        logger.info(f"Claude agent '{agent_name}' starting loop")
        
        while self._running:
            # 1. Poll inbox
            tasks = self.fs.list_tasks("inbox")
            
            for task in tasks:
                # 2. Claim
                claimed = self.fs.claim(agent_name, task.id)
                if not claimed:
                    continue
                
                logger.info(f"Claude claimed task: {task.id[:8]} ({task.title[:50]})")
                
                # 3. Spawn
                await self.spawn(task)
            
            await asyncio.sleep(self._poll_interval)
    
    def stop(self):
        """Stop the agent loop and interrupt all processes."""
        self._running = False
        for handle in list(self._active_processes.keys()):
            proc = self._active_processes[handle]
            proc.terminate()
        self._active_processes.clear()
    
    def __del__(self):
        self.bus.close()
