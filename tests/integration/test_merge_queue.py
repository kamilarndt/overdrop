"""OverDrop — Integration Tests for MergeQueue + Worktree

Covers:
- Merge success (no conflicts)
- Merge conflict (Tier 1 — rebase)
- Retry after failed merge
- Worktree cleanup after operations
- Priority ordering
- Cancel merge
- List all entries
- FsProtocol integration (auto_merge, use_worktree)
"""

import sys
import os
import tempfile
import shutil
import subprocess
import uuid
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
from overdrop import WorktreeManager, MergeQueue, FsProtocol


def setup_git(path):
    """Create a minimal git repo for testing."""
    os.makedirs(path, exist_ok=True)
    os.makedirs(os.path.join(path, ".overdrop"), exist_ok=True)
    subprocess.run(["git", "init"], cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@overdrop.dev"],
                   cwd=path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=path, capture_output=True)
    with open(os.path.join(path, "main.py"), "w") as f:
        f.write("def main(): return 'initial'\n")
    subprocess.run(["git", "add", "."], cwd=path, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True)
    return path


# =========================================================================
# A. MERGE SUCCESS (no conflicts)
# =========================================================================

def test_merge_success_no_conflicts():
    """Two branches editing different files → both merge cleanly."""
    tmp = tempfile.mkdtemp(prefix="od-test-")
    repo = setup_git(os.path.join(tmp, "repo"))
    wt = WorktreeManager(repo)
    mq = MergeQueue(repo)

    t1 = f"task-{uuid.uuid4().hex[:8]}"
    t2 = f"task-{uuid.uuid4().hex[:8]}"

    # Branch 1: new file
    wt1 = wt.create(t1, "agent-a")
    with open(os.path.join(wt1, "feature_a.py"), "w") as f:
        f.write("def feature_a(): return 'a'\n")
    wt.commit_changes(t1, "Add feature A", "agent-a")

    # Branch 2: different new file
    wt2 = wt.create(t2, "agent-b")
    with open(os.path.join(wt2, "feature_b.py"), "w") as f:
        f.write("def feature_b(): return 'b'\n")
    wt.commit_changes(t2, "Add feature B", "agent-b")

    # Enqueue and process both
    b1 = f"od/agent-a/{t1[:8]}"
    b2 = f"od/agent-b/{t2[:8]}"

    mq.enqueue(t1, b1, wt1, "agent-a")
    mq.enqueue(t2, b2, wt2, "agent-b")

    r1 = mq.process_next()
    assert r1.status == "merged", f"First merge failed: {r1.error}"

    r2 = mq.process_next()
    assert r2.status == "merged", f"Second merge failed: {r2.error}"

    mq.close()
    shutil.rmtree(tmp)


# =========================================================================
# B. MERGE CONFLICT (Tier 1 — rebase)
# =========================================================================

def test_merge_conflict_tier1():
    """Two branches editing same file → conflict detected."""
    tmp = tempfile.mkdtemp(prefix="od-test-conflict-")
    repo = setup_git(os.path.join(tmp, "repo"))
    wt = WorktreeManager(repo)
    mq = MergeQueue(repo)

    t1 = f"task-{uuid.uuid4().hex[:8]}"
    t2 = f"task-{uuid.uuid4().hex[:8]}"

    # Branch 1: edit main.py
    wt1 = wt.create(t1, "agent-a")
    with open(os.path.join(wt1, "main.py"), "w") as f:
        f.write("def main(): return 'version A'\n")
    wt.commit_changes(t1, "Version A", "agent-a")

    # Branch 2: different edit to same file
    wt2 = wt.create(t2, "agent-b")
    with open(os.path.join(wt2, "main.py"), "w") as f:
        f.write("def main(): return 'version B'\n")
    wt.commit_changes(t2, "Version B", "agent-b")

    # Merge first (succeeds)
    b1 = f"od/agent-a/{t1[:8]}"
    mq.enqueue(t1, b1, wt1, "agent-a")
    r1 = mq.process_next()
    assert r1.status == "merged"

    # Merge second (conflict)
    b2 = f"od/agent-b/{t2[:8]}"
    mq.enqueue(t2, b2, wt2, "agent-b")
    r2 = mq.process_next()
    assert r2.status == "conflict", f"Expected conflict, got {r2.status}"
    assert r2.conflict_level >= 1, f"Expected conflict_level >= 1"

    mq.close()
    shutil.rmtree(tmp)


# =========================================================================
# C. WORKTREE CLEANUP
# =========================================================================

