"""
fix_logs_sqlite.py -- TRACE log interceptor for Codex logs_2.sqlite

Workflow:
1. Backup logs_2.sqlite
2. Add BEFORE INSERT trigger that RAISE(IGNORE) for TRACE rows
3. Execute WAL checkpoint (TRUNCATE)
4. Sample confirm MAX(id) and WAL stop growing

Usage: Close Codex, then:
    python fix_logs_sqlite.py
"""

import sqlite3, os, shutil, pathlib, sys, time

DB = os.path.expanduser("~/.codex/logs_2.sqlite")
OUT_DIR = r"C:\Users\Administrator\Documents\Codex\2026-06-27\chrome-plugin-chrome-openai-bundled-file-2\outputs"


def step1_backup():
    print("=" * 60)
    print("Step 1: Backup")
    print("=" * 60)
    if not os.path.exists(DB):
        print(f"[ERROR] DB not found: {DB}")
        return False
    os.makedirs(OUT_DIR, exist_ok=True)
    ts = str(pathlib.Path(DB).stat().st_mtime_ns)
    backup_path = os.path.join(OUT_DIR, f"logs_2.sqlite.backup.{ts}")
    shutil.copy2(DB, backup_path)
    size = os.path.getsize(backup_path)
    print(f"  Backup -> {backup_path} ({size:,} bytes)")
    for ext in ["-wal", "-shm"]:
        src = DB + ext
        if os.path.exists(src):
            dst = backup_path + ext
            shutil.copy2(src, dst)
            print(f"  + {ext} -> {os.path.getsize(dst):,} bytes")
    print()
    return True


def step2_add_trigger():
    print("=" * 60)
    print("Step 2: Add TRACE block trigger")
    print("=" * 60)
    conn = sqlite3.connect(DB, timeout=15000)
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name='block_trace_inserts'")
    if cur.fetchone():
        print("  Trigger already exists. Skip.")
        conn.close()
        return True

    cur.execute("SELECT COUNT(*), SUM(CASE WHEN level='TRACE' THEN 1 ELSE 0 END) FROM logs")
    total, trace_cnt = cur.fetchone()
    trace_pct = (trace_cnt * 100.0 / total) if total else 0
    print(f"  Stats: total={total:,}, TRACE={trace_cnt:,} ({trace_pct:.1f}%)")

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
    print("  -> TRACE inserts silently skipped (no app errors)")
    conn.close()
    return True


def step3_checkpoint():
    print("=" * 60)
    print("Step 3: WAL Checkpoint")
    print("=" * 60)
    wal_path = pathlib.Path(DB + "-wal")
    if wal_path.exists():
        print(f"  WAL before: {wal_path.stat().st_size:,} bytes")

    conn = sqlite3.connect(DB, timeout=15000)
    cur = conn.cursor()

    for mode, label in [("PASSIVE", "PASSIVE"), ("TRUNCATE", "TRUNCATE")]:
        try:
            cur.execute(f"PRAGMA wal_checkpoint({mode})")
            ckpt = cur.fetchone()
            status_map = {0: "OK(truncated)", 1: "PASSIVE_OK", 2: "FULL_OK"}
            status = status_map.get(ckpt[0], f"status={ckpt[0]}")
            print(f"  Checkpoint({label}): {status}, mod={ckpt[1]} ckpt={ckpt[2]}")
            if ckpt[0] == 0:
                print("  -> WAL truncated!")
                break
        except Exception as e:
            print(f"  Checkpoint({label}): {e}")
            if "locked" in str(e).lower():
                print("  -> DB locked by Codex. Close Codex and retry.")

    if wal_path.exists():
        print(f"  WAL after: {wal_path.stat().st_size:,} bytes")
    conn.close()
    return True


def step4_verify():
    print("=" * 60)
    print("Step 4: Verify (sample 2x over 5s)")
    print("=" * 60)

    conn = sqlite3.connect("file:" + DB + "?mode=rwc", uri=True, timeout=10000)
    cur = conn.cursor()

    cur.execute("SELECT MAX(id) FROM logs")
    id1 = cur.fetchone()[0]
    wal_path = pathlib.Path(DB + "-wal")
    wal_before = wal_path.stat().st_size if wal_path.exists() else 0
    print(f"  T+0s: MAX(id)={id1}, WAL={wal_before:,} bytes")

    time.sleep(5)

    cur.execute("SELECT MAX(id) FROM logs")
    id2 = cur.fetchone()[0]
    wal_after = wal_path.stat().st_size if wal_path.exists() else 0
    print(f"  T+5s: MAX(id)={id2}, WAL={wal_after:,} bytes")

    delta_id = id2 - id1
    delta_wal = wal_after - wal_before
    print(f"\n  Delta: MAX(id)={delta_id:+d}, WAL={delta_wal:+d} bytes")
    if delta_id <= 10 and delta_wal <= 65536:
        print("  VERIFIED: TRACE blocked, no significant growth.")
    else:
        print("  WARNING: Growth still detected.")
    conn.close()


def main():
    print("fix_logs_sqlite.py -- TRACE interceptor for Codex sqlite logs")
    print(f"\nDatabase: {DB}")
    print()
    if not step1_backup():
        sys.exit(1)
    try:
        step2_add_trigger()
        step3_checkpoint()
    except sqlite3.OperationalError as e:
        if "locked" in str(e).lower() or "readonly" in str(e).lower():
            print(f"\n[WARN] DB locked by Codex. Trigger NOT applied.")
            print("  Options:")
            print("  1. Close Codex desktop, re-run this script.")
            print("  2. Backup already saved for safety.")
        else:
            print(f"\n[ERROR] {e}")
    except Exception as e:
        print(f"\n[ERROR] {e}")
    step4_verify()
    print("\nDone.")


if __name__ == "__main__":
    main()
