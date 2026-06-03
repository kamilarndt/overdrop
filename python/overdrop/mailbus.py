"""
OverDrop — SQLite Mail Bus
    
Główna szyna komunikacyjna. Używana do:
- Typowanego routingu (dispatch, ask, escalate, etc.)
- Kolejkowania wiadomości z priorytetami
- Threadingu (reply_to)
- Broadcastu (@all, @builders, room:<run>)
    
SQLite w trybie WAL dla współbieżności 1-5ms.
    
UWAGA: Tylko wiadomości, nie stan zadań. 
Stan zadań = FsProtocol (system plików).
"""

import json
import os
import sqlite3
import struct
import uuid
from datetime import datetime, timezone
from typing import Optional
from .types import Message, MessageType


def _uuid7() -> str:
    """UUID v7 (time-ordered) — idealny dla SQLite clustered PK.

    Format:
    - 48 bits: Unix timestamp in milliseconds
    - 4 bits: version (0111 = 7)
    - 62 bits: random data (with variant bits)

    Monotonically increasing = perfect for B-tree indexes.
    """
    # Current time in milliseconds
    ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    # 48-bit timestamp
    ts_bytes = struct.pack(">Q", ts_ms)[2:]  # 6 bytes

    # Version bits (0111) + 12 random bits
    rand1 = os.urandom(2)
    rand1_byte = bytes([0x70 | (rand1[0] & 0x0F)])  # version 7

    # Variant bits (10xx) + 6 random bytes
    rand2 = os.urandom(8)
    rand2_byte = bytes([0x80 | (rand2[0] & 0x3F)])  # variant 1

    # Combine: 6 bytes timestamp + 1 byte version + 2 bytes random + 1 byte variant + 7 bytes random
    uuid_bytes = ts_bytes + rand1_byte + rand1[1:2] + rand2_byte + rand2[1:8]

    # Format as UUID string
    return str(uuid.UUID(bytes=uuid_bytes))


class MailBus:
    """SQLite-backed message bus for inter-agent communication."""
    
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
    
    def connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._init_schema()
        return self._conn
    
    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
    
    def _init_schema(self):
        """Initialize Mail Bus tables."""
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id          TEXT PRIMARY KEY,
                type        TEXT NOT NULL,
                sender      TEXT NOT NULL,
                recipient   TEXT NOT NULL,
                payload     TEXT DEFAULT '{}',
                reply_to    TEXT,
                task_id     TEXT,
                run_id      TEXT,
                priority    INTEGER DEFAULT 5,
                read        INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_mbox_recipient_unread 
                ON messages(recipient, read);
            CREATE INDEX IF NOT EXISTS idx_mbox_created_at 
                ON messages(created_at);
        """)
    
    def send(self, msg_type: MessageType, sender: str, recipient: str,
             payload: dict = None, reply_to: str = None,
             task_id: str = None, priority: int = 5) -> str:
        """Send a message. Returns message ID."""
        conn = self.connect()
        msg_id = _uuid7()
        conn.execute(
            """INSERT INTO messages (id, type, sender, recipient, payload, reply_to, task_id, priority)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (msg_id, msg_type.value, sender, recipient,
             json.dumps(payload or {}), reply_to, task_id, priority)
        )
        conn.commit()
        return msg_id
    
    def ask(self, sender: str, recipient: str, payload: dict = None,
            task_id: str = None) -> str:
        """Send a blocking ask. Recipient should reply with reply_to."""
        return self.send(MessageType.ASK, sender, recipient, payload, task_id=task_id)
    
    def reply(self, reply_to_msg_id: str, sender: str, payload: dict = None) -> str:
        """Reply to a previous message (continues thread)."""
        conn = self.connect()
        row = conn.execute("SELECT sender FROM messages WHERE id=?", (reply_to_msg_id,)).fetchone()
        if not row:
            raise ValueError(f"Message {reply_to_msg_id} not found")
        return self.send(MessageType.REPLY, sender, row["sender"],
                         payload, reply_to=reply_to_msg_id)
    
    def broadcast(self, sender: str, group: str, payload: dict = None,
                  task_id: str = None) -> str:
        """Broadcast message to all agents in a group (@all, @builders, room:<run>)."""
        return self.send(MessageType.BROADCAST, sender, f"@{group}",
                         payload, task_id=task_id)
    
    def poll(self, recipient: str, unread_only: bool = True,
             limit: int = 50) -> list[Message]:
        """Poll messages for an agent."""
        conn = self.connect()
        if unread_only:
            rows = conn.execute(
                """SELECT * FROM messages 
                   WHERE recipient=? AND read=0 
                   ORDER BY priority ASC, created_at ASC LIMIT ?""",
                (recipient, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM messages 
                   WHERE recipient=? 
                   ORDER BY created_at DESC LIMIT ?""",
                (recipient, limit)
            ).fetchall()
        return [self._row_to_msg(r) for r in rows]
    
    def mark_read(self, msg_id: str):
        """Mark a message as read."""
        self.connect().execute("UPDATE messages SET read=1 WHERE id=?", (msg_id,))
        self.connect().commit()
    
    def mark_all_read(self, recipient: str):
        """Mark all messages for a recipient as read."""
        self.connect().execute(
            "UPDATE messages SET read=1 WHERE recipient=?", (recipient,))
        self.connect().commit()
    
    def archive_old(self, days: int = 7):
        """Archive read messages older than X days.
        
        This is the cleanup mechanism so the messages table doesn't grow unbounded.
        """
        conn = self.connect()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages_archive AS SELECT * FROM messages WHERE 0
        """)
        conn.execute(
            """INSERT INTO messages_archive SELECT * FROM messages 
               WHERE read=1 AND created_at < datetime('now', ?)""",
            (f"-{days} days",)
        )
        conn.execute(
            """DELETE FROM messages WHERE read=1 AND created_at < datetime('now', ?)""",
            (f"-{days} days",)
        )
        conn.commit()
    
    def count_unread(self, recipient: str) -> int:
        """Get unread count for an agent."""
        row = self.connect().execute(
            "SELECT COUNT(*) FROM messages WHERE recipient=? AND read=0",
            (recipient,)
        ).fetchone()
        return row[0]
    
    def _row_to_msg(self, row) -> Message:
        return Message(
            id=row["id"],
            type=MessageType(row["type"]),
            sender=row["sender"],
            recipient=row["recipient"],
            payload=json.loads(row["payload"] or "{}"),
            reply_to=row["reply_to"],
            task_id=row["task_id"],
            priority=row["priority"],
            read=bool(row["read"]),
            created_at=row["created_at"],
        )
    
    def __enter__(self):
        self.connect()
        return self
    
    def __exit__(self, *args):
        self.close()