def test_worktree_cleanup_after_merge():
    """Worktree should be cleaned up after successful merge."""
    tmp = tempfile.mkdtemp(prefix="od-test-cleanup-")
    repo = setup_git(os.path.join(tmp, "repo"))
    wt = WorktreeManager(repo)
    mq = MergeQueue(repo)

    t1 = f"task-{uuid.uuid4().hex[:8]}"

    wt1 = wt.create(t1, "agent-a")
    with open(os.path.join(wt1, "cleanup.py"), "w") as f:
        f.write("def cleanup(): return 'done'\n")
    wt.commit_changes(t1, "Cleanup test", "agent-a")

    b1 = f"od/agent-a/{t1[:8]}"
    mq.enqueue(t1, b1, wt1, "agent-a")

    # Verify worktree exists
    assert os.path.exists(wt1), "Worktree should exist before merge"

    # Process merge
    r1 = mq.process_next()
    assert r1.status == "merged"

    # Verify worktree is cleaned up
    assert not os.path.exists(wt1), "Worktree should be cleaned up after merge"

    mq.close()
    shutil.rmtree(tmp)


# =========================================================================
# D. RETRY AFTER CONFLICT
# =========================================================================

def test_retry_after_conflict():
    """Retry a conflicted merge should reset status to pending."""
    tmp = tempfile.mkdtemp(prefix="od-test-retry-")
    repo = setup_git(os.path.join(tmp, "repo"))
    # Use unique worktree root to avoid conflicts
    wt_root = os.path.join(tmp, "worktrees")
    wt = WorktreeManager(repo, worktree_root=wt_root)
    mq = MergeQueue(repo)

    t1 = f"task-{uuid.uuid4().hex[:8]}"
    t2 = f"task-{uuid.uuid4().hex[:8]}"

    # Branch 1
    wt1 = wt.create(t1, "agent-a")
    with open(os.path.join(wt1, "conflict.py"), "w") as f:
        f.write("def conflict(): return 'A'\n")
    wt.commit_changes(t1, "Version A", "agent-a")

    # Branch 2 (conflicts)
    wt2 = wt.create(t2, "agent-b")
    with open(os.path.join(wt2, "conflict.py"), "w") as f:
        f.write("def conflict(): return 'B'\n")
    wt.commit_changes(t2, "Version B", "agent-b")

    # Merge first
    b1 = f"od/agent-a/{t1[:8]}"
    mq.enqueue(t1, b1, wt1, "agent-a")
    r1 = mq.process_next()
    assert r1.status == "merged"

    # Merge second (conflict)
    b2 = f"od/agent-b/{t2[:8]}"
    mq.enqueue(t2, b2, wt2, "agent-b")
    r2 = mq.process_next()
    assert r2.status == "conflict"

    # Retry
    success = mq.retry(t2)
    assert success, "Retry should succeed"

    # Verify status reset
    status = mq.get_status(t2)
    assert status["status"] == "pending", f"Expected pending, got {status['status']}"

    mq.close()
    shutil.rmtree(tmp)


# =========================================================================
# E. CANCEL MERGE
# =========================================================================

def test_cancel_merge():
    """Cancel a pending merge request."""
    tmp = tempfile.mkdtemp(prefix="od-test-cancel-")
    repo = setup_git(os.path.join(tmp, "repo"))
    wt = WorktreeManager(repo)
    mq = MergeQueue(repo)

    t1 = f"task-{uuid.uuid4().hex[:8]}"

    wt1 = wt.create(t1, "agent-a")
    with open(os.path.join(wt1, "cancel.py"), "w") as f:
        f.write("def cancel(): return 'pending'\n")
    wt.commit_changes(t1, "Cancel test", "agent-a")

    b1 = f"od/agent-a/{t1[:8]}"
    mq.enqueue(t1, b1, wt1, "agent-a")

    # Cancel
    success = mq.cancel(t1)
    assert success, "Cancel should succeed"

    # Verify status
    status = mq.get_status(t1)
    assert status["status"] == "cancelled", f"Expected cancelled, got {status['status']}"

    mq.close()
    shutil.rmtree(tmp)


# =========================================================================
# F. LIST ALL ENTRIES
# =========================================================================

def test_list_all_entries():
    """List all merge queue entries regardless of status."""
    tmp = tempfile.mkdtemp(prefix="od-test-list-")
    repo = setup_git(os.path.join(tmp, "repo"))
    wt = WorktreeManager(repo)
    mq = MergeQueue(repo)

    t1 = f"task-{uuid.uuid4().hex[:8]}"
    t2 = f"task-{uuid.uuid4().hex[:8]}"

    wt1 = wt.create(t1, "agent-a")
    with open(os.path.join(wt1, "list_a.py"), "w") as f:
        f.write("def list_a(): return 'a'\n")
    wt.commit_changes(t1, "List test A", "agent-a")

    wt2 = wt.create(t2, "agent-b")
    with open(os.path.join(wt2, "list_b.py"), "w") as f:
        f.write("def list_b(): return 'b'\n")
    wt.commit_changes(t2, "List test B", "agent-b")

    b1 = f"od/agent-a/{t1[:8]}"
    b2 = f"od/agent-b/{t2[:8]}"

    mq.enqueue(t1, b1, wt1, "agent-a")
    mq.enqueue(t2, b2, wt2, "agent-b")

    # Process first
    r1 = mq.process_next()
    assert r1.status == "merged"

    # List all — should show both
    all_entries = mq.list_all()
    assert len(all_entries) == 2, f"Expected 2 entries, got {len(all_entries)}"

    statuses = {e["status"] for e in all_entries}
    assert "merged" in statuses
    assert "pending" in statuses

    mq.close()
    shutil.rmtree(tmp)


