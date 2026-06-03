#!/usr/bin/env python3
"""OverDrop Agent Worker — polls inbox, processes tasks, reports back.

Usage:
    python agent_worker.py [--workspace DIR] [--agent NAME] [--interval SECS]

Features:
- Auto-polls inbox every N seconds
- Claims tasks, simulates work with progress updates
- Sends MailBus notifications (DISPATCH, PROGRESS, WORKER_DONE, BROADCAST)
- Runs stale task reaper periodically
- Graceful shutdown on SIGTERM/SIGINT
"""

import sys
import os
import time
import signal
import logging
import argparse
import random

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("overdrop.worker")

# Resolve path — works regardless of where script is run from
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, os.path.join(PROJECT_ROOT, "python"))

from overdrop import FsProtocol, MailBus, MessageType


class AgentWorker:
    """OverDrop agent worker with error handling and graceful shutdown."""

    def __init__(self, workspace: str, agent_name: str, poll_interval: int = 2):
        self.workspace = workspace
        self.agent_name = agent_name
        self.poll_interval = poll_interval
        self.running = True
        self.task_count = 0
        self.error_count = 0

        # Initialize components
        self.fs = FsProtocol(workspace)
        self.bus = MailBus(os.path.join(workspace, "overdrop.db"))
        self.bus.connect()

        # Register signal handlers for graceful shutdown
        signal.signal(signal.SIGTERM, self._shutdown)
        signal.signal(signal.SIGINT, self._shutdown)

        logger.info(f"Agent '{agent_name}' initialized — workspace: {workspace}")

    def _shutdown(self, signum, frame):
        """Handle shutdown signals gracefully."""
        logger.info(f"Received signal {signum}, shutting down...")
        self.running = False

    def run(self):
        """Main polling loop."""
        logger.info(f"Starting agent loop (poll every {self.poll_interval}s)")

        while self.running:
            try:
                self._process_cycle()
            except Exception as e:
                self.error_count += 1
                logger.error(f"Error in cycle: {e}", exc_info=True)
                # Don't crash — wait and retry
                if self.error_count > 10:
                    logger.critical(f"Too many errors ({self.error_count}), stopping")
                    self.running = False
                    break

            time.sleep(self.poll_interval)

        # Cleanup
        self.bus.close()
        logger.info(f"Agent stopped. Processed {self.task_count} tasks, {self.error_count} errors")

    def _process_cycle(self):
        """Single processing cycle — poll inbox, claim, process."""
        inbox_tasks = self.fs.list_tasks("inbox")

        for task in inbox_tasks:
            if not self.running:
                break

            try:
                self._process_task(task)
            except Exception as e:
                self.error_count += 1
                logger.error(f"Failed to process task {task.id[:8]}: {e}")

    def _process_task(self, task):
        """Process a single task — claim, work, complete."""
        # 1. Claim
        claimed = self.fs.claim(self.agent_name, task.id)
        if not claimed:
            return

        self.task_count += 1
        logger.info(f"📥 Claimed: {task.title} (id={task.id[:8]})")

        # Notify: claimed
        self.bus.send(
            MessageType.DISPATCH, sender=self.agent_name, recipient="hermes",
            payload={"event": "task_started", "task_id": task.id, "title": task.title},
            task_id=task.id,
        )
        self.bus.send(
            MessageType.BROADCAST, sender=self.agent_name, recipient="@all",
            payload={"event": "agent_claimed", "agent": self.agent_name, "task": task.title},
        )

        # 2. Simulate work
        steps = random.randint(2, 4)
        for step in range(steps):
            if not self.running:
                break
            time.sleep(1.5)
            pct = int((step + 1) / steps * 100)
            self.bus.send(
                MessageType.PROGRESS, sender=self.agent_name, recipient="@all",
                payload={"task": task.title, "progress": f"{pct}%", "step": f"{step+1}/{steps}"},
                task_id=task.id,
            )
            logger.info(f"  ⏳ {pct}% ({step+1}/{steps})")

        # 3. Complete
        result = {"files": [f"{task.title.lower().replace(' ', '_')}.py"], "lines": random.randint(10, 200)}
        self.fs.complete(task.id, result=result)

        self.bus.send(
            MessageType.WORKER_DONE, sender=self.agent_name, recipient="hermes",
            payload={"task_id": task.id, "result": result}, task_id=task.id,
        )
        self.bus.send(
            MessageType.BROADCAST, sender=self.agent_name, recipient="@all",
            payload={"event": "task_completed", "task": task.title, "result": result},
        )

        logger.info(f"  ✅ Done: {task.title}")

    def _run_reaper(self):
        """Run stale task reaper periodically."""
        reaped = self.fs.reap_stale(timeout_s=300)
        if reaped:
            logger.info(f"🧹 Reaped {len(reaped)} stale tasks")


def main():
    parser = argparse.ArgumentParser(description="OverDrop Agent Worker")
    parser.add_argument("--workspace", "-w", default=".overdrop", help="Workspace directory")
    parser.add_argument("--agent", "-a", default="auto-worker", help="Agent name")
    parser.add_argument("--interval", "-i", type=int, default=2, help="Poll interval (seconds)")
    args = parser.parse_args()

    # Resolve workspace path
    workspace = os.path.abspath(args.workspace)

    worker = AgentWorker(workspace, args.agent, args.interval)

    # Run reaper on startup
    worker._run_reaper()

    # Main loop
    worker.run()


if __name__ == "__main__":
    main()
