"""
OverDrop — OpenCode Adapter (Python)

Integrates OpenCode CLI with OverDrop protocol.
OpenCode is a headless coding agent CLI.

Similar to Claude adapter but for the opencode binary.
"""

import asyncio
import json
import logging
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from overdrop import FsProtocol, MailBus
from overdrop.types import Task, TaskStatus, MessageType

logger = logging.getLogger("overdrop.opencode")


class OpenCodeAdapter:
    """Run OpenCode CLI as an OverDrop worker.
    
    OpenCode doesn't have built-in guards, so we:
    1. Run it in an isolated temp directory
    2. Copy only the files it needs
    3. Monitor its output for errors
    """
    
    def __init__(self, workspace: str = "./workspace",
                 opencode_bin: str = "opencode",
                 db_path: str = None):
        self.fs = FsProtocol(workspace)
        self.bus = MailBus(db_path or f"{workspace}/overdrop.db")
        self.bus.connect()
        self.opencode_bin = opencode_bin
        self._running = False
        self._poll_interval = 3.0
        self._processes: dict[str, subprocess.Popen] = {}
    
    async def spawn(self, task: Task) -> str:
        """Run OpenCode on a task."""
        workdir = tempfile.mkdtemp(prefix=f"od-opencode-{task.id[:8]}-")
        
        # Build context
        context_files = task.context.get("files", [])
        extras = task.context.get("extra", "")
        
        cmd = (f"{self.opencode_bin} "
               f"--output json "
               f"-w {shlex.quote(workdir)} ")
        
        if context_files:
            cmd += f"-f {' '.join(shlex.quote(f) for f in context_files)} "
        
        cmd += f"\"{task.title}\""
        
        if extras:
            cmd += f" {shlex.quote(extras)}"
        
        logger.info(f"OpenCode spawn: {cmd[:120]}...")
        
        process = await asyncio.create_subprocess_shell(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        
        handle = f"opencode:{task.id}"
        self._processes[handle] = process
        
        asyncio.create_task(self._handle_output(process, task, handle))
        return handle
    
    async def _handle_output(self, process, task: Task, handle: str):
        """Read stdout and stderr, complete or fail task."""
        stdout_lines = []
        stderr_lines = []
        
        while True:
            line = await process.stdout.readline()
            if not line:
                break
            stdout_lines.append(line.decode("utf-8", errors="replace").strip())
        
        while True:
            line = await process.stderr.readline()
            if not line:
                break
            stderr_lines.append(line.decode("utf-8", errors="replace").strip())
        
        self._processes.pop(handle, None)
        return_code = await process.wait()
        
        if return_code == 0:
            self.fs.complete(task.id, result={
                "output_summary": "\n".join(stdout_lines[-5:]),
                "stdout_lines": len(stdout_lines),
            })
            self.bus.send(MessageType.WORKER_DONE, "opencode",
                         task.from_agent or "coordinator",
                         {"task_id": task.id}, task_id=task.id)
        else:
            error = "\n".join(stderr_lines[-10:]) or "Unknown error"
            self.fs.fail(task.id, error=error[:500])
            self.bus.send(MessageType.ESCALATE, "opencode",
                         task.from_agent or "coordinator",
                         {"task_id": task.id, "error": error[:200]},
                         task_id=task.id)
    
    async def agent_loop(self, agent_name: str = "opencode"):
        """Main agent loop."""
        self._running = True
        while self._running:
            tasks = self.fs.list_tasks("inbox")
            for task in tasks:
                claimed = self.fs.claim(agent_name, task.id)
                if claimed:
                    await self.spawn(task)
            await asyncio.sleep(self._poll_interval)
    
    def stop(self):
        self._running = False
        for p in self._processes.values():
            p.terminate()
    
    def __del__(self):
        self.bus.close()
