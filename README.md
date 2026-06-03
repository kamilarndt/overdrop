# OverDrop — Universal Agent Communication Protocol

> SQLite WAL + Filesystem. Lżejsze niż Google A2A, bardziej strukturalne niż DropSite.

**OverDrop** to uniwersalny system komunikacji między-agentowej — jeden protokół, który działa z **Hermes, Pi, Claude Code, OpenCode i innymi**.

## Architektura

```
┌──────────────────────────────────────────┐
│  L3: Observability                       │
│  Web Dashboard (NDJSON/WS) + TUI (tmux)  │
├──────────────────────────────────────────┤
│  L2: AgentRuntime                        │
│  Hermes | Pi | Claude | OpenCode | ...   │
├──────────────────────────────────────────┤
│  L1: Hybrid Data Plane                   │
│  SQLite WAL (mail bus) + FS (folders)    │
└──────────────────────────────────────────┘
```

## Dlaczego OverDrop?

| vs. | OverDrop | 
|---|---|
| **A2A** | Bez OAuth, bez federacji, setup w sekundy |
| **MCP** | Komunikacja P2P, nie tylko tools/resources |
| **DropSite** | Typowany protokół + gwarancja dostarczenia (SQLite) |
| **RabbitMQ** | Zero zależności, jeden plik SQLite |

## Szybki start

```bash
git clone https://github.com/ArndtOs/overdrop
cd overdrop/python
pip install -e .
```

```python
from overdrop import MailBus, FsProtocol, AgentRuntime

# 1. Mail bus (SQLite)
bus = MailBus("workspace/mail.db")
bus.send("dispatch", sender="hermes", recipient="pi", payload={"task": "..."})

# 2. Filesystem protocol
fs = FsProtocol("workspace/")
task_id = fs.submit("Build auth API", from_agent="hermes", assign="builder")

# 3. Agent picks up task
claimed = fs.claim("builder", task_id)  # atomic os.rename
fs.complete(claimed, result={"files": ["api.py"]})
```

## Struktura projektu

```
overdrop/
├── docs/          # Dokumentacja i research
├── spec/          # Specyfikacja protokołu i schema SQL
├── python/        # Python core (Hermes natywny)
├── ts/            # TypeScript adapter (Pi)
├── src/           # Rust/CLI (przyszłość)
└── tests/         # Testy integracyjne
```

## Stan

🚧 W budowie — faza 1: Python core (SQLite Mail Bus + FS Protocol)

