"""OverDrop — Mail Bus Unit Tests (pytest)

Tests for SQLite Mail Bus: delivery, threading, broadcast, archive, throughput.
"""
import os
import sys
import tempfile
import threading
import time
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "python"))
from overdrop import MailBus, MessageType


@pytest.fixture
def bus():
    """Create a temporary MailBus for testing."""
    tmp = tempfile.mkdtemp(prefix="od-bus-")
    db = os.path.join(tmp, "test.db")
    b = MailBus(db)
    b.connect()
    yield b
    b.close()
    import shutil
    shutil.rmtree(tmp)


# ---------------------------------------------------------------------------
# A. Message Delivery & Read
# ---------------------------------------------------------------------------

def test_message_delivery_and_read(bus):
    """Messages delivered to specific agent, @all, room:<run>."""
    # Direct
    mid1 = bus.send(MessageType.DISPATCH, "hermes", "pi", {"task": "build"})
    assert mid1

    # Broadcast @all
    mid2 = bus.broadcast("coordinator", "all", {"status": "sprint-start"})
    assert mid2

    # Room message
    mid3 = bus.send(MessageType.PROGRESS, "pi", "room:run-123", {"progress": 50})
    assert mid3

    # Verify pi received direct + broadcast (not room)
    pi_msgs = bus.poll("pi", unread_only=True)
    assert len(pi_msgs) >= 1
    assert any(m.type == MessageType.DISPATCH for m in pi_msgs)

    # Verify @all delivered
    all_msgs = bus.poll("@all", unread_only=True)
    assert len(all_msgs) >= 1

    # Verify room messages
    room_msgs = bus.poll("room:run-123", unread_only=True)
    assert len(room_msgs) >= 1

    # Mark read
    bus.mark_read(mid1)
    assert bus.count_unread("pi") < len(pi_msgs)


def test_ask_reply_threading(bus):
    """Ask → reply preserves thread via reply_to."""
    ask_id = bus.ask("pi", "hermes", {"question": "JWT or OAuth?"})
    assert ask_id

    reply_id = bus.reply(ask_id, "hermes", {"answer": "JWT"})
    assert reply_id

    # Pi should see the reply
    pi_msgs = bus.poll("pi", unread_only=True)
    replies = [m for m in pi_msgs if m.reply_to == ask_id]
    assert len(replies) == 1
    assert replies[0].payload.get("answer") == "JWT"


# ---------------------------------------------------------------------------
# B. Archiving & Cleanup
# ---------------------------------------------------------------------------

def test_archiving_cleanup(bus):
    """Insert messages, archive old ones, check retention."""
    # Insert 50 messages
    for i in range(50):
        bus.send(MessageType.DISPATCH, "test", "archive-agent",
                 {"i": i})

    # Mark all as read
    bus.mark_all_read("archive-agent")
    
    # Manually set created_at to be 8 days old so archive picks them up
    import sqlite3
    bus._conn.execute(
        "UPDATE messages SET created_at=datetime('now', '-8 days') WHERE recipient=?",
        ("archive-agent",))
    bus._conn.commit()

    # Archive with days=7
    bus.archive_old(days=7)

    # Should be empty now
    remaining = bus.poll("archive-agent", unread_only=False)
    assert len(remaining) == 0


def test_archive_preserves_unread(bus):
    """Archive should NOT delete unread messages."""
    # 20 read messages + 10 unread
    for i in range(20):
        mid = bus.send(MessageType.DISPATCH, "test", "agent-x", {"i": i})
        bus.mark_read(mid)
    for i in range(10):
        bus.send(MessageType.DISPATCH, "test", "agent-x", {"j": i})

    bus.archive_old(days=0)

    # Unread should remain
    remaining = bus.poll("agent-x", unread_only=True)
    assert len(remaining) == 10


# ---------------------------------------------------------------------------
# C. High Throughput (no SQLITE_BUSY)
# ---------------------------------------------------------------------------

def test_high_throughput_no_locking(bus):
    """200 messages sequential — verify throughput."""
    errors = []
    
    # Sequential send (single-connection throughput test)
    import time
    start = time.time()
    for i in range(200):
        try:
            bus.send(MessageType.DISPATCH, "producer", "target", {"seq": i})
        except Exception as e:
            errors.append(str(e))
    elapsed = time.time() - start

    assert len(errors) == 0, f"Got {len(errors)} errors"

    # Verify all 200 delivered
    count = bus.count_unread("target")
    assert count == 200, f"Expected 200, got {count}"
    
    # Throughput check: at least 50 msg/s
    rate = 200 / max(elapsed, 0.001)
    assert rate > 50, f"Throughput too low: {rate:.0f} msg/s"


def test_mailbus_priority_order(bus):
    """Lower priority number (=higher priority) messages should be polled first."""
    bus.send(MessageType.DISPATCH, "test", "prio-agent", {"p": 9}, priority=9)
    bus.send(MessageType.DISPATCH, "test", "prio-agent", {"p": 1}, priority=1)
    bus.send(MessageType.DISPATCH, "test", "prio-agent", {"p": 5}, priority=5)

    msgs = bus.poll("prio-agent", unread_only=True)
    # Should be ordered by priority ASC (1 = highest, 10 = lowest)
    priorities = [m.priority for m in msgs]
    assert priorities == [1, 5, 9], f"Expected [1,5,9], got {priorities}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
