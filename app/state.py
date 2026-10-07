"""Global per-property snapshots. No global poll counter or value propagation."""
from __future__ import annotations
import json
from typing import Any
from contextlib import asynccontextmanager
from . import db


@asynccontextmanager
async def connection():
    conn = await db.connect()
    try:
        yield conn
    finally:
        await conn.close()


async def register_object(name: str, module: str, object_type: str, config: dict | None = None) -> None:
    if not name or "." in name or not all(c.isalnum() or c in "_-" for c in name):
        raise ValueError("Object name must contain only letters, numbers, hyphens or underscores")
    async with connection() as conn:
        found = await (await conn.execute("SELECT module FROM objects WHERE name=?",(name,))).fetchone()
        if found and found["module"] != module:
            raise ValueError(f"Object {name!r} is owned by module {found['module']!r}")
        await conn.execute("INSERT INTO objects(name,module,type,config_json) VALUES(?,?,?,?) "
                           "ON CONFLICT(name) DO UPDATE SET type=excluded.type",
                           (name,module,object_type,json.dumps(config or {})))
        await conn.commit()


async def update_properties(name: str, changes: dict[str, Any]) -> None:
    """One atomic batch. Only supplied property names advance last/current."""
    if not changes:
        return
    now = db.utc_now()
    async with connection() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        found = await (await conn.execute("SELECT 1 FROM objects WHERE name=?", (name,))).fetchone()
        if not found:
            raise KeyError(f"Unknown object: {name}")
        for key,value in changes.items():
            if not key or "." in key:
                raise ValueError(f"Invalid property: {key!r}")
            current = json.dumps(value,default=str)
            old = await (await conn.execute(
                "SELECT current_json,last_changed FROM object_properties WHERE object_name=? AND property_name=?",
                (name,key))).fetchone()
            last = old["current_json"] if old else None
            changed_at = now if not old or last != current else old["last_changed"]
            await conn.execute("""INSERT INTO object_properties
                (object_name,property_name,current_json,last_json,last_polled,last_changed)
                VALUES (?,?,?,?,?,?) ON CONFLICT(object_name,property_name) DO UPDATE SET
                last_json=excluded.last_json,current_json=excluded.current_json,
                last_polled=excluded.last_polled,last_changed=excluded.last_changed""",
                (name,key,current,last,now,changed_at))
        await conn.commit()



async def apply_source_poll(name: str, changes: dict[str, Any], discovered_json: str, polled_at: str) -> int:
    """Atomically apply source properties and advance source poll metadata."""
    async with connection() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        found = await (await conn.execute("SELECT 1 FROM objects WHERE name=?", (name,))).fetchone()
        if not found:
            raise KeyError(f"Unknown object: {name}")
        for key,value in changes.items():
            if not key or "." in key:
                raise ValueError(f"Invalid property: {key!r}")
            current=json.dumps(value,default=str)
            old=await (await conn.execute(
                "SELECT current_json,last_changed FROM object_properties WHERE object_name=? AND property_name=?",
                (name,key))).fetchone()
            last=old["current_json"] if old else None
            changed_at=polled_at if not old or last!=current else old["last_changed"]
            await conn.execute("""INSERT INTO object_properties
                (object_name,property_name,current_json,last_json,last_polled,last_changed)
                VALUES (?,?,?,?,?,?) ON CONFLICT(object_name,property_name) DO UPDATE SET
                last_json=excluded.last_json,current_json=excluded.current_json,
                last_polled=excluded.last_polled,last_changed=excluded.last_changed""",
                (name,key,current,last,polled_at,changed_at))
        await conn.execute("""UPDATE http_sources
            SET last_attempt=?,last_success=?,last_error=NULL,discovered_json=?,
                poll_sequence=poll_sequence+1,checks_since_poll=0
            WHERE name=?""",(polled_at,polled_at,discovered_json,name))
        row=await (await conn.execute("SELECT poll_sequence FROM http_sources WHERE name=?",(name,))).fetchone()
        await conn.commit()
        return int(row["poll_sequence"])


async def mark_sources_checked(sequences: dict[str,int]) -> None:
    """Increment checks_since_poll only if no newer poll replaced the snapshot."""
    if not sequences:
        return
    async with connection() as conn:
        await conn.execute("BEGIN IMMEDIATE")
        for name,sequence in sequences.items():
            await conn.execute("""UPDATE http_sources
                SET checks_since_poll=checks_since_poll+1
                WHERE name=? AND poll_sequence=?""",(name,int(sequence)))
        await conn.commit()


async def pool() -> dict[str,Any]:
    """Read coherent persisted global snapshot. Metadata is per property."""
    async with connection() as conn:
        await conn.execute("BEGIN")
        objects = await (await conn.execute("SELECT name,module,type,enabled FROM objects ORDER BY name")).fetchall()
        properties = await (await conn.execute("SELECT * FROM object_properties")).fetchall()
        sources = await (await conn.execute("SELECT name,poll_sequence,checks_since_poll,last_success FROM http_sources")).fetchall()
        await conn.commit()
    source_meta={row["name"]:{
        "poll_sequence":int(row["poll_sequence"]),
        "checks_since_poll":int(row["checks_since_poll"]),
        "last_poll":row["last_success"]
    } for row in sources}
    output = {row["name"]:{"module":row["module"],"type":row["type"],"enabled":bool(row["enabled"]),"properties":{},"source_meta":source_meta.get(row["name"])} for row in objects}
    for row in properties:
        output[row["object_name"]]["properties"][row["property_name"]] = {
            "current": json.loads(row["current_json"]) if row["current_json"] is not None else None,
            "last": json.loads(row["last_json"]) if row["last_json"] is not None else None,
            "last_polled": row["last_polled"],"last_changed":row["last_changed"]}
    return output


def condition_context(snapshot:dict[str,Any]) -> dict[str,Any]:
    """Existing evaluator syntax: current.<object>.<property>; previous.<object>.<property>."""
    result={"current":{},"previous":{},"meta":{}}
    for name,obj in snapshot.items():
        props=obj["properties"]
        result["current"][name]={k:v["current"] for k,v in props.items()}
        result["previous"][name]={k:v["last"] for k,v in props.items()}
        result["meta"][name]={k:{"last_polled":v["last_polled"],"last_changed":v["last_changed"]} for k,v in props.items()}
        if obj.get("source_meta"):
            result["meta"][name].update(obj["source_meta"])
    return result