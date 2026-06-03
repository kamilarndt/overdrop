"""OverDrop — Full End-to-End Test

Simulates a real workflow:
  Hermes submits task → Pi claims → works → completes
  MailBus: dispatch → ask → reply → worker_done
  Retry flow: fail → retry → done
  Concurrency: 5 agents racing for 5 tasks
"""
import os, sys, tempfile, asyncio, uuid, time, threading, shutil
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
from overdrop import FsProtocol, MailBus, TaskStatus, MessageType


@pytest.fixture
def e2e():
    tmp = tempfile.mkdtemp(prefix="od-e2e-")
    fs = FsProtocol(tmp)
    bus = MailBus(os.path.join(tmp, "od.db"))
    bus.connect()
    yield fs, bus, tmp
    bus.close()
    shutil.rmtree(tmp)


def test_e2e_hermes_pi_workflow(e2e):
    """End-to-end: Hermes → Pi workflow with mail bus + FS protocol."""
    fs, bus, _ = e2e

    # 1. Hermes submits task
    task_id = fs.submit(
        "Implement user authentication",
        from_agent="hermes",
        assign="pi",
        context={"type": "code", "lang": "python", "framework": "FastAPI"},
        priority=3,
    )

    # 2. Hermes sends dispatch via mail bus
    bus.send(MessageType.DISPATCH, "hermes", "pi",
             {"task_id": task_id, "title": "Implement user auth"},
             task_id=task_id)

    # 3. Pi polls inbox
    inbox = fs.list_tasks("inbox")
    assert len(inbox) >= 1

    # 4. Pi claims
    claimed = fs.claim("pi", task_id)
    assert claimed is not None

    # 5. Pi asks question
    ask_id = bus.ask("pi", "hermes",
                     {"question": "JWT or OAuth?", "task_id": task_id},
                     task_id=task_id)

    # 6. Hermes replies
    bus.reply(ask_id, "hermes", {"answer": "JWT with refresh tokens"})

    # 7. Pi completes
    result = {
        "files": ["api/auth.py", "api/models.py", "api/middleware.py"],
        "tests": 12,
        "test_status": "all green",
    }
    fs.complete(task_id, result=result)

    # 8. Pi sends worker_done
    bus.send(MessageType.WORKER_DONE, "pi", "hermes",
             {"task_id": task_id, "result": result}, task_id=task_id)

    # Verify
    task = fs.get_task(task_id)
    assert task.status == TaskStatus.DONE
    assert task.result["files"] == result["files"]
    assert task.result["tests"] == 12

    # Hermes should see completion
    hermes_msgs = bus.poll("hermes", unread_only=True)
    done_msgs = [m for m in hermes_msgs if m.type == MessageType.WORKER_DONE]
    assert len(done_msgs) >= 1


def test_e2e_retry_and_recovery(e2e):
    """E2E task goes through failure → retry → success."""
    fs, bus, _ = e2e

    task_id = fs.submit("Deploy service", from_agent="hermes", max_retries=3)

    # Fail twice
    fs.claim("ops", task_id)
    fs.fail(task_id, error="DNS resolution timeout")
    assert fs.get_task(task_id).status == TaskStatus.INBOX

    fs.claim("ops", task_id)
    fs.fail(task_id, error="Connection refused")
    assert fs.get_task(task_id).status == TaskStatus.INBOX
    assert fs.get_task(task_id).retry_count == 2

    # Succeed on third try
    fs.claim("ops", task_id)
    fs.complete(task_id, result={"deployed": True, "endpoint": "https://api.example.com"})
    assert fs.get_task(task_id).status == TaskStatus.DONE


def test_e2e_concurrent_agents(e2e):
    """5 agents simultaneously claim 5 tasks — each gets one."""
    fs, bus, _ = e2e

    task_ids = []
    for i in range(5):
        tid = fs.submit(f"Concurrent task {i}", from_agent="test", assign="any")
        task_ids.append(tid)

    results = {}
    lock = threading.Lock()

    def worker(agent_name, tid):
        claimed = fs.claim(agent_name, tid)
        if claimed:
            with lock:
                results[agent_name] = tid

    threads = []
    for i, tid in enumerate(task_ids):
        t = threading.Thread(target=worker, args=(f"agent-{i}", tid))
        threads.append(t)
        t.start()

    for t in threads:
        t.join(timeout=5)

    assert len(results) == 5, f"Not all tasks claimed: {len(results)}/5"


def test_e2e_broadcast_and_poll(e2e):
    """E2E broadcast notification to all agents."""
    fs, bus, _ = e2e

    bus.broadcast("coordinator", "all", {"alert": "Sprint deadline in 2h"})

    all_msgs = bus.poll("@all", unread_only=True)
    assert len(all_msgs) >= 1
    assert all_msgs[0].payload.get("alert") == "Sprint deadline in 2h"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
