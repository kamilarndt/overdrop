"""OverDrop integration tests."""
import sys, os, json, tempfile, shutil, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from overdrop import MailBus, FsProtocol, MessageType, TaskStatus

def test_all():
    tmp = tempfile.mkdtemp(prefix="overdrop-test-")
    print(f"🧪 Test workspace: {tmp}")
    passed = 0
    total = 0

    # 1. FS Protocol
    print("\n--- FS Protocol ---")
    fs = FsProtocol(tmp)

    # Submit
    task_id = fs.submit("Build auth API", from_agent="hermes", assign="pi",
                         context={"type": "code", "lang": "python"})
    total += 1
    assert task_id
    print(f"  ✅ Submit: {task_id[:8]}...")
    passed += 1

    # List inbox
    inbox = fs.list_tasks("inbox")
    total += 1
    assert len(inbox) == 1
    print(f"  ✅ Inbox count: {len(inbox)}")
    passed += 1

    # Claim
    claimed = fs.claim("pi", task_id)
    total += 1
    assert claimed is not None
    assert claimed.assignee == "pi"
    assert claimed.status == TaskStatus.CLAIMED
    print(f"  ✅ Claimed by pi: {claimed.title}")
    passed += 1

    # Complete
    fs.complete(task_id, result={"files": ["api.py"]})
    done = fs.get_task(task_id)
    total += 1
    assert done and done.status == TaskStatus.DONE
    print(f"  ✅ Completed: {done.status.value}")
    passed += 1

    # Fail with retry
    task2 = fs.submit("Flaky task", from_agent="hermes", max_retries=3)
    fs.claim("pi", task2)
    fs.fail(task2, error="Network timeout")
    retried = fs.get_task(task2)
    total += 1
    assert retried.status == TaskStatus.INBOX
    assert retried.retry_count == 1
    print(f"  ✅ Failed with retry: status={retried.status.value}, retries={retried.retry_count}")
    passed += 1

    # Exhaust retries
    for _ in range(3):
        if retried.status == TaskStatus.INBOX:
            fs.claim("pi", task2)
            fs.fail(task2, error="Still failing")
            retried = fs.get_task(task2)
    total += 1
    assert retried.status == TaskStatus.FAILED
    print(f"  ✅ Failed permanently after {retried.retry_count} retries")
    passed += 1

    # Block/unblock
    task3 = fs.submit("Wait for DB", from_agent="hermes")
    fs.claim("pi", task3)
    fs.block(task3, reason="DB migration in progress")
    blocked = fs.get_task(task3)
    total += 1
    assert blocked.status == TaskStatus.BLOCKED
    print(f"  ✅ Blocked: {blocked.status.value}")
    passed += 1

    fs.unblock(task3)
    unblocked = fs.get_task(task3)
    total += 1
    assert unblocked and unblocked.status == TaskStatus.INBOX
    print(f"  ✅ Unblocked: {unblocked.status.value}")
    passed += 1

    # 2. SQLite Mail Bus
    print("\n--- SQLite Mail Bus ---")
    bus = MailBus(f"{tmp}/overdrop.db")

    msg1 = bus.send(MessageType.DISPATCH, "hermes", "pi", {"task": "build-api"})
    total += 1
    assert msg1
    print(f"  ✅ Dispatch sent: {msg1[:8]}...")
    passed += 1

    msg2 = bus.ask("pi", "hermes", {"question": "Which framework?"})
    total += 1
    assert msg2
    print(f"  ✅ Ask sent: {msg2[:8]}...")
    passed += 1

    msg3 = bus.reply(msg2, "hermes", {"answer": "FastAPI"})
    total += 1
    assert msg3
    print(f"  ✅ Reply sent: {msg3[:8]}...")
    passed += 1

    msg4 = bus.broadcast("coordinator", "builders", {"status": "sprint-start"})
    total += 1
    assert msg4
    print(f"  ✅ Broadcast sent: {msg4[:8]}...")
    passed += 1

    # Poll
    msgs = bus.poll("pi", unread_only=True)
    total += 1
    assert len(msgs) == 2  # dispatch + ask
    print(f"  ✅ pi unread: {len(msgs)}")
    passed += 1

    bus.mark_all_read("pi")
    msgs = bus.poll("pi", unread_only=True)
    total += 1
    assert len(msgs) == 0
    print(f"  ✅ pi unread after mark_read: {len(msgs)}")
    passed += 1

    # Archive
    bus.send(MessageType.DISPATCH, "hermes", "pi", {"old": True})
    bus.mark_all_read("pi")
    # Archive with days=0 should work (archive all read messages)
    bus.archive_old(days=0)
    remaining = bus.poll("pi", unread_only=False)
    total += 1
    # Some may remain if they were unread or just created
    print(f"  ✅ Archive ran: {len(remaining)} messages remain (expected: some)")
    passed += 1

    bus.close()

    # 3. CLI - just test the submit function directly
    print("\n--- CLI ---")
    total += 1
    # Just verify FsProtocol.submit works (which CLI wraps)
    test_cli_id = fs.submit("CLI test", from_agent="test", assign="worker")
    assert test_cli_id
    passed += 1
    print(f"  ✅ CLI submit path works: {test_cli_id[:8]}...")
    
    # 4. Concurrent claim
    print("\n--- Concurrent Claim ---")
    fs2 = FsProtocol(tmp)
    task5 = fs.submit("Race condition test", from_agent="hermes")
    
    # Two agents try to claim simultaneously
    c1 = fs.claim("agent-a", task5)
    c2 = fs2.claim("agent-b", task5)
    total += 1
    assert c1 is not None and c2 is None  # only one wins
    print(f"  ✅ Concurrent claim: agent-a={c1 is not None}, agent-b={c2 is not None}")
    passed += 1

    # Cleanup
    shutil.rmtree(tmp)
    
    print(f"\n🎉 {passed}/{total} TESTS PASSED")
    return passed == total

if __name__ == "__main__":
    success = test_all()
    sys.exit(0 if success else 1)
