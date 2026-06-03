#!/usr/bin/env python3
"""OverDrop Agent Worker — polls inbox, processes tasks, sends @all broadcasts."""
import sys, time, json, random
sys.path.insert(0, '/home/ArndtOs/Tools/overdrop/python')

from overdrop import FsProtocol, MailBus, MessageType

WORKSPACE = '/home/ArndtOs/Tools/overdrop/.overdrop'
AGENT_NAME = 'auto-worker'

fs = FsProtocol(WORKSPACE)
bus = MailBus(f'{WORKSPACE}/overdrop.db')
bus.connect()

print(f"🤖 Agent '{AGENT_NAME}' started — polling every 2s")

task_count = 0

while True:
    inbox_tasks = fs.list_tasks('inbox')

    for task in inbox_tasks:
        claimed = fs.claim(AGENT_NAME, task.id)
        if not claimed:
            continue

        task_count += 1
        print(f"\n📥 CLAIMED #{task_count}: {task.title}")

        # Notify: claimed
        bus.send(MessageType.DISPATCH, sender=AGENT_NAME, recipient='hermes',
                 payload={'task_id': task.id, 'title': task.title}, task_id=task.id)
        bus.send(MessageType.BROADCAST, sender=AGENT_NAME, recipient='@all',
                 payload={'event': 'agent_claimed', 'agent': AGENT_NAME, 'task': task.title})

        # Simulate work steps
        steps = random.randint(2, 4)
        for step in range(steps):
            time.sleep(1.5)
            pct = int((step + 1) / steps * 100)
            bus.send(MessageType.PROGRESS, sender=AGENT_NAME, recipient='@all',
                     payload={'task': task.title, 'progress': f'{pct}%', 'step': f'{step+1}/{steps}'},
                     task_id=task.id)
            print(f"   ⏳ {pct}% ({step+1}/{steps})")

        # Complete
        result = {'files': [f'{task.title.lower().replace(" ", "_")}.py'], 'lines': random.randint(10, 200)}
        fs.complete(task.id, result=result)

        bus.send(MessageType.WORKER_DONE, sender=AGENT_NAME, recipient='hermes',
                 payload={'task_id': task.id, 'result': result}, task_id=task.id)
        bus.send(MessageType.BROADCAST, sender=AGENT_NAME, recipient='@all',
                 payload={'event': 'task_completed', 'task': task.title, 'result': result})

        print(f"   ✅ DONE: {task.title}")

    time.sleep(2)
