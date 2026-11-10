#!/usr/bin/env python3
"""Independent stale-cycle watchdog; intended to run as a root systemd oneshot."""
import json
import os
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

DB = Path('/var/lib/jrule/jrule.db')
HISTORY = Path('/var/lib/jrule/.watchdog-restarts.json')
SERVICE = 'jrule.service'
WINDOW_SECONDS = 15 * 60
MAX_RESTARTS = 3


def inspect(db_path=DB, now=None):
    now = now or datetime.now(timezone.utc)
    # A read-only connection must not create an empty database on misconfiguration.
    with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True, timeout=5) as conn:
        row = conn.execute("SELECT last_polled FROM object_properties WHERE object_name='DateTime' AND property_name='datetime'").fetchone()
        interval_row = conn.execute("SELECT value_json FROM settings WHERE key='rule_interval_seconds'").fetchone()
    interval = max(1, int(json.loads(interval_row[0]))) if interval_row else 10
    threshold = max(180, 3 * interval)
    if not row or not row[0]:
        return None, threshold
    age = (now - datetime.fromisoformat(row[0])).total_seconds()
    return age, threshold


def main():
    status = subprocess.run(['systemctl', 'is-active', '--quiet', SERVICE], check=False)
    if status.returncode:
        print('jRULE is not active; leaving recovery to its systemd service', flush=True)
        return 0
    started = subprocess.run(['systemctl', 'show', SERVICE, '-p', 'ExecMainStartTimestampMonotonic', '--value'],
                             capture_output=True, text=True, check=True)
    started_us = int(started.stdout.strip() or '0')
    uptime = (time.monotonic_ns() / 1000 - started_us) / 1_000_000 if started_us else 0
    try:
        age, threshold = inspect()
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        print(f'Unable to inspect polling heartbeat: {exc}; no automatic restart', file=sys.stderr, flush=True)
        return 1
    if uptime < threshold:
        return 0  # Start-up grace period, including initial database setup.
    if age is not None and age <= threshold:
        return 0
    now = time.time()
    try:
        history = json.loads(HISTORY.read_text()) if HISTORY.exists() else []
        history = [float(t) for t in history if now - float(t) < WINDOW_SECONDS]
    except (ValueError, OSError, TypeError) as exc:
        print(f'Cannot read restart history: {exc}; refusing automatic restart', file=sys.stderr, flush=True)
        return 1
    if len(history) >= MAX_RESTARTS:
        print(f'Polling stale ({age} seconds); recovery suppressed after {MAX_RESTARTS} restarts in 15 minutes',
              file=sys.stderr, flush=True)
        return 1
    pid = subprocess.run(['systemctl', 'show', SERVICE, '-p', 'MainPID', '--value'],
                         capture_output=True, text=True, check=True).stdout.strip()
    print(f'Polling stale: age={age}, threshold={threshold}s, pid={pid}, uptime={uptime:.0f}s; restarting', flush=True)
    # Persist the attempt before restart, so failed restarts count toward the limit.
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    temporary = HISTORY.with_name(HISTORY.name + f'.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(history + [now]))
    os.chmod(temporary, 0o600)
    os.replace(temporary, HISTORY)
    restart = subprocess.run(['systemctl', 'restart', SERVICE], timeout=45, check=False)
    if restart.returncode:
        print(f'Restart failed with status {restart.returncode}', file=sys.stderr, flush=True)
        return 1
    print('jRULE restart requested successfully; subsequent timer runs verify freshness', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
