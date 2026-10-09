"""One serialized cycle: poll sources, publish state, evaluate rules and actions."""
from __future__ import annotations
import asyncio
import json
import os
from datetime import datetime,timezone
from . import db, state, modules, http_services
from .rules import evaluate_condition

evaluation_lock = asyncio.Lock()


async def evaluate_once() -> list[dict]:
    """Serialise manual and scheduled runs: never execute one rule twice concurrently."""
    async with evaluation_lock:
        published=await http_services.poll_all_sources()
        return await _evaluate_once_unlocked({entry['name'] for entry in published})


def condition_source_dependencies(node, source_names: set[str]) -> set[str]:
    """Discover jAPI source references used by a saved condition tree."""
    if not isinstance(node,dict):
        return set()
    if node.get("kind")=="group":
        required=set()
        for item in node.get("items") or []:
            required.update(condition_source_dependencies(item,source_names))
        return required
    required=set()
    expressions=[node.get("left")]
    if node.get("right_type") in ("variable","expression"):
        expressions.append(node.get("right"))
    for expression in expressions:
        path=expression.get("source","") if isinstance(expression,dict) else expression
        if not isinstance(path,str):
            continue
        parts=path.split(".")
        if len(parts)>=3 and parts[0] in ("current","previous","meta") and parts[1] in source_names:
            required.add(parts[1])
    return required


async def _evaluate_once_unlocked(published_sources: set[str] | None = None) -> list[dict]:
    snapshot=await state.pool()
    context=state.condition_context(snapshot)
    now=datetime.now(timezone.utc)
    source_names={name for name,obj in snapshot.items() if obj.get('source_meta')}
    results=[]
    async with state.connection() as conn:
        rows=await (await conn.execute("SELECT * FROM global_rules WHERE enabled=1 ORDER BY priority,id")).fetchall()
    for row in rows:
        rule=dict(row)
        try:
            condition=json.loads(rule["condition_json"])
            required=condition_source_dependencies(condition,source_names)
            missing=required-(published_sources if published_sources is not None else source_names)
            if missing:
                results.append({"id":rule["id"],"matched":False,
                                "skipped_stale_sources":sorted(missing),"executed":False})
                continue
            matched=evaluate_condition(condition,context)
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
async def force_run_rule(rule_id: int) -> dict:
    """Run one saved rule's actions regardless of condition/cooldown. Explicit manual action."""
    async with evaluation_lock:
        async with state.connection() as conn:
            row=await (await conn.execute("SELECT * FROM global_rules WHERE id=?",(rule_id,))).fetchone()
        if row is None:
            raise KeyError(f"Unknown rule: {rule_id}")
        rule=dict(row)
        snapshot=await state.pool()
        context=state.condition_context(snapshot)
        executed=0
        try:
            if os.getenv("JRULE_ENABLE_ACTIONS","0")!="1":
                raise PermissionError("Actions disabled (JRULE_ENABLE_ACTIONS=0)")
            for action in json.loads(rule["actions_json"]):
                await modules.invoke(action["method"],action.get("object",""),action.get("arguments",{}),context)
                executed+=1
            if executed:
                async with state.connection() as conn:
                    await conn.execute("UPDATE global_rules SET last_write_at=? WHERE id=?",(db.utc_now(),rule_id))
                    await conn.commit()
            await db.log_event(level="info",event_type="rule_forced",message=f"{rule['name']}: manually ran {executed} action(s)",details={"rule_id":rule_id,"actions_executed":executed})
            return {"ok":True,"rule_id":rule_id,"forced":True,"actions_executed":executed}
        except Exception as exc:
            await db.log_event(level="error",event_type="rule_force_error",message=f"{rule['name']}: {exc}",details={"rule_id":rule_id,"actions_completed":executed})
            raise
