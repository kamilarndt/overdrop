//! OverDrop CLI — high-performance task & message manager
//! 
//! Usage:
//!   od init <workspace>
//!   od submit <title> --from <agent> [--assign <agent>]
//!   od claim <agent> <task-id>
//!   od done <task-id> [--result <json>]
//!   od fail <task-id> [--error <msg>]
//!   od list [folder]
//!   od status <task-id>
//!   od send <recipient> --msg <text> [--type <type>]
//!   od poll <agent>
//!   od archive [--days <n>]
//!   od dashboard

use clap::{Parser, Subcommand};
use rusqlite::Connection;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use uuid::Uuid;

#[derive(Parser)]
#[command(name = "od", about = "OverDrop CLI — Universal Agent Communication")]
struct Cli {
    /// OverDrop workspace directory
    #[arg(short, long, default_value = ".overdrop", env = "OVERDROP_DIR")]
    workspace: PathBuf,

    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Initialize workspace
    Init { path: Option<PathBuf> },
    /// Submit a new task
    Submit {
        title: String,
        #[arg(long, default_value = "cli")]
        from: String,
        #[arg(long)]
        assign: Option<String>,
        #[arg(long)]
        context: Option<String>,
        #[arg(long, default_value = "5")]
        priority: u8,
    },
    /// Claim a task
    Claim { agent: String, task_id: String },
    /// Complete a task
    Done {
        task_id: String,
        #[arg(long)]
        result: Option<String>,
    },
    /// Fail a task
    Fail {
        task_id: String,
        #[arg(long)]
        error: Option<String>,
    },
    /// List tasks
    List { folder: Option<String> },
    /// Show task status
    Status { task_id: String },
    /// Send a message via Mail Bus
    Send {
        recipient: String,
        #[arg(long)]
        msg: String,
        #[arg(long, default_value = "dispatch")]
        msg_type: String,
    },
    /// Poll messages
    Poll { agent: String },
    /// Archive old messages
    Archive {
        #[arg(long, default_value = "7")]
        days: u32,
    },
    /// Dashboard (watch mode)
    Dashboard,
}

// ---------------------------------------------------------------------------
// FS Protocol
// ---------------------------------------------------------------------------

fn ensure_dirs(root: &Path) {
    for folder in &["inbox", "active", "done", "failed", "blocked", "feedback"] {
        fs::create_dir_all(root.join(folder)).ok();
    }
}

fn task_path(root: &Path, folder: &str, task_id: &str) -> PathBuf {
    root.join(folder).join(format!("{}.json", task_id))
}

fn submit_task(root: &Path, title: &str, from: &str, assign: Option<&str>,
               context: Option<&str>, priority: u8) -> String {
    let id = Uuid::new_v4().to_string();
    let task = serde_json::json!({
        "id": id,
        "title": title,
        "status": "inbox",
        "from_agent": from,
        "assignee": assign,
        "context": context
            .and_then(|c| serde_json::from_str::<serde_json::Value>(c).ok())
            .unwrap_or(serde_json::json!({})),
        "result": {},
        "priority": priority,
        "max_retries": 3,
        "retry_count": 0,
        "version": 1,
        "created_at": chrono::Utc::now().to_rfc3339(),
    });
    
    let path = task_path(root, "inbox", &id);
    let tmp = path.with_extension("tmp");
    fs::write(&tmp, serde_json::to_string_pretty(&task).unwrap()).unwrap();
    fs::rename(&tmp, &path).unwrap();
    
    println!("✅ Task submitted: {}", &id[..8]);
    println!("   Title: {title}");
    println!("   From: {from} → {}", assign.unwrap_or("any"));
    id
}

fn claim_task(root: &Path, agent: &str, task_id: &str) -> bool {
    let src = task_path(root, "inbox", task_id);
    let dst = task_path(root, "active", task_id);
    
    match fs::rename(&src, &dst) {
        Ok(_) => {
            // Update assignee in the file
            let data: serde_json::Value = serde_json::from_str(
                &fs::read_to_string(&dst).unwrap()
            ).unwrap();
            let mut data = data;
            data["assignee"] = serde_json::Value::String(agent.to_string());
            data["status"] = serde_json::Value::String("claimed".to_string());
            let tmp = dst.with_extension("tmp");
            fs::write(&tmp, serde_json::to_string_pretty(&data).unwrap()).unwrap();
            fs::rename(&tmp, &dst).unwrap();
            println!("✅ Claimed: {task_id}");
            true
        }
        Err(_) => {
            eprintln!("❌ Task not available (already claimed or not found)");
            false
        }
    }
}

