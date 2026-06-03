#!/usr/bin/env python3
"""
OverDrop CLI — command-line interface for the agent communication protocol.

Usage:
    overdrop init <workspace>           Initialize workspace
    overdrop submit <title> <from>      Submit a new task
    overdrop claim <agent> <task-id>    Claim a task
    overdrop done <task-id>             Complete a task
    overdrop fail <task-id>             Fail a task
    overdrop list [folder]              List tasks in a folder
    overdrop status <task-id>           Show task details
    overdrop reap                       Reap stale tasks
    overdrop archive                    Archive old messages
    overdrop mail <agent>               Show unread messages for agent
"""

import sys
import os
import json
import argparse

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from overdrop import FsProtocol, MailBus
from overdrop.types import TaskStatus


def cmd_init(args):
    """Initialize an OverDrop workspace."""
    path = args.workspace
    fs = FsProtocol(path)
    bus = MailBus(os.path.join(path, "overdrop.db"))
    bus.connect()
    bus.close()
    
    print(f"✅ OverDrop workspace initialized at: {os.path.abspath(path)}")
    print(f"   inbox/     — new tasks")
    print(f"   active/    — claimed tasks")
    print(f"   done/      — completed tasks")
    print(f"   failed/    — failed tasks")
    print(f"   blocked/   — blocked tasks")
    print(f"   feedback/  — tasks needing decision")
    print(f"   overdrop.db — SQLite Mail Bus")


def cmd_submit(args):
    """Submit a new task."""
    fs = FsProtocol(args.workspace)
    context = {}
    if args.context:
        context = json.loads(args.context)
    
    task_id = fs.submit(
        title=args.title,
        from_agent=args.from_agent,
        assign=args.assign,
        context=context,
        priority=args.priority,
    )
    print(f"✅ Task submitted: {task_id}")
    print(f"   Title: {args.title}")
    print(f"   From: {args.from_agent} → {args.assign or 'any'}")


def cmd_claim(args):
    """Claim a task."""
    fs = FsProtocol(args.workspace)
    task = fs.claim(args.agent, args.task_id)
    if task:
        print(f"✅ Task claimed: {task.id}")
        print(f"   Title: {task.title}")
        print(f"   By: {args.agent}")
    else:
        print(f"❌ Task not available (already claimed or not found)")
        sys.exit(1)


def cmd_done(args):
    """Complete a task."""
    fs = FsProtocol(args.workspace)
    result = {}
    if args.result:
        result = json.loads(args.result)
    fs.complete(args.task_id, result=result)
    print(f"✅ Task completed: {args.task_id}")


def cmd_fail(args):
    """Fail a task."""
    fs = FsProtocol(args.workspace)
    fs.fail(args.task_id, error=args.error)
    print(f"❌ Task failed: {args.task_id}")


def cmd_list(args):
    """List tasks."""
    fs = FsProtocol(args.workspace)
    folder = args.folder or "inbox"
    tasks = fs.list_tasks(folder, limit=args.limit)
    
    if not tasks:
        print(f"📭 No tasks in {folder}/")
        return
    
    print(f"📋 Tasks in {folder}/ ({len(tasks)}):")
    print("-" * 80)
    for t in tasks:
        status_icon = {
            "inbox": "📥", "claimed": "📌", "active": "⚡",
            "done": "✅", "failed": "❌", "blocked": "🔒",
            "needs_decision": "❓",
        }.get(t.status.value, "📄")
        print(f"  {status_icon} [{t.status.value:20s}] {t.id[:8]}... {t.title[:50]}")


def cmd_status(args):
    """Show task status."""
    fs = FsProtocol(args.workspace)
    task = fs.get_task(args.task_id)
    if not task:
        print(f"❌ Task not found: {args.task_id}")
        sys.exit(1)
    
    print(f"📄 Task: {task.id}")
    print(f"   Title:      {task.title}")
    print(f"   Status:     {task.status.value}")
    print(f"   From:       {task.from_agent}")
    print(f"   Assignee:   {task.assignee or 'unassigned'}")
    print(f"   Priority:   {task.priority}")
    print(f"   Retries:    {task.retry_count}/{task.max_retries}")
    print(f"   Context:    {json.dumps(task.context, indent=2)}")
    print(f"   Result:     {json.dumps(task.result, indent=2)}")


