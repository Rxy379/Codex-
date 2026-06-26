# Codex-
ix_logs_sqlite 是一个 Windows 命令行工具，用于解决 Codex desktop 的 TRACE 日志高频写盘问题。它自动备份 ~/.codex/logs_2.sqlite，通过 SQLite trigger 静默拦截所有 level=TRACE 的 INSERT，然后执行 WAL checkpoint 并截断，最后采样验证不再增长。整个过程对 Codex 应用本身零影响，无报错。
[fix_logs_sqlite_exe.py](https://github.com/user-attachments/files/29390361/fix_logs_sqlite_exe.py)
# -*- coding: utf-8 -*-
"""
fix_logs_sqlite.exe — TRACE log interceptor for Codex logs_2.sqlite

Workflow:
1. Backup logs_2.sqlite (with timestamp)
2. Add BEFORE INSERT trigger: RAISE(IGNORE) for TRACE rows
3. Execute WAL checkpoint (TRUNCATE)
4. Sample confirm MAX(id) and WAL stop growing

Usage: Just run the exe. If Codex is running, close it first and retry.
"""

import sqlite3, os, shutil, sys, time, pathlib

def main():
    db = os.path.expanduser("~/.codex/logs_2.sqlite")
    backup_dir = os.path.join(os.path.dirname(db), "backup")
    
    print("=" * 60)
    print(" Codex TRACE Log Interceptor")
    print("=" * 60)
    print(f"\nTarget DB: {db}")
    
    if not os.path.exists(db):
        print(f"\n[ERROR] Database not found: {db}")
        print("Make sure Codex desktop is installed and has been run at least once.")
        input("\nPress Enter to exit...")
        return 1
    
    # Step 1: Backup
    print("\n" + "=" * 60)
    print("Step 1/4: Backup")
    print("=" * 60)
    os.makedirs(backup_dir, exist_ok=True)
    ts = str(int(time.time()))
    backup_path = os.path.join(backup_dir, f"logs_2.sqlite.backup.{ts}")
    shutil.copy2(db, backup_path)
    print(f"  DB  -> {backup_path}")
    print(f"  Size: {os.path.getsize(backup_path):,} bytes")
    for ext in ["-wal", "-shm"]:
        src = db + ext
        if os.path.exists(src):
            dst = backup_path + ext
            shutil.copy2(src, dst)
            print(f"  {ext} -> backed up ({os.path.getsize(dst):,} bytes)")
    
    # Connect
    conn = sqlite3.connect(db, timeout=15000)
    cur = conn.cursor()
    
    # Step 2: Add trigger
    print("\n" + "=" * 60)
    print("Step 2/4: Create TRACE block trigger")
    print("=" * 60)
    cur.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name='block_trace_inserts'")
    if cur.fetchone():
        print("  Trigger already exists. Skipping.")
    else:
        cur.execute("SELECT COUNT(*), SUM(CASE WHEN level='TRACE' THEN 1 ELSE 0 END) FROM logs")
        total, trace = cur.fetchone()
        pct = (trace * 100.0 / total) if total else 0
        print(f"  Before: total={total:,}, TRACE={trace:,} ({pct:.1f}%)")
        
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS block_trace_inserts
            BEFORE INSERT ON logs
            WHEN NEW.level = 'TRACE'
            BEGIN
                SELECT RAISE(IGNORE);
            END
        """)
        conn.commit()
        print("  Trigger created: block_trace_inserts")
        print("  -> All future TRACE inserts will be silently dropped.")
    
    # Step 3: WAL checkpoint
    print("\n" + "=" * 60)
    print("Step 3/4: WAL Checkpoint")
    print("=" * 60)
    wal_path = pathlib.Path(db + "-wal")
    if wal_path.exists():
        print(f"  WAL before: {wal_path.stat().st_size:,} bytes")
    
    for mode, label in [("PASSIVE", "PASSIVE"), ("TRUNCATE", "TRUNCATE")]:
        try:
            cur.execute(f"PRAGMA wal_checkpoint({mode})")
            ckpt = cur.fetchone()
            status = {0: "OK(truncated)", 1: "PASSIVE_OK", 2: "FULL_OK"}.get(ckpt[0], f"status={ckpt[0]}")
            print(f"  Checkpoint({label}): {status}")
            if ckpt[0] == 0:
                break
        except Exception as e:
            print(f"  Checkpoint({label}): {e}")
            if "locked" in str(e).lower():
                print("  -> DB locked! Close Codex desktop and retry.")
    
    if wal_path.exists():
        print(f"  WAL after:  {wal_path.stat().st_size:,} bytes")
    
    # Step 4: Verify
    print("\n" + "=" * 60)
    print("Step 4/4: Verify (sampling 5 seconds)")
    print("=" * 60)
    cur.execute("SELECT MAX(id) FROM logs")
    id1 = cur.fetchone()[0]
    wal_before = wal_path.stat().st_size if wal_path.exists() else 0
    print(f"  T+0s: MAX(id)={id1:,}, WAL={wal_before:,} bytes")
    
    for i in range(5):
        time.sleep(1)
        cur.execute("SELECT MAX(id) FROM logs")
        cur_id = cur.fetchone()[0]
        if cur_id != id1:
            wal_now = wal_path.stat().st_size if wal_path.exists() else 0
            cur.execute("SELECT level FROM logs WHERE id=?", (cur_id,))
            last = cur.fetchone()
            if last and last[0] == "TRACE":
                print(f"  T+{i+1}s: id={cur_id:,} (+{cur_id-id1}) - TRACE still getting through! Check trigger.")
            elif last:
                print(f"  T+{i+1}s: id={cur_id:,} (+{cur_id-id1}) - level={last[0]}")
    
    cur.execute("SELECT MAX(id) FROM logs")
    id2 = cur.fetchone()[0]
    delta_id = id2 - id1
    wal_after = wal_path.stat().st_size if wal_path.exists() else 0
    
    print(f"\n  Final delta: MAX(id)={delta_id:+d}, WAL={wal_after - wal_before:+d} bytes")
    if delta_id <= 10:
        print("\n  [OK] TRACE inserts blocked. DB growth is contained.")
    else:
        print("\n  [WARN] Growth still detected - trigger may not be active yet.")
    
    conn.close()
    print("\n" + "=" * 60)
    print(" Done! Backup saved to: " + backup_dir)
    print("=" * 60)
    input("\nPress Enter to exit...")
    return 0

if __name__ == "__main__":
    sys.exit(main())