fn complete_task(root: &Path, task_id: &str, result: Option<&str>) {
    let src = task_path(root, "active", task_id);
    let dst = task_path(root, "done", task_id);
    
    if let Ok(_) = fs::rename(&src, &dst) {
        let data: serde_json::Value = serde_json::from_str(
            &fs::read_to_string(&dst).unwrap()
        ).unwrap();
        let mut data = data;
        data["status"] = serde_json::Value::String("done".to_string());
        if let Some(r) = result {
            if let Ok(rv) = serde_json::from_str::<serde_json::Value>(r) {
                data["result"] = rv;
            }
        }
        let tmp = dst.with_extension("tmp");
        fs::write(&tmp, serde_json::to_string_pretty(&data).unwrap()).unwrap();
        fs::rename(&tmp, &dst).unwrap();
        println!("✅ Completed: {task_id}");
    } else {
        eprintln!("❌ Task not found in active: {task_id}");
    }
}

fn fail_task(root: &Path, task_id: &str, error: Option<&str>) {
    let src = task_path(root, "active", task_id);
    if !src.exists() {
        eprintln!("❌ Task not found: {task_id}");
        return;
    }
    
    let mut data: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(&src).unwrap()
    ).unwrap();
    
    let retries = data["retry_count"].as_u64().unwrap_or(0) + 1;
    let max_retries = data["max_retries"].as_u64().unwrap_or(3);
    data["retry_count"] = serde_json::json!(retries);
    if let Some(e) = error {
        data["result"]["error"] = serde_json::Value::String(e.to_string());
    }
    
    if retries < max_retries {
        // Move back to inbox
        let dst = task_path(root, "inbox", task_id);
        data["status"] = serde_json::Value::String("inbox".to_string());
        let tmp = dst.with_extension("tmp");
        fs::write(&tmp, serde_json::to_string_pretty(&data).unwrap()).unwrap();
        fs::rename(&tmp, &dst).unwrap();
        println!("🔁 Failed, retry {retries}/{max_retries}: {task_id}");
    } else {
        // Final failure
        let dst = task_path(root, "failed", task_id);
        data["status"] = serde_json::Value::String("failed".to_string());
        let tmp = dst.with_extension("tmp");
        fs::write(&tmp, serde_json::to_string_pretty(&data).unwrap()).unwrap();
        fs::rename(&tmp, &dst).unwrap();
        // Cleanup source
        fs::remove_file(&src).ok();
        println!("❌ Failed permanently: {task_id}");
    }
}

fn list_tasks(root: &Path, folder: &str) {
    let dir = root.join(folder);
    if !dir.exists() {
        println!("📭 No tasks in {folder}/");
        return;
    }
    
    let mut entries: Vec<_> = fs::read_dir(&dir)
        .unwrap()
        .filter_map(|e| e.ok())
        .filter(|e| e.path().extension().map_or(false, |ext| ext == "json"))
        .collect();
    
    entries.sort_by_key(|e| e.metadata().unwrap().modified().unwrap());
    entries.reverse();
    
    if entries.is_empty() {
        println!("📭 No tasks in {folder}/");
        return;
    }
    
    println!("📋 Tasks in {folder}/ ({}):", entries.len());
    println!("{:-<80}", "");
    
    for entry in entries.iter().take(50) {
        let data: serde_json::Value = serde_json::from_str(
            &fs::read_to_string(entry.path()).unwrap()
        ).unwrap_or_default();
        
        let icon = match data["status"].as_str().unwrap_or("?") {
            "inbox" => "📥",
            "active" | "claimed" => "⚡",
            "done" => "✅",
            "failed" => "❌",
            "blocked" => "🔒",
            _ => "📄",
        };
        
        let id = data["id"].as_str().unwrap_or("?");
        let title = data["title"].as_str().unwrap_or("?");
        let status = data["status"].as_str().unwrap_or("?");
        
        println!("  {} [{:20}] {:8}... {:50}", icon, status, &id[..8.min(id.len())], &title[..50.min(title.len())]);
    }
}

fn show_status(root: &Path, task_id: &str) {
    for folder in &["inbox", "active", "done", "failed", "blocked", "feedback"] {
        let path = task_path(root, folder, task_id);
        if path.exists() {
            let data: serde_json::Value = serde_json::from_str(
                &fs::read_to_string(&path).unwrap()
            ).unwrap();
            println!("📄 Task: {task_id}");
            println!("   Title:   {}", data["title"].as_str().unwrap_or("-"));
            println!("   Status:  {}", data["status"].as_str().unwrap_or("-"));
            println!("   From:    {}", data["from_agent"].as_str().unwrap_or("-"));
            println!("   Assign:  {}", data["assignee"].as_str().unwrap_or("unassigned"));
            println!("   Priority:{}", data["priority"].as_u64().unwrap_or(0));
            println!("   Retries: {}/{}", 
                     data["retry_count"].as_u64().unwrap_or(0),
                     data["max_retries"].as_u64().unwrap_or(3));
            println!("   Context: {}", data["context"]);
            println!("   Result:  {}", data["result"]);
            return;
        }
    }
    eprintln!("❌ Task not found: {task_id}");
}

