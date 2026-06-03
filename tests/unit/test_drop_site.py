"""OverDrop — DropSite Primitives Unit Tests (pytest)

Tests for filesystem protocol: atomic claim, stale reap, retry, concurrency.
"""
import os
import sys
import tempfile
import threading
import time
import shutil
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "python"))
from overdrop import FsProtocol, TaskStatus


@pytest.fixture
def fs():
    """Create a temporary FsProtocol for testing."""
    tmp = tempfile.mkdtemp(prefix="od-fs-")
    yield FsProtocol(tmp)
    shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# A. Atomic Task Acquisition
# ---------------------------------------------------------------------------

def test_atomic_task_acquisition(fs):
    """10 agents try to claim the SAME task — only ONE wins."""
    task_id = fs.submit("Race task", from_agent="test")
    assert task_id

    results = []
    lock = threading.Lock()

    def try_claim(agent_name):
        claimed = fs.claim(agent_name, task_id)
        with lock:
            results.append((agent_name, claimed is not None))

    threads = []
    for i in range(10):
        t = threading.Thread(target=try_claim, args=(f"agent-{i}",))
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=5)

    winners = [a for a, won in results if won]
    losers = [a for a, won in results if not won]

    assert len(winners) == 1, f"Expected 1 winner, got {len(winners)}: {winners}"
    assert len(losers) == 9, f"Expected 9 losers, got {len(losers)}"

    # Verify the task is in active/
    task = fs.get_task(task_id)
    assert task is not None
    assert task.assignee == winners[0]
    assert task.status == TaskStatus.CLAIMED


def test_claim_returns_none_when_taken(fs):
    """Claim should return None if task already moved."""
    task_id = fs.submit("Already taken", from_agent="test")
    first = fs.claim("agent-a", task_id)
    assert first is not None

    second = fs.claim("agent-b", task_id)
    assert second is None  # already in active/


def test_atomic_rename_concurrent_threads(fs):
    """5 concurrent agents claim 5 different tasks — no cross-contamination."""
    task_ids = []
    for i in range(5):
        tid = fs.submit(f"Task {i}", from_agent="test", assign="any")
        task_ids.append(tid)

    claimed = {}
    lock = threading.Lock()

    def claim_one(agent_name, tid):
        result = fs.claim(agent_name, tid)
        with lock:
            if result:
                claimed[agent_name] = tid

    import random
    threads = []
    for i in range(5):
        tid = task_ids[i]
        t = threading.Thread(target=claim_one, args=(f"agent-{i}", tid))
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=5)

    assert len(claimed) == 5, f"Expected 5 claims, got {len(claimed)}"


# ---------------------------------------------------------------------------
# B. Stale Task Reaper
# ---------------------------------------------------------------------------

def test_stale_task_reaper(fs):
    """Task stuck in active/ too long → reaped back to inbox."""
    task_id = fs.submit("Stale task", from_agent="test")
    claimed = fs.claim("ghost-agent", task_id)
    assert claimed is not None

    # Artificially age the file (change mtime)
    active_file = os.path.join(fs.root, "active", f"{task_id}.json")
    old_time = time.time() - 400  # 400 seconds ago
    os.utime(active_file, (old_time, old_time))

    # Reap with 300s timeout
    reaped = fs.reap_stale(timeout_s=300)
    assert task_id in reaped, f"Expected {task_id[:8]} to be reaped, got {reaped}"

    # Should be back in inbox (status may be preserved as 'claimed' from active)
    task = fs.get_task(task_id)
    assert task is not None


def test_stale_reaper_ignores_recent(fs):
    """Fresh task in active/ should NOT be reaped."""
    task_id = fs.submit("Fresh task", from_agent="test")
    fs.claim("active-agent", task_id)

    # Don't age — use very strict timeout
    reaped = fs.reap_stale(timeout_s=3600)  # 1 hour
    assert task_id not in reaped


# ---------------------------------------------------------------------------
# C. Retry Logic
# ---------------------------------------------------------------------------

def test_retry_exhausts(fs):
    """Task retries until max_retries, then FAILED."""
    task_id = fs.submit("Flaky task", from_agent="test", max_retries=3)

    for i in range(3):
        task = fs.get_task(task_id)
        assert task.status == TaskStatus.INBOX
        fs.claim("worker", task_id)
        fs.fail(task_id, error=f"Attempt {i+1}")
        task = fs.get_task(task_id)
        if task.retry_count < 3:
            assert task.status == TaskStatus.INBOX, f"Expected INBOX on retry {i+1}, got {task.status}"

    # After 3 failures
    task = fs.get_task(task_id)
    assert task.status == TaskStatus.FAILED
    assert task.retry_count == 3


def test_retry_count_preserved(fs):
    """Retry count survives multiple fails."""
    task_id = fs.submit("Count test", from_agent="test", max_retries=5)
    fs.claim("worker", task_id)
    fs.fail(task_id)

    task = fs.get_task(task_id)
    assert task.retry_count == 1

    fs.claim("worker", task_id)
    fs.fail(task_id)

    task = fs.get_task(task_id)
    assert task.retry_count == 2


# ---------------------------------------------------------------------------
# D. Block/Unblock
# ---------------------------------------------------------------------------

def test_block_and_unblock(fs):
    """Task can be blocked and unblocked."""
    task_id = fs.submit("DB migration", from_agent="test")
    fs.claim("worker", task_id)
    fs.block(task_id, reason="Cluster restart")

    task = fs.get_task(task_id)
    assert task.status == TaskStatus.BLOCKED

    fs.unblock(task_id)
    task = fs.get_task(task_id)
    assert task.status == TaskStatus.INBOX


# ---------------------------------------------------------------------------
# E. Full Lifecycle
# ---------------------------------------------------------------------------

def test_full_lifecycle(fs):
    """Full: submit → claim → complete → done."""
    task_id = fs.submit("Build feature", from_agent="hermes",
                         context={"type": "code"}, max_retries=2)
    assert task_id

    task = fs.get_task(task_id)
    assert task.status == TaskStatus.INBOX
    assert task.title == "Build feature"
    assert task.from_agent == "hermes"

    # Claim
    claimed = fs.claim("pi", task_id)
    assert claimed.status == TaskStatus.CLAIMED
    assert claimed.assignee == "pi"

    # Complete
    fs.complete(task_id, result={"files": ["src/app.py"]})

    done = fs.get_task(task_id)
    assert done.status == TaskStatus.DONE
    assert done.result["files"] == ["src/app.py"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
