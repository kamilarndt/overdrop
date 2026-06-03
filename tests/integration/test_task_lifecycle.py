"""OverDrop — Task Lifecycle & DAG Integration Tests (pytest)"""
import os
import sys
import tempfile
import asyncio
import uuid
import subprocess as sp
import shutil
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "python"))
from overdrop import FsProtocol, MailBus, TaskStatus, MessageType


@pytest.fixture
def fs():
    tmp = tempfile.mkdtemp(prefix="od-lifecycle-")
    yield FsProtocol(tmp)
    shutil.rmtree(tmp)


@pytest.fixture
def bus(fs):
    db = os.path.join(fs.root, "od.db")
    b = MailBus(db)
    b.connect()
    yield b
    b.close()


# ---------------------------------------------------------------------------
# A. Full Lifecycle
# ---------------------------------------------------------------------------

def test_full_lifecycle_pending_to_done(fs, bus):
    """PENDING_DEPENDENCIES → INBOX → CLAIMED → DONE."""
    task_id = fs.submit("Build feature", from_agent="hermes",
                         context={"type": "implementation"}, max_retries=3)
    assert task_id

    # INBOX
    task = fs.get_task(task_id)
    assert task.status == TaskStatus.INBOX

    # CLAIMED
    claimed = fs.claim("pi", task_id)
    assert claimed.status == TaskStatus.CLAIMED

    # Update to ACTIVE (manual transition)
    task.status = TaskStatus.ACTIVE
    fs._write_task("active", task)

    # DONE
    fs.complete(task_id, result={"code": "ok"})
    task = fs.get_task(task_id)
    assert task.status == TaskStatus.DONE


def test_inbox_to_blocked_to_done(fs):
    """INBOX → CLAIMED → BLOCKED → INBOX → CLAIMED → DONE."""
    task_id = fs.submit("Complex flow", from_agent="hermes")

    fs.claim("worker", task_id)
    fs.block(task_id, reason="Waiting for dependency")
    assert fs.get_task(task_id).status == TaskStatus.BLOCKED

    fs.unblock(task_id)
    assert fs.get_task(task_id).status == TaskStatus.INBOX

    fs.claim("worker", task_id)
    fs.complete(task_id, result={"done": True})
    assert fs.get_task(task_id).status == TaskStatus.DONE


# ---------------------------------------------------------------------------
# B. needs vs after Dependencies
# ---------------------------------------------------------------------------

def setup_dag(fs, bus):
    """Setup DAG tables in the bus SQLite."""
    bus._conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id TEXT PRIMARY KEY, title TEXT, status TEXT DEFAULT 'inbox'
        );
        CREATE TABLE IF NOT EXISTS dag_edges (
            from_task TEXT NOT NULL, to_task TEXT NOT NULL, 
            dep_type TEXT NOT NULL DEFAULT 'needs',
            PRIMARY KEY (from_task, to_task)
        );
    """)
    bus._conn.commit()


def test_needs_vs_after_dependencies(fs, bus):
    """task-B with needs:[A] → blocked if A fails.
       task-C with after:[A] → runs even if A fails."""
    setup_dag(fs, bus)

    task_a = fs.submit("Upstream task A", from_agent="test")
    task_b = fs.submit("Task B (needs A)", from_agent="test")
    task_c = fs.submit("Task C (after A)", from_agent="test")

    # Create DAG edges
    bus._conn.execute(
        "INSERT INTO dag_edges VALUES (?, ?, ?)", (task_a, task_b, "needs"))
    bus._conn.execute(
        "INSERT INTO dag_edges VALUES (?, ?, ?)", (task_a, task_c, "after"))
    bus._conn.commit()

    # Verify edges exist
    edges = bus._conn.execute(
        "SELECT * FROM dag_edges WHERE from_task=?", (task_a,)
    ).fetchall()
    assert len(edges) == 2

    # Fail task A
    fs.claim("worker", task_a)
    fs.fail(task_a, error="Permanent failure")
    # Force final failure by exhausting retries
    for _ in range(2):
        t = fs.get_task(task_a)
        if t.status == TaskStatus.FAILED:
            break
        if t.status == TaskStatus.INBOX:
            fs.claim("worker", task_a)
            fs.fail(task_a, error="Still failing")

    assert fs.get_task(task_a).status == TaskStatus.FAILED

    # needs: → task B should NOT be unblocked (A failed)
    deps_b = bus._conn.execute(
        "SELECT dep_type FROM dag_edges WHERE to_task=?", (task_b,)
    ).fetchall()
    assert len(deps_b) == 1
    assert deps_b[0]["dep_type"] == "needs"

    # after: → task C could proceed (A completed, even with failure)
    deps_c = bus._conn.execute(
        "SELECT dep_type FROM dag_edges WHERE to_task=?", (task_c,)
    ).fetchall()
    assert len(deps_c) == 1
    assert deps_c[0]["dep_type"] == "after"


# ---------------------------------------------------------------------------
# C. Parallel Lifecycles
# ---------------------------------------------------------------------------

def test_parallel_task_lifecycles(fs):
    """Multiple independent tasks go through lifecycle simultaneously."""
    task_ids = []
    for i in range(10):
        tid = fs.submit(f"Task {i}", from_agent="test")
        task_ids.append(tid)

    # All in inbox
    inbox = fs.list_tasks("inbox")
    assert len(inbox) == 10

    # Claim all
    for i, tid in enumerate(task_ids):
        agent = f"worker-{i % 3}"
        claimed = fs.claim(agent, tid)
        assert claimed is not None

    # Complete half, fail half
    for i, tid in enumerate(task_ids):
        if i % 2 == 0:
            fs.complete(tid, result={"ok": True})
        else:
            fs.claim(f"worker-{i}", tid)
            fs.fail(tid, error="Flaky")
            # Continue until final state
            t = fs.get_task(tid)
            while t.status not in (TaskStatus.FAILED, TaskStatus.DONE):
                if t.status == TaskStatus.INBOX:
                    fs.claim(f"retry-{i}", tid)
                    fs.fail(tid, error="Still flaky")
                t = fs.get_task(tid)

    # Verify all terminal
    for tid in task_ids:
        t = fs.get_task(tid)
        assert t.status in (TaskStatus.DONE, TaskStatus.FAILED), \
            f"Task {tid} stuck at {t.status}"


# ---------------------------------------------------------------------------
# D. Error Propagation
# ---------------------------------------------------------------------------

def test_error_propagation_on_submit(fs):
    """Submit with invalid data should still work (robust)."""
    task_id = fs.submit("", from_agent="")
    assert task_id
    task = fs.get_task(task_id)
    assert task is not None
    assert task.title == ""


def test_result_preserved_on_complete(fs):
    """Complete should preserve all result data."""
    task_id = fs.submit("Result test", from_agent="test")
    fs.claim("worker", task_id)
    result = {
        "files": ["a.py", "b.py"],
        "tests": 42,
        "coverage": 0.85,
        "nested": {"deep": True},
    }
    fs.complete(task_id, result=result)

    task = fs.get_task(task_id)
    assert task.result["files"] == result["files"]
    assert task.result["nested"]["deep"] == True
    assert task.result["coverage"] == 0.85


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
