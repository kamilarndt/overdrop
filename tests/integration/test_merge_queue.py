"""OverDrop — Merge Queue & Worktree Tests (pytest)"""
import os
import sys
import tempfile
import asyncio
import uuid
import subprocess as sp
import shutil
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "python"))
from overdrop.worktree import WorktreeManager, MergeQueue
from overdrop import FsProtocol


def setup_git(path):
    os.makedirs(path, exist_ok=True)
    sp.run(["git", "init"], cwd=path, check=True, capture_output=True)
    sp.run(["git", "config", "user.email", "test@overdrop.io"], cwd=path, capture_output=True)
    sp.run(["git", "config", "user.name", "OverDrop"], cwd=path, capture_output=True)
    with open(os.path.join(path, "README.md"), "w") as f:
        f.write("# test\n")
    sp.run(["git", "add", "-A"], cwd=path, capture_output=True)
    sp.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True)
    return path


@pytest.fixture
def git_repo():
    tmp = tempfile.mkdtemp(prefix="od-git-")
    repo = os.path.join(tmp, "repo")
    setup_git(repo)
    yield repo
    shutil.rmtree(tmp)


@pytest.fixture
def wt_manager(git_repo):
    tmp = tempfile.mkdtemp(prefix="od-wtm-")
    wt_root = os.path.join(tmp, "worktrees")
    wm = WorktreeManager(git_repo, worktree_root=wt_root)
    yield wm
    shutil.rmtree(tmp)


@pytest.fixture
def merge_queue(git_repo):
    tmp = tempfile.mkdtemp(prefix="od-mqt-")
    db = os.path.join(tmp, "od.db")
    mq = MergeQueue(git_repo, base_branch="main", db_path=db)
    yield mq
    mq.close()
    shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# A. FIFO Order
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fifo_order_respected(wt_manager, merge_queue):
    """5 merge requests → must process in FIFO order."""
    tasks = []
    for i in range(5):
        tid = f"task-{uuid.uuid4().hex[:8]}"
        wt = wt_manager.create(tid, f"agent-{i}")
        with open(os.path.join(wt, f"file{i}.py"), "w") as f:
            f.write(f"# task {i}\n")
        wt_manager.commit_changes(tid, f"Add file {i}", f"agent-{i}")
        branch = f"od/agent-{i}/{tid[:8]}"
        merge_queue.enqueue(tid, branch, wt, f"agent-{i}")
        tasks.append(tid)

    order = []
    for _ in range(5):
        result = await merge_queue.process_next()
        if result and result.status == "merged":
            order.append(result.task_id)

    assert order == tasks, f"FIFO order violated: {order} != {tasks}"


# ---------------------------------------------------------------------------
# B. Tier 1 Auto-Merge
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tier1_auto_merge(wt_manager, merge_queue):
    """Two worktrees editing DIFFERENT files → auto-merge success."""
    t1 = f"task-{uuid.uuid4().hex[:8]}"
    t2 = f"task-{uuid.uuid4().hex[:8]}"

    wt1 = wt_manager.create(t1, "pi")
    with open(os.path.join(wt1, "auth.py"), "w") as f:
        f.write("def login(): pass\n")
    wt_manager.commit_changes(t1, "Add auth", "pi")

    wt2 = wt_manager.create(t2, "hermes")
    with open(os.path.join(wt2, "db.py"), "w") as f:
        f.write("def connect(): pass\n")
    wt_manager.commit_changes(t2, "Add db", "hermes")

    b1 = f"od/pi/{t1[:8]}"
    b2 = f"od/hermes/{t2[:8]}"

    merge_queue.enqueue(t1, b1, wt1, "pi")
    merge_queue.enqueue(t2, b2, wt2, "hermes")

    r1 = await merge_queue.process_next()
    assert r1.status == "merged"

    r2 = await merge_queue.process_next()
    assert r2.status == "merged"


