/**
 * OverDrop — TypeScript type definitions
 * Mirror of python/overdrop/types.ts — must stay in sync.
 */

/** Typed protocol message types (z Overstory 8-type system). */
export enum MessageType {
  DISPATCH = "dispatch",
  ASK = "ask",
  REPLY = "reply",
  ESCALATE = "escalate",
  WORKER_DONE = "worker_done",
  MERGE_READY = "merge_ready",
  BROADCAST = "broadcast",
  PROGRESS = "progress_update",
}

/** Full task lifecycle state machine. */
export enum TaskStatus {
  PENDING_DEP = "pending_dependencies",
  INBOX = "inbox",
  CLAIMED = "claimed",
  ACTIVE = "active",
  BLOCKED = "blocked",
  NEEDS_DECISION = "needs_decision",
  MERGE_READY = "merge_ready",
  NEEDS_REVIEW = "needs_review",
  DONE = "done",
  FAILED = "failed",
  NEEDS_ATTENTION = "needs_attention",
}

/** Agent lifecycle status (z pi-intercom model). */
export enum AgentStatus {
  IDLE = "idle",
  THINKING = "thinking",
  TOOL = "tool",
  OFFLINE = "offline",
}

/** A single Message on the Mail Bus. */
export interface Message {
  id: string;
  type: MessageType;
  sender: string;
  recipient: string;
  payload: Record<string, unknown>;
  replyTo?: string;
  taskId?: string;
  runId?: string;
  priority: number;
  read: boolean;
  createdAt: string;
}

/** A unit of work managed by OverDrop. */
export interface Task {
  id: string;
  title: string;
  status: TaskStatus;
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

/** AgentRuntime — pluggable adapter interface for any agent type. */
export interface AgentRuntime {
  spawn(task: Task): Promise<string>;
  deployConfig(worktreePath: string, role: string): Promise<void>;
  enforceGuards(role: string): Promise<void>;
  parseTranscript(stream: AsyncIterable<string>): AsyncIterable<ParsedEvent>;
  interrupt(handle: string): Promise<void>;
}

/** Single parsed event from agent output. */
export interface ParsedEvent {
  type: "thinking" | "tool_call" | "tool_result" | "text" | "error";
  content: unknown;
  timestamp: string;
}

/** DAG dependency edge between tasks. */
export interface DagEdge {
  fromTask: string;
  toTask: string;
  depType: "needs" | "after";
}
