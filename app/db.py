"""SQLite storage for the shared pool, HTTP sources, actions and rules."""
import json
import os
from pathlib import Path
from datetime import datetime, timezone
import aiosqlite

DB_PATH=Path(os.environ.get("JRULE_DB", "/var/lib/jrule/jrule.db"))
def utc_now():return datetime.now(timezone.utc).isoformat()
async def connect():
    DB_PATH.parent.mkdir(parents=True,exist_ok=True)
    conn=await aiosqlite.connect(DB_PATH)
    conn.row_factory=aiosqlite.Row
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.execute("PRAGMA busy_timeout=5000")
    return conn
async def init_db():
    conn=await connect()
    try:
        await conn.executescript("""
        CREATE TABLE IF NOT EXISTS objects(name TEXT PRIMARY KEY,module TEXT NOT NULL,type TEXT NOT NULL,config_json TEXT NOT NULL DEFAULT '{}',enabled INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS object_properties(object_name TEXT NOT NULL REFERENCES objects(name) ON DELETE CASCADE,property_name TEXT NOT NULL,current_json TEXT,last_json TEXT,last_polled TEXT,last_changed TEXT,PRIMARY KEY(object_name,property_name));
        CREATE TABLE IF NOT EXISTS http_sources(name TEXT PRIMARY KEY REFERENCES objects(name) ON DELETE CASCADE,url TEXT NOT NULL,interval_seconds INTEGER NOT NULL DEFAULT 60,mapping_json TEXT NOT NULL DEFAULT '{}',auth_env TEXT NOT NULL DEFAULT '',auth_header TEXT NOT NULL DEFAULT 'Authorization',auth_prefix TEXT NOT NULL DEFAULT 'Bearer ',enabled INTEGER NOT NULL DEFAULT 1,last_attempt TEXT,last_success TEXT,last_error TEXT);
        CREATE TABLE IF NOT EXISTS http_actions(name TEXT PRIMARY KEY,url TEXT NOT NULL,body_json TEXT NOT NULL DEFAULT '{}',auth_env TEXT NOT NULL DEFAULT '',auth_header TEXT NOT NULL DEFAULT 'Authorization',auth_prefix TEXT NOT NULL DEFAULT 'Bearer ',enabled INTEGER NOT NULL DEFAULT 0,timeout_seconds REAL NOT NULL DEFAULT 10);
        CREATE TABLE IF NOT EXISTS global_rules(id INTEGER PRIMARY KEY AUTOINCREMENT,name TEXT NOT NULL,enabled INTEGER NOT NULL DEFAULT 1,priority INTEGER NOT NULL DEFAULT 100,condition_json TEXT NOT NULL,actions_json TEXT NOT NULL DEFAULT '[]',cooldown_seconds INTEGER NOT NULL DEFAULT 0,stop_processing INTEGER NOT NULL DEFAULT 0,last_write_at TEXT,last_evaluated_at TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS event_log(id INTEGER PRIMARY KEY AUTOINCREMENT,created_at TEXT NOT NULL,level TEXT NOT NULL,event_type TEXT NOT NULL,message TEXT NOT NULL,details_json TEXT);
        """)
        # Lightweight schema migration for source-field discovery.
        cols={row[1] for row in await (await conn.execute("PRAGMA table_info(http_sources)")).fetchall()}
        if 'discovered_json' not in cols:
            await conn.execute("ALTER TABLE http_sources ADD COLUMN discovered_json TEXT NOT NULL DEFAULT '[]'")
        if 'poll_sequence' not in cols:
            await conn.execute("ALTER TABLE http_sources ADD COLUMN poll_sequence INTEGER NOT NULL DEFAULT 0")
        if 'checks_since_poll' not in cols:
            await conn.execute("ALTER TABLE http_sources ADD COLUMN checks_since_poll INTEGER NOT NULL DEFAULT 0")
        await conn.execute("INSERT OR IGNORE INTO settings(key,value_json) VALUES('rule_interval_seconds','10')")
        await conn.execute("INSERT OR IGNORE INTO objects(name,module,type,config_json,enabled) VALUES('variables','builtin.variables','variables','{}',1)")
        await conn.commit()
    finally: await conn.close()
async def get_settings():
    conn=await connect()
    try:
        rows=await (await conn.execute('SELECT key,value_json FROM settings')).fetchall()
        return {r['key']:json.loads(r['value_json']) for r in rows}
    finally: await conn.close()
async def set_settings(settings):
    conn=await connect()
    try:
        for k,v in settings.items():
            await conn.execute('INSERT INTO settings(key,value_json) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json',(k,json.dumps(v)))
        await conn.commit()
    finally: await conn.close()
async def log_event(*,level,event_type,message,details=None,**_):
    conn=await connect()
    try:
        await conn.execute('INSERT INTO event_log(created_at,level,event_type,message,details_json) VALUES(?,?,?,?,?)',(utc_now(),level,event_type,message,json.dumps(details,default=str) if details is not None else None))
        await conn.commit()
    finally: await conn.close()