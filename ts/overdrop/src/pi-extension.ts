/**
 * OverDrop Pi Extension — Universal Agent Communication for Pi
 *
 * Installs tools that let Pi agents participate in the OverDrop ecosystem:
 * - overdrop_submit  — create a task
 * - overdrop_claim   — pick up a task
 * - overdrop_complete — finish a task
 * - overdrop_send    — send a message via Mail Bus
 * - overdrop_poll    — check for messages/tasks
 * - overdrop_status  — check task status
 * - overdrop_init    — init workspace
 *
 * Install:  pi install /path/to/overdrop-ts
 * Register: Install as npm package in .pi/agent/extensions/
 */

import { FsProtocol } from "./fsprotocol.js";
import { MailBus } from "./mailbus.js";
import { MessageType, TaskStatus } from "./types.js";
import path from "node:path";
import os from "node:os";

const OVERDROP_DIR = process.env.OVERDROP_DIR || path.join(os.homedir(), ".overdrop");

let fsProto: FsProtocol;
let mailBus: MailBus;

function getFs(): FsProtocol {
  if (!fsProto) fsProto = new FsProtocol(OVERDROP_DIR);
  return fsProto;
}

function getBus(): MailBus {
  if (!mailBus) {
    mailBus = new MailBus(path.join(OVERDROP_DIR, "overdrop.db"));
    mailBus.connect();
  }
  return mailBus;
}

/**
 * Pi extension registration.
 * 
 * Pi calls this function when loading the extension.
 * See: https://pi.dev/packages
 */
export default function overdropExtension(pi: any) {
  const od = {
    name: "overdrop",
    description: "Universal Agent Communication Protocol — send tasks, messages, coordinate agents",
    tools: [
      {
        name: "overdrop_submit",
        description: "Submit a new task to the OverDrop ecosystem",
        parameters: {
          type: "object",
          properties: {
            title: { type: "string", description: "Task title" },
            assign: { type: "string", description: "Target agent" },
            context: { type: "string", description: "JSON context string" },
            priority: { type: "number", default: 5, description: "1-10 priority" },
          },
          required: ["title"],
        },
        handler: async (args: any, _ctx: any) => {
          const fs = getFs();
          const taskId = fs.submit(
            args.title,
            "pi",
            args.assign,
            args.context ? JSON.parse(args.context) : {},
            args.priority || 5,
          );
          return { taskId, status: "submitted", title: args.title };
        },
      },
      {
        name: "overdrop_claim",
        description: "Claim a task from the inbox for processing",
        parameters: {
          type: "object",
          properties: {
            taskId: { type: "string", description: "ID of task to claim" },
          },
          required: ["taskId"],
        },
        handler: async (args: any, ctx: any) => {
          const agentName = ctx?.agentName || "pi";
          const fs = getFs();
          const task = fs.claim(agentName, args.taskId);
          if (!task) return { error: "Task not available", taskId: args.taskId };
          return { taskId: task.id, title: task.title, status: task.status };
        },
      },
      {
        name: "overdrop_complete",
        description: "Mark a task as completed with result",
        parameters: {
          type: "object",
          properties: {
            taskId: { type: "string", description: "Task ID" },
            result: { type: "string", description: "JSON result string" },
          },
          required: ["taskId"],
        },
        handler: async (args: any) => {
          const fs = getFs();
          const result = args.result ? JSON.parse(args.result) : {};
          fs.complete(args.taskId, result);
          return { taskId: args.taskId, status: "done" };
        },
      },
      {
        name: "overdrop_send",
        description: "Send a message to another agent via OverDrop Mail Bus",
        parameters: {
          type: "object",
          properties: {
            message: { type: "string", description: "Message content" },
            recipient: { type: "string", description: "Agent name or @group or room:<id>" },
            type: {
              type: "string",
              enum: ["dispatch", "ask", "reply", "broadcast", "progress"],
              default: "dispatch",
            },
            taskId: { type: "string", description: "Associated task ID (optional)" },
          },
          required: ["message", "recipient"],
        },
        handler: async (args: any, ctx: any) => {
          const agentName = ctx?.agentName || "pi";
          const bus = getBus();
          const msgType = args.type as MessageType || MessageType.DISPATCH;
          const msgId = bus.send(
            msgType,
            agentName,
            args.recipient,
            { text: args.message },
            undefined,
            args.taskId,
          );
          return { messageId: msgId, sent: true };
        },
      },
      {
        name: "overdrop_poll",
        description: "Check for pending tasks in inbox and unread messages",
        parameters: {
          type: "object",
          properties: {
            agentName: { type: "string", description: "Agent name (default: current)" },
          },
        },
        handler: async (_args: any, ctx: any) => {
          const agentName = ctx?.agentName || "pi";
          const bus = getBus();
          const fs = getFs();

          const messages = bus.poll(agentName, true, 20);
          const tasks = fs.listTasks("inbox", 10);
          const unread = bus.countUnread(agentName);

          bus.markAllRead(agentName);

          return {
            unreadMessages: unread,
            messages: messages.map((m) => ({
              id: m.id,
              type: m.type,
              sender: m.sender,
              payload: m.payload,
              createdAt: m.createdAt,
            })),
            pendingTasks: tasks.map((t) => ({
              id: t.id,
              title: t.title,
              from: t.fromAgent,
              priority: t.priority,
            })),
          };
        },
      },
      {
        name: "overdrop_status",
        description: "Check the status of a task",
        parameters: {
          type: "object",
          properties: {
            taskId: { type: "string", description: "Task ID" },
          },
          required: ["taskId"],
        },
        handler: async (args: any) => {
          const fs = getFs();
          const task = fs.getTask(args.taskId);
          if (!task) return { error: "Task not found", taskId: args.taskId };
          return {
            taskId: task.id,
            title: task.title,
            status: task.status,
            assignee: task.assignee,
            from: task.fromAgent,
            retries: `${task.retryCount}/${task.maxRetries}`,
            result: task.result,
          };
        },
      },
    ],
  };

  // Register with Pi
  if (pi?.registerExtension) {
    pi.registerExtension(od);
  }

  return od;
}
