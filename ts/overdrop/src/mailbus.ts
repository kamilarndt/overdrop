/**
 * OverDrop — SQLite Mail Bus (TypeScript)
 *
 * Synchronous import (no top-level await).
 */

import { randomUUID } from "node:crypto";
import { Message, MessageType } from "./types.js";

export class MailBus {
  private dbPath: string;
  private db: any = null;

  constructor(dbPath: string) {
    this.dbPath = dbPath;
  }

  connect(): void {
    if (this.db) return;
    try {
      // eslint-disable-next-line @typescript-eslint/no-require-imports
      const Database = require("better-sqlite3");
      this.db = new Database(this.dbPath);
      this.db.pragma("journal_mode = WAL");
      this.db.pragma("foreign_keys = ON");
      this.#initSchema();
    } catch {
      throw new Error(
        "better-sqlite3 required. Install: npm install better-sqlite3"
      );
    }
  }

  close(): void {
    if (this.db) {
      this.db.close();
      this.db = null;
    }
  }

  #initSchema(): void {
    this.db.exec(`
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
    `);
  }

  send(
    type: MessageType,
    sender: string,
    recipient: string,
    payload: Record<string, unknown> = {},
    replyTo?: string,
    taskId?: string,
    priority = 5,
  ): string {
    const id = randomUUID();
    this.db
      .prepare(
        `INSERT INTO messages (id, type, sender, recipient, payload, reply_to, task_id, priority)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?)`
      )
      .run(id, type, sender, recipient, JSON.stringify(payload), replyTo || null, taskId || null, priority);
    return id;
  }

  ask(sender: string, recipient: string, payload = {}, taskId?: string): string {
    return this.send(MessageType.ASK, sender, recipient, payload, undefined, taskId);
  }

  reply(replyToMsgId: string, sender: string, payload = {}): string {
    const row = this.db.prepare("SELECT sender FROM messages WHERE id=?").get(replyToMsgId) as
      | { sender: string }
      | undefined;
    if (!row) throw new Error(`Message ${replyToMsgId} not found`);
    return this.send(MessageType.REPLY, sender, row.sender, payload, replyToMsgId);
  }

  broadcast(sender: string, group: string, payload = {}, taskId?: string): string {
    return this.send(MessageType.BROADCAST, sender, `@${group}`, payload, undefined, taskId);
  }

  poll(recipient: string, unreadOnly = true, limit = 50): Message[] {
    let rows: any[];
    if (unreadOnly) {
      rows = this.db
        .prepare(
          `SELECT * FROM messages WHERE recipient=? AND read=0 ORDER BY created_at ASC LIMIT ?`
        )
        .all(recipient, limit);
    } else {
      rows = this.db
        .prepare(
          `SELECT * FROM messages WHERE recipient=? ORDER BY created_at DESC LIMIT ?`
        )
        .all(recipient, limit);
    }
    return rows.map(this.#rowToMsg);
  }

  markRead(msgId: string): void {
    this.db.prepare("UPDATE messages SET read=1 WHERE id=?").run(msgId);
  }

  markAllRead(recipient: string): void {
    this.db.prepare("UPDATE messages SET read=1 WHERE recipient=?").run(recipient);
  }

  archiveOld(days = 7): void {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS messages_archive AS SELECT * FROM messages WHERE 0
    `);
    this.db
      .prepare(
        `INSERT INTO messages_archive SELECT * FROM messages 
         WHERE read=1 AND created_at < datetime('now', ?)`
      )
      .run(`-${days} days`);
    this.db
      .prepare(
        `DELETE FROM messages WHERE read=1 AND created_at < datetime('now', ?)`
      )
      .run(`-${days} days`);
  }

  countUnread(recipient: string): number {
    const row = this.db
      .prepare("SELECT COUNT(*) as count FROM messages WHERE recipient=? AND read=0")
      .get(recipient) as { count: number };
    return row.count;
  }

  #rowToMsg(row: any): Message {
    return {
      id: row.id,
      type: row.type as MessageType,
      sender: row.sender,
      recipient: row.recipient,
      payload: JSON.parse(row.payload || "{}"),
      replyTo: row.reply_to || undefined,
      taskId: row.task_id || undefined,
      runId: row.run_id || undefined,
      priority: row.priority,
      read: Boolean(row.read),
      createdAt: row.created_at,
    };
  }
}
