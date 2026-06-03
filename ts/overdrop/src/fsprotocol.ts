/**
 * OverDrop — Filesystem Protocol (TypeScript)
 *
 * Zero-dependency task state management via folders + JSON files.
 * Uses atomic rename() for concurrency safety.
 *
 * Mirrors python/overdrop/fsprotocol.py
 */

import * as fs from "node:fs";
import * as path from "node:path";
import { randomUUID } from "node:crypto";
import { Task, TaskStatus } from "./types.js";

const TASK_FOLDERS = ["inbox", "active", "done", "failed", "blocked", "feedback"] as const;
type TaskFolder = (typeof TASK_FOLDERS)[number];

function now(): string {
  return new Date().toISOString();
}

interface TaskData {
  id: string;
  title: string;
  status: string;
  fromAgent: string;
  assignee?: string;
  context: Record<string, unknown>;
  result: Record<string, unknown>;
  priority: number;
  maxRetries: number;
  retryCount: number;
  parentTask?: string;
  groupId?: string;
  worktree?: string;
  version: number;
  createdAt: string;
}

function toTask(d: TaskData): Task {
  return {
    id: d.id,
    title: d.title,
    status: d.status as TaskStatus,
    fromAgent: d.fromAgent,
    assignee: d.assignee,
    context: d.context,
    result: d.result,
    priority: d.priority,
    maxRetries: d.maxRetries,
    retryCount: d.retryCount,
    parentTask: d.parentTask,
    groupId: d.groupId,
    worktree: d.worktree,
    version: d.version,
    createdAt: d.createdAt,
  };
}

export class FsProtocol {
  readonly root: string;

  constructor(workspaceDir: string) {
    this.root = path.resolve(workspaceDir);
    this.#ensureDirs();
  }

  #ensureDirs(): void {
    for (const folder of TASK_FOLDERS) {
      fs.mkdirSync(path.join(this.root, folder), { recursive: true });
    }
  }

  #path(folder: TaskFolder, taskId: string): string {
    return path.join(this.root, folder, `${taskId}.json`);
  }

  #writeTask(folder: TaskFolder, task: Task): void {
    const p = this.#path(folder, task.id);
    const data: TaskData = {
      id: task.id,
      title: task.title,
      status: task.status,
      fromAgent: task.fromAgent,
      assignee: task.assignee,
      context: task.context,
      result: task.result,
      priority: task.priority,
      maxRetries: task.maxRetries,
      retryCount: task.retryCount,
      parentTask: task.parentTask,
      groupId: task.groupId,
      worktree: task.worktree,
      version: task.version,
      createdAt: task.createdAt,
    };
    // Atomic write: .tmp → replace
    const tmp = p + ".tmp";
    fs.writeFileSync(tmp, JSON.stringify(data, null, 2), "utf-8");
    fs.renameSync(tmp, p);
  }

  #readTask(p: string): Task | null {
    try {
      const data: TaskData = JSON.parse(fs.readFileSync(p, "utf-8"));
      return toTask(data);
    } catch {
      return null;
    }
  }

  #moveTask(src: TaskFolder, dst: TaskFolder, taskId: string): Task | null {
    const srcPath = this.#path(src, taskId);
    const dstPath = this.#path(dst, taskId);
    try {
      fs.renameSync(srcPath, dstPath);
      return this.#readTask(dstPath);
    } catch {
      return null;
    }
  }

  // ---- WRITE OPERATIONS ----

  submit(
    title: string,
    fromAgent: string,
    assign?: string,
    context: Record<string, unknown> = {},
    priority = 5,
    maxRetries = 3,
  ): string {
    const taskId = randomUUID();
    const task: Task = {
      id: taskId,
      title,
      status: TaskStatus.INBOX,
      fromAgent,
      assignee: assign,
      context,
      result: {},
      priority,
      maxRetries,
      retryCount: 0,
      version: 1,
      createdAt: now(),
    };
    this.#writeTask("inbox", task);
    return taskId;
  }

  claim(agent: string, taskId: string): Task | null {
    const task = this.#moveTask("inbox", "active", taskId);
    if (task) {
      task.assignee = agent;
      task.status = TaskStatus.CLAIMED;
      this.#writeTask("active", task);
    }
    return task;
  }

  complete(taskId: string, result: Record<string, unknown> = {}): void {
    const task = this.#moveTask("active", "done", taskId);
    if (task) {
      task.status = TaskStatus.DONE;
      task.result = result;
      this.#writeTask("done", task);
    }
  }

  fail(taskId: string, error?: string): void {
    const task = this.#readTask(this.#path("active", taskId));
    if (!task) return;

    task.retryCount++;
    task.result = { ...task.result, error };

    if (task.retryCount < task.maxRetries) {
      this.#moveTask("active", "inbox", taskId);
      task.status = TaskStatus.INBOX;
      this.#writeTask("inbox", task);
    } else {
      this.#moveTask("active", "failed", taskId);
      task.status = TaskStatus.FAILED;
      this.#writeTask("failed", task);
    }
  }

  block(taskId: string, reason?: string): void {
    const task = this.#moveTask("active", "blocked", taskId);
    if (task) {
      task.status = TaskStatus.BLOCKED;
      task.result = { ...task.result, blockedReason: reason };
      this.#writeTask("blocked", task);
    }
  }

  unblock(taskId: string): void {
    this.#moveTask("blocked", "inbox", taskId);
    const task = this.#readTask(this.#path("inbox", taskId));
    if (task) {
      task.status = TaskStatus.INBOX;
      this.#writeTask("inbox", task);
    }
  }

  // ---- READ OPERATIONS ----

  listTasks(folder: TaskFolder, limit = 100): Task[] {
    const pattern = path.join(this.root, folder, "*.json");
    // Simple glob
    let files: string[] = [];
    try {
      files = fs.readdirSync(path.join(this.root, folder))
        .filter((f) => f.endsWith(".json"))
        .map((f) => path.join(this.root, folder, f))
        .sort((a, b) => fs.statSync(b).mtimeMs - fs.statSync(a).mtimeMs)
        .slice(0, limit);
    } catch {
      return [];
    }
    return files
      .map((f) => this.#readTask(f))
      .filter((t): t is Task => t !== null);
  }

  getTask(taskId: string): Task | null {
    for (const folder of TASK_FOLDERS) {
      const task = this.#readTask(this.#path(folder, taskId));
      if (task) return task;
    }
    return null;
  }

  reapStale(timeoutS = 300): string[] {
    const reaped: string[] = [];
    const now = Date.now();
    const activeDir = path.join(this.root, "active");
    try {
      for (const file of fs.readdirSync(activeDir)) {
        if (!file.endsWith(".json")) continue;
        const filePath = path.join(activeDir, file);
        const mtime = fs.statSync(filePath).mtimeMs;
        if (now - mtime > timeoutS * 1000) {
          const taskId = path.basename(file, ".json");
          const dst = path.join(this.root, "inbox", file);
          try {
            fs.renameSync(filePath, dst);
            reaped.push(taskId);
          } catch {
            // ignore
          }
        }
      }
    } catch {
      // ignore
    }
    return reaped;
  }
}
