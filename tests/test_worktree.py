"""OverDrop — Worktree & Merge Queue Tests"""
import sys, os, tempfile, shutil, asyncio, uuid, subprocess as sp

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "python"))
from overdrop import WorktreeManager, MergeQueue

PASS = 0; FAIL = 0

def check(n, c, d=""):
    global PASS, FAIL
    if c: PASS += 1; print(f"  ✅ {n}")
    else: FAIL += 1; print(f"  ❌ {n} — {d}")

def setup_git(path):
    os.makedirs(path, exist_ok=True)
    sp.run(["git", "init"], cwd=path, check=True, capture_output=True)
    sp.run(["git", "config", "user.email", "test@overdrop.io"], cwd=path, capture_output=True)
    sp.run(["git", "config", "user.name", "OverDrop"], cwd=path, capture_output=True)
    with open(os.path.join(path, "README.md"), "w") as f: f.write("# test\n")
    sp.run(["git", "add", "-A"], cwd=path, capture_output=True)
    sp.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True)
    return path

async def test_worktree():
    tmp = tempfile.mkdtemp(prefix="od-wt-")
    repo = setup_git(os.path.join(tmp, "repo"))
    wt_root = os.path.join(tmp, "worktrees")
    wm = WorktreeManager(repo, worktree_root=wt_root)

    wt1 = wm.create("task-001", "pi")
    check("Worktree created", os.path.exists(wt1))
    check("Is git repo", os.path.exists(os.path.join(wt1, ".git")))

    with open(os.path.join(wt1, "api.py"), "w") as f:
        f.write("def hello(): pass\n")
    wm.commit_changes("task-001", "Add hello", "pi")
    check("Worktree tracked", "task-001" in wm.list_all())

    wt2 = wm.create("task-002", "hermes")
    check("Two worktrees", os.path.exists(wt2))

    wm.remove("task-001")
    check("Removed from list", "task-001" not in wm.list_all())

    shutil.rmtree(tmp)
    return True

async def test_merge():
    tmp = tempfile.mkdtemp(prefix="od-mq-")
    repo = setup_git(os.path.join(tmp, "repo"))
    wt_root = os.path.join(tmp, "worktrees")
    
    mq = MergeQueue(repo, base_branch="main", db_path=os.path.join(tmp, "od.db"))
    wm = WorktreeManager(repo, worktree_root=wt_root)

    t1 = f"task-{uuid.uuid4().hex[:8]}"
    t2 = f"task-{uuid.uuid4().hex[:8]}"

    wt1 = wm.create(t1, "pi")
    with open(os.path.join(wt1, "auth.py"), "w") as f: f.write("def login(): pass\n")
    wm.commit_changes(t1, "Add auth", "pi")
    
    wt2 = wm.create(t2, "hermes")
    with open(os.path.join(wt2, "db.py"), "w") as f: f.write("def connect(): pass\n")
    wm.commit_changes(t2, "Add db", "hermes")

    b1 = f"od/pi/{t1[:8]}"
    b2 = f"od/hermes/{t2[:8]}"

    mq.enqueue(t1, b1, wt1, "pi")
    mq.enqueue(t2, b2, wt2, "hermes")

    r1 = await mq.process_next()
    check("Merge 1", r1 is not None and r1.status == "merged")
    r2 = await mq.process_next()
    check("Merge 2", r2 is not None and r2.status == "merged")
    
    s = mq.get_status(t1)
    check("Status recorded", s is not None and s.get("merged_at") is not None)

    mq.close()
    shutil.rmtree(tmp)
    return True

async def run():
    global PASS; global FAIL
    print("="*60)
    print("🧪 Worktree & Merge Queue")
    print("="*60)
    
    print("\n--- Worktree ---")
    await test_worktree()
    
    print("\n--- Merge Queue ---")
    try: await test_merge()
    except Exception as e: FAIL += 1; print(f"  ❌ {e}")
    
    print("\n" + "="*60)
    print(f"📊 {PASS}/{PASS+FAIL} passed")
    return FAIL == 0

if __name__ == "__main__":
    sys.exit(0 if asyncio.run(run()) else 1)
