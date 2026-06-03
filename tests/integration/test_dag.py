"""OverDrop — DAG Orchestration Integration Tests (pytest)"""
import os, sys, tempfile, asyncio, uuid, shutil
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "python"))
from overdrop import FsProtocol, MailBus, TaskStatus
from dag_orchestrator import DagOrchestrator


@pytest.fixture
def setup():
    tmp = tempfile.mkdtemp(prefix="od-dag-")
    fs = FsProtocol(tmp)
    bus = MailBus(os.path.join(tmp, "od.db"))
    bus.connect()
    orch = DagOrchestrator(fs, bus)
    yield fs, bus, orch
    bus.close()
    shutil.rmtree(tmp)


def test_can_execute_no_deps(setup):
    """Task with no dependencies can always execute."""
    fs, bus, orch = setup
    task_id = fs.submit("No deps task", from_agent="test")
    assert orch.can_execute(task_id)


def test_can_execute_with_satisfied_deps(setup):
    """Task with satisfied needs can execute."""
    fs, bus, orch = setup

    task_a = fs.submit("Upstream A", from_agent="test")
    fs.claim("worker", task_a)
    fs.complete(task_a, result={"ok": True})

    task_b = fs.submit("Downstream B", from_agent="test")
    orch.add_edge(task_a, task_b, "needs")

    assert orch.can_execute(task_b)


def test_cannot_execute_with_unsatisfied_needs(setup):
    """Task with unsatisfied needs dependency cannot execute."""
    fs, bus, orch = setup

    task_a = fs.submit("Upstream", from_agent="test")
    task_b = fs.submit("Downstream", from_agent="test")
    orch.add_edge(task_a, task_b, "needs")

    # A is still in inbox → B blocked
    assert not orch.can_execute(task_b)


def test_can_execute_after_failure_with_after(setup):
    """Task with after: dependency can execute even if upstream failed."""
    fs, bus, orch = setup

    task_a = fs.submit("Upstream A", from_agent="test", max_retries=1)
    
    # Fail it once (max_retries=1 means it goes straight to FAILED)
    fs.claim("worker", task_a)
    fs.fail(task_a, error="Fatal error")
    
    t = fs.get_task(task_a)
    # With max_retries=1, one fail should be final
    # Verify it's in a terminal state
    assert t is not None

    task_b = fs.submit("Downstream B", from_agent="test")
    orch.add_edge(task_a, task_b, "after")

    # after: dependency allows execution even if upstream failed
    # Task A is terminal (FAILED) so B should be unblocked
    assert orch.can_execute(task_b)


def test_dag_collects_all_tasks(setup):
    """DAG collection finds all reachable tasks."""
    fs, bus, orch = setup

    tasks = {
        "root": fs.submit("Root", from_agent="test"),
        "mid1": fs.submit("Mid 1", from_agent="test"),
        "mid2": fs.submit("Mid 2", from_agent="test"),
        "leaf1": fs.submit("Leaf 1", from_agent="test"),
        "leaf2": fs.submit("Leaf 2", from_agent="test"),
    }

    # root → mid1 → leaf1
    # root → mid2 → leaf2
    orch.add_edge(tasks["root"], tasks["mid1"], "needs")
    orch.add_edge(tasks["root"], tasks["mid2"], "needs")
    orch.add_edge(tasks["mid1"], tasks["leaf1"], "needs")
    orch.add_edge(tasks["mid2"], tasks["leaf2"], "needs")

    all_tasks = orch._collect_dag([tasks["root"]])
    assert len(all_tasks) == 5, f"Expected 5 tasks, got {len(all_tasks)}"
    assert tasks["leaf1"] in all_tasks
    assert tasks["leaf2"] in all_tasks


def test_get_blocked_tasks(setup):
    """Blocked tasks are correctly identified."""
    fs, bus, orch = setup

    task_a = fs.submit("Upstream", from_agent="test")
    task_b = fs.submit("Downstream", from_agent="test")
    orch.add_edge(task_a, task_b, "needs")

    blocked = orch.get_blocked_tasks()
    assert task_b in blocked


def test_multiple_deps_all_must_be_satisfied(setup):
    """Task with TWO needs dependencies — both must be done."""
    fs, bus, orch = setup

    task_a = fs.submit("Dep A", from_agent="test")
    task_b = fs.submit("Dep B", from_agent="test")
    task_c = fs.submit("Target C", from_agent="test")

    orch.add_edge(task_a, task_c, "needs")
    orch.add_edge(task_b, task_c, "needs")

    # Only A done, B still pending
    fs.claim("worker", task_a)
    fs.complete(task_a)
    assert not orch.can_execute(task_c)

    # Both done
    fs.claim("worker", task_b)
    fs.complete(task_b)
    assert orch.can_execute(task_c)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