# ---------------------------------------------------------------------------
# C. Worktree Isolation
# ---------------------------------------------------------------------------

def test_worktree_isolation(wt_manager, git_repo):
    """Agent A changes in worktree not visible to agent B before merge."""
    t1 = f"task-{uuid.uuid4().hex[:8]}"
    t2 = f"task-{uuid.uuid4().hex[:8]}"

    wt1 = wt_manager.create(t1, "agent-a")
    with open(os.path.join(wt1, "secret.py"), "w") as f:
        f.write("SECRET = 'agent-a-work'\n")
    wt_manager.commit_changes(t1, "Add secret", "agent-a")

    wt2 = wt_manager.create(t2, "agent-b")
    # agent-b should NOT see secret.py
    assert not os.path.exists(os.path.join(wt2, "secret.py")), \
        "Isolation broken: agent-b sees agent-a's unmerged changes!"

    # But README.md from initial commit should be visible to both
    assert os.path.exists(os.path.join(wt2, "README.md"))


# ---------------------------------------------------------------------------
# D. Conflict Detection
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_conflict_detection(wt_manager, merge_queue):
    """Same file edited → conflict detected, not merged."""
    t1 = f"task-{uuid.uuid4().hex[:8]}"
    t2 = f"task-{uuid.uuid4().hex[:8]}"

    wt1 = wt_manager.create(t1, "agent-a")
    with open(os.path.join(wt1, "shared.py"), "w") as f:
        f.write("def f(): return 'a'\n")
    wt_manager.commit_changes(t1, "Version a", "agent-a")

    wt2 = wt_manager.create(t2, "agent-b")
    with open(os.path.join(wt2, "shared.py"), "w") as f:
        f.write("def f(): return 'b'\n")
    wt_manager.commit_changes(t2, "Version b", "agent-b")

    b1 = f"od/agent-a/{t1[:8]}"
    b2 = f"od/agent-b/{t2[:8]}"

    merge_queue.enqueue(t1, b1, wt1, "agent-a")
    result1 = await merge_queue.process_next()
    assert result1.status == "merged"  # first one goes through

    merge_queue.enqueue(t2, b2, wt2, "agent-b")
    result2 = await merge_queue.process_next()
    # Should fail with conflict
    assert result2.status in ("conflict", "failed"), \
        f"Expected conflict, got {result2.status}"


# ---------------------------------------------------------------------------
# E. Merge Status Tracking
# ---------------------------------------------------------------------------

def test_merge_status_tracking(merge_queue):
    """Merge queue stores and retrieves status correctly."""
    merge_queue.enqueue("task-xyz", "branch/xyz", "/tmp/wt", "agent")
    status = merge_queue.get_status("task-xyz")
    assert status is not None
    assert status["status"] == "pending"
    assert status["agent_id"] == "agent"


# ---------------------------------------------------------------------------
# F. Priority-based ordering
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_priority_ordering(wt_manager, merge_queue):
    """High priority merges should be processed before low."""
    tasks_data = [
        ("task-low", "agent-low", 10),
        ("task-high", "agent-high", 1),
        ("task-med", "agent-med", 5),
    ]

    for tid, agent, prio in tasks_data:
        wt = wt_manager.create(tid, agent)
        with open(os.path.join(wt, f"{agent}.py"), "w") as f:
            f.write(f"# {agent}\n")
        wt_manager.commit_changes(tid, f"File from {agent}", agent)
        branch = f"od/{agent}/{tid[:8]}"
        merge_queue.enqueue(tid, branch, wt, agent, priority=prio)

    # First processed should be highest priority (1 = highest)
    # NOTE: queue sorts by (-priority, created_at), all enqueued simultaneously
    # Task with priority=1 comes first
    results = []
    for _ in range(3):
        r = await merge_queue.process_next()
        if r:
            results.append(r.task_id)
    
    # task-high (priority=1) should be first
    assert results[0] == "task-high", f"Expected task-high first, got {results}"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--asyncio-mode=auto"])
