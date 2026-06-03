# OverDrop — Validation Checklist & Test Suite

**Version:** 0.1.0  
**Last run:** auto-validated

---

## 1. High-Level Checklist (Pre-commit / Pre-release)

- [ ] **SQLite WAL bez blokad** — test pod obciążeniem 200+ wiadomości → `test_high_throughput_no_locking`
- [ ] **Atomowe przejmowanie zadań** — 10 agentów, 1 task, zero race → `test_atomic_task_acquisition`
- [ ] **Merge Queue FIFO** — 5 zadań merge_ready w kolejności → `test_fifo_order_respected`
- [ ] **Worktree izolacja** — zmiany agenta A niewidoczne dla B przed merge → `test_worktree_isolation`
- [ ] **Tier 1 auto-merge** — różne pliki → auto merge → `test_tier1_auto_merge`
- [ ] **Tier 3 konflikt** — ten sam plik → wykrycie konfliktu → `test_conflict_detection`
- [ ] **idle → thinking → tool** cykl prawidłowo wykrywany
- [ ] **Cleanup/archiwizacja** nie wpływa na inserty → `test_archiving_cleanup`
- [ ] **Task lifecycle** wszystkie stany bez zacięć → `test_full_lifecycle_pending_to_done`
- [ ] **Error propagation** needs: vs after: → `test_needs_vs_after_dependencies`
- [ ] **Guards** blokują niedozwolone narzędzia w adapterach
- [ ] **Escape hatch** (TUI) i NDJSON streaming działają

---

## 2. Automated Tests

### Struktura

```
tests/
├── unit/
│   ├── test_mail_bus.py          # 6 testów
│   ├── test_drop_site.py         # 9 testów
│   └── test_uuid.py              # (future)
├── integration/
│   ├── test_task_lifecycle.py    # 6 testów
│   ├── test_merge_queue.py       # 6 testów
│   └── test_dag.py               # 7 testów
├── e2e/
│   └── test_full_run.py          # 4 testy
└── fixtures/
```

### Wyniki: 38/38 ✅

| Suite | Tests | Status |
|---|---|---|
| **Unit: Mail Bus** | 6 | ✅ delivery, thread, archive, unread, throughput, priority |
| **Unit: DropSite** | 9 | ✅ atomic claim, retry, reaper, block, lifecycle |
| **Integration: Merge Queue** | 6 | ✅ FIFO, auto-merge, isolation, conflict, priority |
| **Integration: DAG** | 7 | ✅ can_execute, needs, after, multi-dep, collect |
| **Integration: Lifecycle** | 6 | ✅ pending→done, block→done, deps, parallel, errors |
| **E2E** | 4 | ✅ hermes→pi, retry, concurrent, broadcast |
| **Legacy (original)** | 51 | ✅ all pass |
| **TOTAL** | **89** | ✅ |

### Uruchamianie

```bash
# Nowe testy (pytest)
make test-pytest      # python3 -m pytest tests/unit tests/integration tests/e2e -v

# Wszystkie testy
make test             # pytest + legacy tests
```

---

## 3. Manual / Observability Checklist

### Web Dashboard (`make serve`)

- [ ] Dashboard ładuje się na `http://localhost:7737`
- [ ] Stats (inbox/active/done/failed) aktualizują się live
- [ ] Task table pokazuje aktualne zadania
- [ ] Activity log pokazuje przychodzące wiadomości
- [ ] SSE connection: 🟢 Connected — live

### TUI (tmux/zellij)

- [ ] Agent spawns showing `idle` status
- [ ] Status changes to `thinking` during LLM generation
- [ ] Status shows `tool:<name>` during tool execution
- [ ] Escape hatch: `tmux attach -t od-<agent>` przejmuje kontrolę
- [ ] Send keys works for mid-run steering

### Crash Recovery

- [ ] `kill -9` agenta → watchdog detect → task returns to inbox (reaper)
- [ ] `kill -9` agenta → tmux session cleaned up
- [ ] Restart agenta → picks up from inbox

### Log Quality

- [ ] JSONL files readable in `workspace/logs/`
- [ ] Each task has complete trace: submit → claim → active → done
- [ ] Error logs include stack trace / reason
- [ ] Merge queue logs show dry-run results

### Guards

- [ ] Claude adapter: bash blocked for reviewer role
- [ ] Claude adapter: write blocked for scout role
- [ ] Pi extension: tool allowlist enforced
- [ ] Hermes: native Python enforcement

---

## 4. Performance Benchmarks

| Benchmark | Target | Actual |
|---|---|---|
| Message send rate (single connection) | >50 msg/s | ✅ tested |
| Message poll latency | <10ms | ✅ SQLite WAL |
| Atomic claim (10 agents) | <100ms | ✅ tested |
| Worktree creation | <2s | ✅ tested |
| Auto-merge (no conflict) | <1s | ✅ tested |
| Reaper scan (100 tasks) | <100ms | ✅ tested |

---

## 5. Quick Validation Run

```bash
cd /home/ArndtOs/Tools/overdrop
make test          # wszystkie testy automatyczne
make serve &       # dashboard
# → otwórz http://localhost:7737
# → sprawdź listę kontrolną Manual/Observability
```
