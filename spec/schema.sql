-- OverDrop SQLite Schema v1
-- Tryb WAL obowiązkowy dla współbieżności
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ============================================================
-- TABELA 1: messages — główna szyna wiadomości (Mail Bus)
-- ============================================================
CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,                    -- UUID v7
    type        TEXT NOT NULL,                       -- dispatch | ask | reply | escalate | worker_done | merge_ready | broadcast
    sender      TEXT NOT NULL,                       -- agent name / session ID
    recipient   TEXT NOT NULL,                       -- agent name / @all / room:<run_id>
    payload     TEXT DEFAULT '{}',                   -- JSON payload
    reply_to    TEXT,                                -- message ID this replies to (threading)
    task_id     TEXT,                                -- associated task
    run_id      TEXT,                                -- associated run
    priority    INTEGER DEFAULT 5,                   -- 1 (critical)..10 (low)
    read        INTEGER DEFAULT 0,                   -- 0=unread, 1=read
    created_at  TEXT DEFAULT (datetime('now')),      -- ISO 8601
    
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_recipient_unread
    ON messages(recipient, read);
CREATE INDEX IF NOT EXISTS idx_messages_task_id
    ON messages(task_id);
CREATE INDEX IF NOT EXISTS idx_messages_created_at
    ON messages(created_at);

-- ============================================================
-- TABELA 2: tasks — rejestr zadań
-- ============================================================
CREATE TABLE IF NOT EXISTS tasks (
    id           TEXT PRIMARY KEY,                   -- UUID v7
    title        TEXT NOT NULL,
    description  TEXT,
    status       TEXT NOT NULL DEFAULT 'inbox',      -- inbox | claimed | active | blocked | merge_ready | review | done | failed | needs_attention
    from_agent   TEXT NOT NULL,                      -- delegator
    assignee     TEXT,                               -- assigned agent
    run_id       TEXT,                               -- run this task belongs to
    parent_task  TEXT,                               -- parent task (for subtasks)
    group_id     TEXT,                               -- task group (batch)
    context      TEXT DEFAULT '{}',                  -- JSON: files, constraints, etc.
    result       TEXT DEFAULT '{}',                  -- JSON: output, artifacts
    priority     INTEGER DEFAULT 5,
    max_retries  INTEGER DEFAULT 3,
    retry_count  INTEGER DEFAULT 0,
    timeout_s    INTEGER DEFAULT 7200,               -- 2 hours
    worktree     TEXT,                               -- git worktree path (if active)
    version      INTEGER DEFAULT 1,                  -- optimistic locking
    created_at   TEXT DEFAULT (datetime('now')),
    updated_at   TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE SET NULL,
    FOREIGN KEY (parent_task) REFERENCES tasks(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee);
CREATE INDEX IF NOT EXISTS idx_tasks_group ON tasks(group_id);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at);

-- ============================================================
-- TABELA 3: runs — pojedyncze uruchomienie / pipeline
-- ============================================================
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,                    -- UUID v7
    goal        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',      -- active | paused | done | failed
    context     TEXT DEFAULT '{}',                   -- JSON
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now')),
    completed_at TEXT
);

-- ============================================================
-- TABELA 4: agents — rejestr agentów
-- ============================================================
CREATE TABLE IF NOT EXISTS agents (
    id          TEXT PRIMARY KEY,                    -- agent name
    type        TEXT NOT NULL,                       -- hermes | pi | claude | opencode | gemini | bash
    status      TEXT NOT NULL DEFAULT 'offline',     -- idle | thinking | tool:<name> | offline
    last_activity TEXT,
    config      TEXT DEFAULT '{}',                   -- JSON
    registered_at TEXT DEFAULT (datetime('now'))
);

-- ============================================================
-- TABELA 5: merge_queue — kolejka scalania git worktree
-- ============================================================
CREATE TABLE IF NOT EXISTS merge_queue (
    task_id     TEXT PRIMARY KEY,
    branch      TEXT NOT NULL,
    agent_id    TEXT,
    worktree    TEXT,
    priority    INTEGER DEFAULT 5,
    status      TEXT DEFAULT 'pending',              -- pending | dry_run | resolving (AI) | conflict | merged | failed
    error_log   TEXT,                                -- merge error output
    created_at  TEXT DEFAULT (datetime('now')),
    merged_at   TEXT,
    
    FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

-- ============================================================
-- TABELA 6: dag_edges — graf zależności między zadaniami
-- ============================================================
CREATE TABLE IF NOT EXISTS dag_edges (
    from_task   TEXT NOT NULL,
    to_task     TEXT NOT NULL,
    dep_type    TEXT NOT NULL DEFAULT 'needs',       -- needs (success) | after (sequential)
    
    PRIMARY KEY (from_task, to_task),
    FOREIGN KEY (from_task) REFERENCES tasks(id) ON DELETE CASCADE,
    FOREIGN KEY (to_task) REFERENCES tasks(id) ON DELETE CASCADE
);

-- ============================================================
-- TRIGGER: auto-set updated_at na tasks
-- ============================================================
CREATE TRIGGER IF NOT EXISTS trg_tasks_updated_at
    AFTER UPDATE ON tasks
    FOR EACH ROW
BEGIN
    UPDATE tasks SET updated_at = datetime('now') WHERE id = OLD.id;
END;

-- ============================================================
-- TRIGGER: auto-set updated_at na runs
-- ============================================================
CREATE TRIGGER IF NOT EXISTS trg_runs_updated_at
    AFTER UPDATE ON runs
    FOR EACH ROW
BEGIN
    UPDATE runs SET updated_at = datetime('now') WHERE id = OLD.id;
END;
