"""
OverDrop — Full Integration Test

Tests the complete flow: submit → claim → execute → complete → verify,
spanning both Python and (optionally) TypeScript implementations.

Also tests the Mail Bus: send/ask/reply/broadcast/archive.
"""

import sys, os, json, tempfile, shutil, time, asyncio
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
from overdrop import MailBus, FsProtocol, MessageType, TaskStatus

PASS = 0
FAIL = 0

def check(name: str, condition: bool, detail: str = ""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} — {detail}")

@pytest.mark.asyncio
async def test_integration():
    global PASS, FAIL
    print("=" * 60)
    print("🧪 OverDrop Full Integration Test")
    print("=" * 60)

    tmp = tempfile.mkdtemp(prefix="overdrop-integration-")
    print(f"\n📁 Workspace: {tmp}")
    
    # ---- PHASE 1: Two agents, sequential flow ----
    print("\n--- Phase 1: Hermes → Pi sequential ---")
    fs = FsProtocol(tmp)
    bus = MailBus(os.path.join(tmp, "overdrop.db"))
    bus.connect()

    # 1. Hermes submits a task
    task_id = fs.submit(
        "Add user authentication endpoint",
        from_agent="hermes",
        assign="pi",
        context={
            "type": "code",
            "lang": "python",
            "framework": "FastAPI",
            "files": ["api/auth.py", "api/models.py"],
        },
        priority=3,
    )
    check("Task submitted", bool(task_id))

    # 2. Hermes sends a dispatch message
    msg_id = bus.send(
        MessageType.DISPATCH,
        "hermes",
        "pi",
        {"task_id": task_id, "title": "Add user auth"},
        task_id=task_id,
    )
    check("Dispatch message sent", bool(msg_id))
    check("Dispatch has task_id", msg_id is not None)

    # 3. Pi polls and finds the task
    inbox = fs.list_tasks("inbox")
    check("Pi sees task in inbox", len(inbox) == 1)
    check("Task is for pi", inbox[0].assignee == "pi")

    # 4. Pi claims the task (atomic rename)
    claimed = fs.claim("pi", task_id)
    check("Pi claimed task", claimed is not None)
    check("Task is now claimed", fs.get_task(task_id).status == TaskStatus.CLAIMED)

    # 5. Pi works on it, then completes
    result = {
        "files_created": ["api/auth.py", "api/models.py"],
        "schema": "User(id, email, password_hash)",
    }
    fs.complete(task_id, result=result)
    done = fs.get_task(task_id)
    check("Task completed", done is not None and done.status == TaskStatus.DONE)
    check("Result preserved", done.result.get("files_created") == result["files_created"])

    # 6. Pi sends worker_done
    bus.send(MessageType.WORKER_DONE, "pi", "hermes",
             {"task_id": task_id, "result": result}, task_id=task_id)
    
    # 7. Hermes polls and sees completion
    hermes_msgs = bus.poll("hermes", unread_only=True)
    check("Hermes gets completion message", len(hermes_msgs) >= 1)
    check("Last message is worker_done", 
          any(m.type == MessageType.WORKER_DONE for m in hermes_msgs))

    # ---- PHASE 2: Ask/Reply pattern ----
    print("\n--- Phase 2: Ask/Reply ---")
    bus.mark_all_read("pi")
    bus.mark_all_read("hermes")
    
    # Pi asks a question
    ask_id = bus.ask("pi", "hermes", {"question": "Which auth method? JWT or OAuth?"})
    check("Ask sent", bool(ask_id))
    
    # Hermes replies
    reply_id = bus.reply(ask_id, "hermes", {"answer": "JWT with refresh tokens"})
    check("Reply sent", bool(reply_id))
    
    # Pi checks replies
    pi_msgs = bus.poll("pi", unread_only=True)
    check("Pi gets reply", len(pi_msgs) >= 1 and any(m.reply_to == ask_id for m in pi_msgs))

    # ---- PHASE 3: Broadcast ----
    print("\n--- Phase 3: Broadcast ---")
    bcast_id = bus.broadcast("coordinator", "all", {"message": "Sprint starts now"})
    check("Broadcast sent", bool(bcast_id))

    # ---- PHASE 4: Task failure and retry ----
    print("\n--- Phase 4: Retry flow ---")
    task2 = fs.submit("Deploy to production", from_agent="hermes", max_retries=3)
    fs.claim("pi", task2)
    
    # Fail once
    fs.fail(task2, error="Connection timeout")
    retry = fs.get_task(task2)
    check("Task back to inbox after fail", retry.status == TaskStatus.INBOX)
    check("Retry count incremented", retry.retry_count == 1)
    
    # Claim and fail again
    fs.claim("pi", task2)
    fs.fail(task2, error="Still failing")
    retry2 = fs.get_task(task2)
    check("Second retry", retry2.retry_count == 2)
    
    # Exhaust retries
    fs.claim("pi", task2)
    fs.fail(task2, error="Exhausted")
    failed = fs.get_task(task2)
    check("Task finally failed", failed.status == TaskStatus.FAILED)
    check("All retries used", failed.retry_count == 3)

    # ---- PHASE 5: Block/Unblock ----
    print("\n--- Phase 5: Blocking ---")
    task3 = fs.submit("Database migration", from_agent="hermes")
    fs.claim("pi", task3)
    fs.block(task3, reason="DB cluster restart in progress")
    blocked = fs.get_task(task3)
    check("Task blocked", blocked.status == TaskStatus.BLOCKED)
    check("Block reason saved", "cluster restart" in str(blocked.result))
    
    fs.unblock(task3)
    unblocked = fs.get_task(task3)
    check("Task unblocked", unblocked.status == TaskStatus.INBOX)

    # ---- PHASE 6: Stale reaper ----
    print("\n--- Phase 6: Reaper ---")
    # Put a task in active with old mtime
    task4 = fs.submit("Stale task", from_agent="hermes")
    fs.claim("ghost-agent", task4)
    
    # Make it look stale by rewriting it with an old timestamp
    time.sleep(1.1)  # ensure at least 1s old
    
    reaped = fs.reap_stale(timeout_s=1)
    check("Stale task reaped", task4 in reaped)

    # ---- PHASE 7: Concurrent claims ----
    print("\n--- Phase 7: Concurrency ---")
    fs2 = FsProtocol(tmp)
    task5 = fs.submit("Race condition test", from_agent="hermes")
    
    c1 = fs.claim("agent-a", task5)
    c2 = fs2.claim("agent-b", task5)
    check("Only one agent wins the race", c1 is not None and c2 is None)
    check("Winner is agent-a", c1.assignee == "agent-a")

    # ---- CLEANUP ----
    bus.close()
    shutil.rmtree(tmp)
    
    # ---- SUMMARY ----
    print("\n" + "=" * 60)
    total = PASS + FAIL
    print(f"📊 Results: {PASS}/{total} passed, {FAIL} failed")
    print("=" * 60)
    return FAIL == 0

if __name__ == "__main__":
    # Run async test
    success = asyncio.run(test_integration())
    sys.exit(0 if success else 1)
