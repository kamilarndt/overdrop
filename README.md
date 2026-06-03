# OverDrop — Universal Agent Communication Protocol

> SQLite WAL + Filesystem. Lżejsze niż Google A2A, bardziej strukturalne niż DropSite.

**OverDrop** to uniwersalny system komunikacji między-agentowej — jeden protokół, który działa z **Hermes, Pi, Claude Code, OpenCode i innymi**.

## Architektura

```
┌──────────────────────────────────────────┐
│  L3: Observability                       │
│  Web Dashboard (SSE) + TUI (tmux)        │
├──────────────────────────────────────────┤
│  L2: Orchestration                       │
│  MergeQueue + WorktreeManager + DAG      │
├──────────────────────────────────────────┤
│  L1: AgentRuntime                        │
│  Hermes | Pi | Claude | OpenCode | ...   │
├──────────────────────────────────────────┤
│  L0: Hybrid Data Plane                   │
│  SQLite WAL (mail bus) + FS (folders)    │
└──────────────────────────────────────────┘
```

## Dlaczego OverDrop?

| vs. | OverDrop |
|-----|----------|
| **A2A** | Bez OAuth, bez federacji, setup w sekundy |
| **MCP** | Komunikacja P2P, nie tylko tools/resources |
| **DropSite** | Typowany protokół + gwarancja dostarczenia (SQLite) |
| **RabbitMQ** | Zero zależności, jeden plik SQLite |

## Szybki start

```bash
git clone https://github.com/kamilarndt/overdrop
cd overdrop
python -m venv .venv
.venv/bin/pip install -e python/.
```

### Podstawowe użycie

```python
from overdrop import MailBus, FsProtocol, MessageType

# 1. Mail bus (SQLite)
bus = MailBus("workspace/overdrop.db")
bus.connect()
bus.send(MessageType.DISPATCH, sender="hermes", recipient="pi", 
         payload={"task": "Build auth API"})

# 2. Filesystem protocol
fs = FsProtocol("workspace/")
task_id = fs.submit("Build auth API", from_agent="hermes", assign="builder")

# 3. Agent picks up task
claimed = fs.claim("builder", task_id)  # atomic os.rename
fs.complete(task_id, result={"files": ["api.py"]})
```

### MergeQueue — Zarządzanie merge'ami

```python
from overdrop import WorktreeManager, MergeQueue

# Setup
wt = WorktreeManager("/path/to/repo")
mq = MergeQueue("/path/to/repo")

# Utwórz worktree dla zadania
worktree_path = wt.create("task-123", "agent-a")

# Pracuj w izolowanym worktree
# ... (agent implementuje feature) ...

# Commit changes
wt.commit_changes("task-123", "Add auth feature", "agent-a")

# Enqueue do merge queue
mq.enqueue("task-123", "od/agent-a/task-123", worktree_path, "agent-a")

# Przetwórz merge (FIFO z priorytetami)
result = mq.process_next()
print(f"Status: {result.status}")  # merged | conflict | failed

# Dodatkowe akcje
mq.cancel("task-123")      # Anuluj pending
mq.retry("task-123")       # Retry po conflict/failed
all_entries = mq.list_all() # Wszystkie wpisy
```

### Conflict Resolution Pipeline

```
Pending → Dry-run → Auto-merge (success) → Merged
              ↓
         Conflict detected
              ↓
         Tier 1 (≤2 files) → Rebase/Cherry-pick
              ↓ (failed)
         Tier 2 (≤5 files) → AI Resolution
              ↓ (failed)
         Tier 3 (>5 files) → Human Escalation
```

## Struktura projektu

```
overdrop/
├── dashboard/         # Modularny frontend (HTML/CSS/JS)
│   ├── index.html     # Szkielet HTML
│   ├── css/style.css  # Style
│   └── js/            # Moduły JavaScript
├── docs/              # Dokumentacja i research
├── python/            # Python core
│   └── overdrop/      # Core modules
│       ├── mailbus.py    # SQLite WAL mail bus
│       ├── fsprotocol.py # Filesystem protocol
│       ├── worktree.py   # WorktreeManager + MergeQueue
│       └── types.py      # Typy i interfejsy
├── tests/             # Testy
│   ├── unit/          # Testy jednostkowe
│   └── integration/   # Testy integracyjne
└── spec/              # Specyfikacja protokołu
```

## API Endpoints

| Method | Endpoint | Opis |
|--------|----------|------|
| GET | `/api/tasks` | Lista zadań (filtry: status, agent, q) |
| POST | `/api/tasks` | Utwórz zadanie |
| GET | `/api/tasks/:id` | Szczegóły zadania |
| POST | `/api/tasks/:id/claim` | Claim zadania |
| POST | `/api/tasks/:id/done` | Oznacz jako done |
| POST | `/api/tasks/:id/block` | Zablokuj |
| POST | `/api/tasks/:id/unblock` | Odblokuj |
| POST | `/api/tasks/:id/fail` | Oznacz jako failed |
| DELETE | `/api/tasks/:id` | Usuń zadanie |
| GET | `/api/agents` | Lista agentów z manifestu |
| POST | `/api/agents/:name/model` | Zmień model agenta |
| GET | `/api/models` | Lista dostępnych modeli |
| GET | `/api/merge-queue` | Lista merge queue |
| POST | `/api/merge-queue/:id/trigger` | Dodaj do merge queue |
| POST | `/api/merge-queue/:id/process` | Przetwórz merge |
| POST | `/api/merge-queue/:id/cancel` | Anuluj merge |
| POST | `/api/merge-queue/:id/retry` | Retry po conflict |

## Dashboard

```bash
# Uruchom dashboard
cd overdrop
PYTHONPATH=python python3 python/dashboard.py workspace --port 7737

# Otwórz w przeglądarce
open http://localhost:7737
```

### Features
- ✅ Live updates przez SSE
- ✅ Task CRUD (create, claim, done, block, fail, delete)
- ✅ Filtrowanie i wyszukiwanie
- ✅ Lista agentów z model selectorem
- ✅ Merge Queue z akcjami (process, cancel, retry)
- ✅ Szczegóły zadania z Worktree & Merge info

## Testy

```bash
# Wszystkie testy
PYTHONPATH=python .venv/bin/pytest tests/ -v

# Tylko testy integracyjne
PYTHONPATH=python .venv/bin/pytest tests/integration/ -v

# Tylko testy MergeQueue
PYTHONPATH=python .venv/bin/pytest tests/integration/test_merge_queue.py -v
```

### Pokrycie testowe
- ✅ MailBus (send, receive, broadcast, archive)
- ✅ FsProtocol (submit, claim, complete, fail, block, retry)
- ✅ MergeQueue (enqueue, process, cancel, retry, priority)
- ✅ WorktreeManager (create, remove, cleanup)
- ✅ Conflict resolution (Tier 1-3)

## Agent Adapters

| Adapter | Plik | Opis |
|---------|------|------|
| Hermes | `python/adapter_hermes.py` | Natywny Python (async) |
| Claude | `python/adapter_claude.py` | Claude Code CLI |
| OpenCode | `python/adapter_opencode.py` | OpenCode CLI |

## Status

✅ **Faza 1 — Foundation**: MailBus, FsProtocol, AgentRuntime  
✅ **Faza 2 — Orchestration**: MergeQueue, WorktreeManager, DAG  
✅ **Faza 3 — Observability**: Dashboard v3 (SSE, API, MergeQueue UI)

## Licencja

MIT