// ---------------------------------------------------------------------------
// Mail Bus (SQLite)
// ---------------------------------------------------------------------------

fn bus_connect(root: &Path) -> Connection {
    let db_path = root.join("overdrop.db");
    let conn = Connection::open(&db_path).unwrap();
    conn.execute_batch("PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON;").ok();
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            sender TEXT NOT NULL,
            recipient TEXT NOT NULL,
            payload TEXT DEFAULT '{}',
            read INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_mbox_recipient_unread ON messages(recipient, read);"
    ).unwrap();
    conn
}

fn bus_send(root: &Path, recipient: &str, msg_text: &str, msg_type: &str) {
    let conn = bus_connect(root);
    let id = Uuid::new_v4().to_string();
    let payload = serde_json::json!({"text": msg_text}).to_string();
    conn.execute(
        "INSERT INTO messages (id, type, sender, recipient, payload) VALUES (?1, ?2, ?3, ?4, ?5)",
        rusqlite::params![id, msg_type, "cli", recipient, payload],
    ).unwrap();
    println!("✅ Message sent: {}...", &id[..8]);
    println!("   {} → {} [{}]: {}", "cli", recipient, msg_type, &msg_text[..60.min(msg_text.len())]);
}

fn bus_poll(root: &Path, agent: &str) {
    let conn = bus_connect(root);
    let mut stmt = conn.prepare(
        "SELECT * FROM messages WHERE recipient=?1 AND read=0 ORDER BY created_at ASC LIMIT 50"
    ).unwrap();
    
    let msgs: Vec<_> = stmt.query_map(rusqlite::params![agent], |row| {
        Ok((
            row.get::<_, String>(0)?,
            row.get::<_, String>(1)?,
            row.get::<_, String>(2)?,
            row.get::<_, String>(5)?,
        ))
    }).unwrap().filter_map(|r| r.ok()).collect();
    
    if msgs.is_empty() {
        println!("📭 No unread messages for '{agent}'");
        return;
    }
    
    println!("📨 Unread for '{agent}' ({}):", msgs.len());
    for (id, tp, sender, payload) in &msgs {
        println!("  [{tp:12}] {sender} → {agent}: {}", &payload[..60.min(payload.len())]);
    }
    
    conn.execute("UPDATE messages SET read=1 WHERE recipient=?1", rusqlite::params![agent]).unwrap();
}

fn bus_archive(root: &Path, days: u32) {
    let conn = bus_connect(root);
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS messages_archive AS SELECT * FROM messages WHERE 0"
    ).unwrap();
    conn.execute(
        &format!("INSERT INTO messages_archive SELECT * FROM messages WHERE read=1 AND created_at < datetime('now', '-{} days')", days),
        [],
    ).unwrap();
    conn.execute(
        &format!("DELETE FROM messages WHERE read=1 AND created_at < datetime('now', '-{} days')", days),
        [],
    ).unwrap();
    println!("✅ Archived messages older than {days} days");
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

fn main() {
    let cli = Cli::parse();
    let ws = cli.workspace;
    
    match cli.command {
        Commands::Init { path } => {
            let p = path.unwrap_or(ws);
            ensure_dirs(&p);
            println!("✅ OverDrop workspace: {}", p.display());
            println!("   inbox/ active/ done/ failed/ blocked/ feedback/");
        }
        Commands::Submit { title, from, assign, context, priority } => {
            ensure_dirs(&ws);
            submit_task(&ws, &title, &from, assign.as_deref(), context.as_deref(), priority);
        }
        Commands::Claim { agent, task_id } => {
            claim_task(&ws, &agent, &task_id);
        }
        Commands::Done { task_id, result } => {
            complete_task(&ws, &task_id, result.as_deref());
        }
        Commands::Fail { task_id, error } => {
            fail_task(&ws, &task_id, error.as_deref());
        }
        Commands::List { folder } => {
            list_tasks(&ws, &folder.unwrap_or("inbox".to_string()));
        }
        Commands::Status { task_id } => {
            show_status(&ws, &task_id);
        }
        Commands::Send { recipient, msg, msg_type } => {
            bus_send(&ws, &recipient, &msg, &msg_type);
        }
        Commands::Poll { agent } => {
            bus_poll(&ws, &agent);
        }
        Commands::Archive { days } => {
            bus_archive(&ws, days);
        }
        Commands::Dashboard => {
            println!("🔻 OverDrop Dashboard: http://localhost:7737/");
            println!("   Press Ctrl+C to stop");
            // The dashboard is in Python — keep this as a convenience stub
            let status = std::process::Command::new("python3")
                .arg("-m")
                .arg("overdrop.dashboard")
                .arg(&ws)
                .status();
            match status {
                Ok(_) => {}
                Err(e) => eprintln!("Dashboard requires Python: {e}"),
            }
        }
    }
}
