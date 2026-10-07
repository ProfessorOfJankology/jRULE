"""Global, periodic rule evaluations independent of device polling."""
from __future__ import annotations
import asyncio
import json
import os
from datetime import datetime,timezone
from . import db, state, modules
from .rules import evaluate_condition

evaluation_lock = asyncio.Lock()


async def evaluate_once() -> list[dict]:
    """Serialise manual and scheduled runs: never execute one rule twice concurrently."""
    async with evaluation_lock:
        return await _evaluate_once_unlocked()


async def _evaluate_once_unlocked() -> list[dict]:
    snapshot=await state.pool()
    context=state.condition_context(snapshot)
    sequences={name:int(obj["source_meta"]["poll_sequence"])
               for name,obj in snapshot.items() if obj.get("source_meta")}
    now=datetime.now(timezone.utc)
    results=[]
    try:
        async with state.connection() as conn:
            rows=await (await conn.execute("SELECT * FROM global_rules WHERE enabled=1 ORDER BY priority,id")).fetchall()
        for row in rows:
            rule=dict(row)
            try:
                matched=evaluate_condition(json.loads(rule["condition_json"]),context)
                written=False
                skipped=False
                if matched and rule["last_write_at"] and rule["cooldown_seconds"]:
                    skipped=(now-datetime.fromisoformat(rule["last_write_at"])).total_seconds()<rule["cooldown_seconds"]
                if matched and not skipped and os.getenv("JRULE_ENABLE_ACTIONS", "0") == "1":
                    for item in json.loads(rule["actions_json"]):
                        parameters=item.get("arguments",{})
                        await modules.invoke(item["method"],item.get("object",""),parameters,context)
                        written=True
                async with state.connection() as conn:
                    await conn.execute("UPDATE global_rules SET last_evaluated_at=?,last_write_at=CASE WHEN ? THEN ? ELSE last_write_at END WHERE id=?",
                                       (db.utc_now(),int(written),db.utc_now(),rule["id"]))
                    await conn.commit()
                results.append({"id":rule["id"],"matched":matched,"skipped_cooldown":skipped,"executed":written})
                if written:
                    await db.log_event(level="info",event_type="rule_executed",message=f"{rule['name']}: actions executed")
                if matched and not skipped and rule["stop_processing"]:
                    break
            except Exception as exc:
                await db.log_event(level="error",event_type="global_rule_error",message=f"{rule['name']}: {exc}",rule_id=None)
                results.append({"id":rule["id"],"error":str(exc)})
        return results
    finally:
        # Only increment a source if its poll sequence is still the one evaluated.
        await state.mark_sources_checked(sequences)


async def loop(stop:asyncio.Event)->None:
    while not stop.is_set():
        try:
            await evaluate_once()
        except Exception as exc:
            await db.log_event(level="error",event_type="engine_error",message=str(exc))
        settings=await db.get_settings()
        interval=max(1,int(settings.get("rule_interval_seconds",10)))
        try:
            await asyncio.wait_for(stop.wait(),timeout=interval)
        except asyncio.TimeoutError:
            pass