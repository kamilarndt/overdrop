"""
OverDrop — Tmux/Zellij Integration (Mid-run Steering)

Spawns each agent in its own tmux pane for live visibility.
Operator can attach, watch, or interrupt at any time.

Based on pi-interactive-subagents pattern (HazAT).
"""

import logging
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger("overdrop.tmux")


def _run(cmd: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


class TmuxSession:
    """Manages a tmux session for an OverDrop agent."""
    
    def __init__(self, session_name: str, multiplexer: str = "tmux"):
        """
        Args:
            session_name: Unique name for this agent session
            multiplexer: 'tmux' (default) or 'zellij'
        """
        self.name = session_name
        self.mux = multiplexer
        self._window = "overdrop"
    
    def create(self, command: str, cwd: str = None):
        """Create a new tmux session running a command.
        
        Args:
            command: Command to run (e.g. 'pi --print "review X"')
            cwd: Working directory
        """
        if self.mux == "tmux":
            # Kill any existing session with this name
            _run(f"tmux kill-session -t {shlex.quote(self.name)} 2>/dev/null")
            
            # Create new detached session
            if cwd:
                cmd_full = f"cd {shlex.quote(cwd)} && {command}"
            else:
                cmd_full = command
            
            result = _run(
                f"tmux new-session -d -s {shlex.quote(self.name)} "
                f"-n {shlex.quote(self._window)} "
                f"'{cmd_full}'"
            )
            
            if result.returncode == 0:
                logger.info(f"Tmux session created: {self.name}")
                return True
            else:
                logger.error(f"Tmux creation failed: {result.stderr}")
                return False
        
        elif self.mux == "zellij":
            # Zellij: attach-mode for programmatic control
            result = _run(
                f"zellij -s {shlex.quote(self.name)} run -- "
                f"bash -c {shlex.quote(command)}"
            )
            logger.info(f"Zellij session: {self.name}")
            return result.returncode == 0
        
        return False
    
    def send_keys(self, text: str):
        """Send keystrokes to the agent tmux pane (mid-run steering!)."""
        if self.mux == "tmux":
            _run(f"tmux send-keys -t {shlex.quote(self.name)} {shlex.quote(text)} Enter")
        elif self.mux == "zellij":
            _run(f"zellij -s {shlex.quote(self.name)} write {shlex.quote(text)}")
    
    def send_ctrl_c(self):
        """Send Ctrl+C to interrupt the agent."""
        if self.mux == "tmux":
            _run(f"tmux send-keys -t {shlex.quote(self.name)} C-c")
    
    def attach(self):
        """Attach terminal to this session (for the human operator)."""
        if self.mux == "tmux":
            os.execvp("tmux", ["tmux", "attach-session", "-t", self.name])
        elif self.mux == "zellij":
            os.execvp("zellij", ["zellij", "attach", "-s", self.name])
    
    def capture_pane(self, start_line: int = -50) -> str:
        """Capture the last N lines of terminal output."""
        if self.mux == "tmux":
            result = _run(
                f"tmux capture-pane -t {shlex.quote(self.name)} "
                f"-p -S {start_line}"
            )
            return result.stdout
        return ""
    
    def is_alive(self) -> bool:
        """Check if the tmux session is still running."""
        result = _run(
            f"tmux has-session -t {shlex.quote(self.name)} 2>/dev/null"
        )
        return result.returncode == 0
    
    def kill(self):
        """Kill the tmux session."""
        _run(f"tmux kill-session -t {shlex.quote(self.name)} 2>/dev/null")
    
    def resize_pane(self, width: int = None, height: int = None):
        """Resize the tmux pane."""
        if width:
            _run(f"tmux resize-pane -t {shlex.quote(self.name)} -x {width}")
        if height:
            _run(f"tmux resize-pane -t {shlex.quote(self.name)} -y {height}")


class TmuxOrchestrator:
    """Orchestrate multiple agent tmux sessions in a layout.
    
    Creates a tiled layout where each agent gets a pane.
    """
    
    def __init__(self, mux: str = "tmux"):
        self.mux = mux
        self.sessions: dict[str, TmuxSession] = {}
    
    def spawn_agent(self, agent_name: str, command: str, cwd: str = None):
        """Spawn an agent in its own tmux session."""
        sess = TmuxSession(f"od-{agent_name}", self.mux)
        sess.create(command, cwd)
        self.sessions[agent_name] = sess
        return sess
    
    def create_layout(self, agents: list[str]):
        """Create a tmux layout with multiple agents.
        
        For tmux this creates a new window with split panes.
        For multiple sessions, agents are in separate sessions.
        """
        layout_name = "od-layout"
        
        # Create a master session that splits panes
        _run(f"tmux kill-session -t {shlex.quote(layout_name)} 2>/dev/null")
        _run(f"tmux new-session -d -s {shlex.quote(layout_name)} 'echo OverDrop; sleep 999d'")
        
        for i, agent_name in enumerate(agents):
            if i == 0:
                # First agent goes in the first pane
                sess_name = f"od-{agent_name}"
                _run(f"tmux send-keys -t {shlex.quote(layout_name)} "
                     f"'tmux attach -t {shlex.quote(sess_name)} || echo Agent: {agent_name}' Enter")
            else:
                # Split and run next agent
                _run(f"tmux split-window -t {shlex.quote(layout_name)} "
                     f"'{shlex.quote(f'tmux attach -t od-{agent_name}')}'")
                _run(f"tmux select-layout tiled")
        
        logger.info(f"Layout created: {layout_name} with {len(agents)} agents")
    
    def steer(self, agent_name: str, message: str):
        """Send steering prompt to a running agent."""
        sess = self.sessions.get(agent_name)
        if not sess:
            raise ValueError(f"No session for agent: {agent_name}")
        sess.send_keys(message)
        logger.info(f"Steered {agent_name}: {message[:50]}...")
    
    def interrupt(self, agent_name: str):
        """Interrupt a running agent."""
        sess = self.sessions.get(agent_name)
        if sess:
            sess.send_ctrl_c()
    
    def kill_all(self):
        """Kill all agent sessions."""
        for sess in self.sessions.values():
            sess.kill()
        self.sessions.clear()
    
    def status_all(self) -> dict:
        """Get status of all sessions."""
        return {name: sess.is_alive() for name, sess in self.sessions.items()}
    
    def capture_all(self) -> dict:
        """Capture output from all sessions."""
        return {name: sess.capture_pane() for name, sess in self.sessions.items()}