# =========================================================================
# G. PRIORITY ORDERING
# =========================================================================

def test_priority_ordering():
    """Higher priority (lower number) should be processed first."""
    tmp = tempfile.mkdtemp(prefix="od-test-priority-")
    repo = setup_git(os.path.join(tmp, "repo"))
    wt = WorktreeManager(repo)
    mq = MergeQueue(repo)

    tasks = []
    for i, priority in enumerate([10, 1, 5]):
        tid = f"task-{uuid.uuid4().hex[:8]}"
        wtp = wt.create(tid, f"agent-{i}")
        with open(os.path.join(wtp, f"file_{i}.py"), "w") as f:
            f.write(f"def f(): return {i}\n")
        wt.commit_changes(tid, f"File {i}", f"agent-{i}")
        branch = f"od/agent-{i}/{tid[:8]}"
        mq.enqueue(tid, branch, wtp, f"agent-{i}", priority=priority)
        tasks.append((tid, priority))

    # Process — should get priority 1 first, then 5, then 10
    r1 = mq.process_next()
    assert r1.status == "merged"
    assert r1.task_id == tasks[1][0], "Expected priority 1 first"

    r2 = mq.process_next()
    assert r2.status == "merged"
    assert r2.task_id == tasks[2][0], "Expected priority 5 second"

    r3 = mq.process_next()
    assert r3.status == "merged"
    assert r3.task_id == tasks[0][0], "Expected priority 10 third"

    mq.close()
    shutil.rmtree(tmp)


# =========================================================================
# H. FSPROTOCOL INTEGRATION — AUTO_MERGE
# =========================================================================

def test_fsprotocol_auto_merge():
    """FsProtocol.complete(auto_merge=True) should enqueue to MergeQueue."""
    tmp = tempfile.mkdtemp(prefix="od-test-fsproto-")
    repo = setup_git(os.path.join(tmp, "repo"))
    wt = WorktreeManager(repo)
    mq = MergeQueue(repo)
    fs = FsProtocol(os.path.join(tmp, "workspace"), merge_queue=mq, worktree_manager=wt)

    # Create and claim a task with worktree
    t1 = fs.submit("Test auto-merge", from_agent="hermes")
    claimed = fs.claim("agent-a", t1, use_worktree=True)

    # Verify worktree was created
    assert claimed.worktree is not None, "Worktree should be created"

    # Create a file in the worktree
    with open(os.path.join(claimed.worktree, "auto.py"), "w") as f:
        f.write("def auto(): return 'merged'\n")

    # Complete with auto_merge
    fs.complete(t1, result={"files": ["auto.py"]}, auto_merge=True)

    # Verify task is in done folder
    done_tasks = fs.list_tasks("done")
    assert any(t.id == t1 for t in done_tasks), "Task should be in done folder"

    # Verify task was enqueued to MergeQueue
    pending = mq.list_pending()
    assert any(p["task_id"] == t1 for p in pending), "Task should be in MergeQueue"

    mq.close()
    shutil.rmtree(tmp)


# =========================================================================
# I. FSPROTOCOL INTEGRATION — SUBMIT_MERGE_READY
# =========================================================================

def test_fsprotocol_submit_merge_ready():
    """FsProtocol.submit_merge_ready() convenience method."""
    tmp = tempfile.mkdtemp(prefix="od-test-merge-ready-")
    repo = setup_git(os.path.join(tmp, "repo"))
    wt = WorktreeManager(repo)
    mq = MergeQueue(repo)
    fs = FsProtocol(os.path.join(tmp, "workspace"), merge_queue=mq, worktree_manager=wt)

    # Create and claim a task
    t1 = fs.submit("Test merge ready", from_agent="hermes")
    claimed = fs.claim("agent-b", t1, use_worktree=True)

    # Create a file
    with open(os.path.join(claimed.worktree, "ready.py"), "w") as f:
        f.write("def ready(): return True\n")

    # Submit merge ready
    fs.submit_merge_ready(t1, result={"status": "ready"})

    # Verify
    pending = mq.list_pending()
    assert any(p["task_id"] == t1 for p in pending)

    mq.close()
    shutil.rmtree(tmp)


# =========================================================================
# J. CLEANUP_AFTER_MERGE (public method)
# =========================================================================

def test_cleanup_after_merge_public():
    """Test the public cleanup_after_merge method."""
    tmp = tempfile.mkdtemp(prefix="od-test-cleanup-pub-")
    repo = setup_git(os.path.join(tmp, "repo"))
    mq = MergeQueue(repo)

    t1 = f"task-{uuid.uuid4().hex[:8]}"

    # Enqueue a task
    mq.enqueue(t1, "od/agent/test", "/tmp/fake-worktree", "agent")

    # Call cleanup (worktree doesn't exist, should not crash)
    mq.cleanup_after_merge(t1, success=True)

    mq.close()
    shutil.rmtree(tmp)