def cmd_reap(args):
    """Reap stale tasks."""
    fs = FsProtocol(args.workspace)
    reaped = fs.reap_stale(timeout_s=args.timeout)
    if reaped:
        print(f"✅ Reaped {len(reaped)} stale tasks back to inbox")
        for tid in reaped:
            print(f"   • {tid}")
    else:
        print(f"📭 No stale tasks found (timeout: {args.timeout}s)")


def cmd_archive(args):
    """Archive old messages."""
    bus = MailBus(os.path.join(args.workspace, "overdrop.db"))
    bus.connect()
    bus.archive_old(days=args.days)
    bus.close()
    print(f"✅ Messages older than {args.days} days archived")


def cmd_mail(args):
    """Show unread messages."""
    bus = MailBus(os.path.join(args.workspace, "overdrop.db"))
    bus.connect()
    msgs = bus.poll(args.agent, unread_only=True)
    bus.close()
    
    if not msgs:
        print(f"📭 No unread messages for '{args.agent}'")
        return
    
    print(f"📨 Unread messages for '{args.agent}' ({len(msgs)}):")
    print("-" * 60)
    for m in msgs:
        print(f"  [{m.type.value:12s}] from {m.sender}: "
              f"{json.dumps(m.payload)[:60]}")


def main():
    parser = argparse.ArgumentParser(description="OverDrop CLI")
    parser.add_argument("--workspace", "-w", default="./workspace",
                        help="OverDrop workspace path (default: ./workspace)")
    
    sub = parser.add_subparsers(dest="command")
    
    # init
    p_init = sub.add_parser("init", help="Initialize workspace")
    p_init.add_argument("workspace", nargs="?", default="./workspace")
    
    # submit
    p_submit = sub.add_parser("submit", help="Submit a new task")
    p_submit.add_argument("title")
    p_submit.add_argument("from_agent")
    p_submit.add_argument("--assign", "-a", help="Assign to agent")
    p_submit.add_argument("--context", "-c", help="JSON context")
    p_submit.add_argument("--priority", "-p", type=int, default=5)
    
    # claim
    p_claim = sub.add_parser("claim", help="Claim a task")
    p_claim.add_argument("agent")
    p_claim.add_argument("task_id")
    
    # done
    p_done = sub.add_parser("done", help="Complete a task")
    p_done.add_argument("task_id")
    p_done.add_argument("--result", "-r", help="JSON result")
    
    # fail
    p_fail = sub.add_parser("fail", help="Fail a task")
    p_fail.add_argument("task_id")
    p_fail.add_argument("--error", "-e", help="Error message")
    
    # list
    p_list = sub.add_parser("list", help="List tasks")
    p_list.add_argument("folder", nargs="?", default="inbox",
                        choices=["inbox", "active", "done", "failed", "blocked", "feedback"])
    p_list.add_argument("--limit", "-l", type=int, default=50)
    
    # status
    p_status = sub.add_parser("status", help="Show task details")
    p_status.add_argument("task_id")
    
    # reap
    p_reap = sub.add_parser("reap", help="Reap stale tasks")
    p_reap.add_argument("--timeout", "-t", type=int, default=300)
    
    # archive
    p_archive = sub.add_parser("archive", help="Archive old messages")
    p_archive.add_argument("--days", "-d", type=int, default=7)
    
    # mail
    p_mail = sub.add_parser("mail", help="Show agent mail")
    p_mail.add_argument("agent")
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return
    
    cmds = {
        "init": cmd_init,
        "submit": cmd_submit,
        "claim": cmd_claim,
        "done": cmd_done,
        "fail": cmd_fail,
        "list": cmd_list,
        "status": cmd_status,
        "reap": cmd_reap,
        "archive": cmd_archive,
        "mail": cmd_mail,
    }
    
    cmds[args.command](args)


if __name__ == "__main__":
    main()
