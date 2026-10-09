"""Run with: python -m unittest discover -s tests -v (in installed jRULE venv)."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db, state, modules
from app.http_services import json_path, select_properties, discover_fields, merge_discovered_fields, validate_endpoint, render_body
from app.rules import evaluate_condition

class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.old=db.DB_PATH
        db.DB_PATH=Path(self.tmp.name)/'test.db'
        await db.init_db()

    async def asyncTearDown(self):
        db.DB_PATH=self.old
        self.tmp.cleanup()

    async def test_independent_property_poll(self):
        await state.register_object('presence','japi.get','japi')
        await state.register_object('queue','japi.get','japi')
        await state.update_properties('presence',{'user':None,'online':False})
        await state.update_properties('queue',{'waiting':5})
        await state.update_properties('presence',{'user':'JGRA'})
        result=await state.pool()
        self.assertEqual(result['presence']['properties']['user']['last'],None)
        self.assertEqual(result['presence']['properties']['user']['current'],'JGRA')
        self.assertIsNone(result['presence']['properties']['online']['last'])
        self.assertEqual(result['presence']['properties']['online']['current'],False)
        self.assertEqual(result['queue']['properties']['waiting']['current'],5)
        await state.update_properties('presence',{'user':'JGRA'})
        self.assertEqual((await state.pool())['presence']['properties']['user']['last'],'JGRA')

    async def test_source_checks_since_poll_generation_guard(self):
        await state.register_object('presence','japi.get','japi')
        async with state.connection() as conn:
            await conn.execute("""INSERT INTO http_sources
                (name,url,interval_seconds,mapping_json,enabled)
                VALUES(?,?,?,?,1)""",('presence','/v1/test',30,'{}'))
            await conn.commit()
        seq1=await state.apply_source_poll('presence',{'count':1},'[]','2026-10-07T08:00:00+00:00')
        snap=await state.pool()
        self.assertEqual(snap['presence']['source_meta']['checks_since_poll'],0)
        await state.mark_sources_checked({'presence':seq1})
        self.assertEqual((await state.pool())['presence']['source_meta']['checks_since_poll'],1)

        seq2=await state.apply_source_poll('presence',{'count':2},'[]','2026-10-07T08:01:00+00:00')
        self.assertGreater(seq2,seq1)
        self.assertEqual((await state.pool())['presence']['source_meta']['checks_since_poll'],0)

        # A stale evaluator from seq1 must not mark the newer poll as checked.
        await state.mark_sources_checked({'presence':seq1})
        self.assertEqual((await state.pool())['presence']['source_meta']['checks_since_poll'],0)
        await state.mark_sources_checked({'presence':seq2})
        self.assertEqual((await state.pool())['presence']['source_meta']['checks_since_poll'],1)

    async def test_cross_object_condition(self):
        await state.register_object('presence','japi.get','japi')
        await state.register_object('queue','japi.get','japi')
        await state.update_properties('presence',{'user':None})
        await state.update_properties('presence',{'user':'JGRA'})
        await state.update_properties('queue',{'waiting':3})
        ctx=state.condition_context(await state.pool())
        expr={'kind':'group','logic':'all','items':[
            {'kind':'condition','left':'current.presence.user','operator':'eq','right':'JGRA'},
            {'kind':'condition','left':'current.queue.waiting','operator':'gt','right':0},
        ]}
        self.assertTrue(evaluate_condition(expr,ctx))

    def test_right_expression_with_transform(self):
        from app.rules import evaluate_condition
        condition={'kind':'condition','left':'current.presence.username','operator':'eq',
                   'right_type':'expression',
                   'right':{'source':'current.presence.users','transforms':[{'op':'first'}]}}
        ctx={'current':{'presence':{'username':'jordan.grey','users':['jordan.grey']}}}
        self.assertTrue(evaluate_condition(condition,ctx))

    def test_json_mapping(self):
        data={'ok':True,'workstations':[{'hostname':'ESC-R1','username':'JGRA'}]}
        self.assertEqual(json_path(data,'workstations.0.username'),'JGRA')
        selected=select_properties(data,{'current_user':'workstations.0.username'})
        self.assertTrue(selected['ok'])
        self.assertEqual(selected['current_user'],'JGRA')
        presence={'connected_workstations':['ESC-R1'],'workstation_users':{'ESC-R1':['JGRA']}}
        mapping={
            'current_user':{'path':'workstation_users.ESC-R1.0','op':'value','default':None},
            'present':{'path':'connected_workstations','op':'contains','value':'ESC-R1','default':False},
        }
        selected=select_properties(presence,mapping)
        self.assertEqual(selected['current_user'],'JGRA')
        self.assertTrue(selected['present'])
        gone={'connected_workstations':[],'workstation_users':{}}
        selected=select_properties(gone,mapping)
        self.assertIsNone(selected['current_user'])
        self.assertFalse(selected['present'])

    def test_discovered_fields(self):
        fields=discover_fields({'workstation_users':{'ESC-R1':['JGRA']},'connected_workstations':['ESC-R1'],'count':1})
        paths={f['path'] for f in fields}
        self.assertIn('workstation_users.ESC-R1',paths)
        self.assertNotIn('workstation_users.ESC-R1.0',paths)
        self.assertIn('connected_workstations',paths)
        self.assertNotIn('connected_workstations.0',paths)
        self.assertIn('count',paths)

    def test_discovered_fields_are_cumulative(self):
        first=merge_discovered_fields(
            [{'path':'connected_workstations.0','value':'OLD','type':'str','present':False,'last_seen':'2026-10-06T00:00:00+00:00'}],
            discover_fields({'workstation_users':{'ESC-R1':['JGRA']}}),
            '2026-10-07T08:00:00+00:00')
        second=merge_discovered_fields(first,discover_fields({'workstation_users':{}}),'2026-10-07T08:01:00+00:00')
        by_path={f['path']:f for f in second}
        self.assertIn('workstation_users.ESC-R1',by_path)
        self.assertFalse(by_path['workstation_users.ESC-R1']['present'])
        self.assertEqual(by_path['workstation_users.ESC-R1']['value'],['JGRA'])
        self.assertNotIn('connected_workstations.0',by_path)

    def test_recursive_body_template(self):
        payload={'username':'{{ current.presence.user }}','value':'{{ args.message }}','other':[1,True]}
        ctx={'current':{'presence':{'user':'JGRA'}},'args':{'message':'Hi'}}
        self.assertEqual(render_body(payload,ctx),{'username':'JGRA','value':'Hi','other':[1,True]})

    def test_typed_whole_value_templates(self):
        payload={
            'username':'{{ args.username }}',
            'count':'{{ args.count }}',
            'enabled':'{{ args.enabled }}',
            'items':'{{ args.items }}',
            'embedded':'user={{ args.username }}',
        }
        ctx={'args':{'username':None,'count':3,'enabled':True,'items':['a','b']}}
        rendered=render_body(payload,ctx)
        self.assertIsNone(rendered['username'])
        self.assertEqual(rendered['count'],3)
        self.assertIs(rendered['enabled'],True)
        self.assertEqual(rendered['items'],['a','b'])
        with self.assertRaises(ValueError):
            render_body({'embedded':'user={{ args.username }}'},ctx)

    def test_structured_action_parameter_transform(self):
        ctx={'current':{'Presence':{'workstation_users':{'ESC-UTILITY-01':['jordan.grey']}}}}
        arg={'kind':'parameter','expression':{
            'source':'current.Presence.workstation_users.ESC-UTILITY-01',
            'transforms':[{'op':'first'}]
        }}
        self.assertEqual(modules.resolve_action_argument(arg,ctx),'jordan.grey')
        self.assertIsNone(
            modules.resolve_action_argument(
                {'kind':'parameter','expression':'current.Presence.workstation_users.ESC-R1'},
                ctx
            )
        )

    async def test_force_run_ignores_conditions_and_cooldown(self):
        from app import engine
        async with state.connection() as conn:
            await conn.execute("""INSERT INTO global_rules
                (name,enabled,condition_json,actions_json,cooldown_seconds,last_write_at)
                VALUES(?,?,?,?,?,?)""",
                ('manual-test',0,json.dumps({'kind':'condition','left':'current.variables.x','operator':'eq','right':999}),
                 json.dumps([{'method':'variables.set','arguments':{'name':'forced','value':True}}]),
                 3600,db.utc_now()))
            await conn.commit()
            row=await (await conn.execute("SELECT id FROM global_rules WHERE name='manual-test'")).fetchone()
        with patch.dict(os.environ,{'JRULE_ENABLE_ACTIONS':'1'}):
            result=await engine.force_run_rule(row['id'])
        self.assertEqual(result['actions_executed'],1)
        self.assertTrue((await state.pool())['variables']['properties']['forced']['current'])

    def test_action_argument_types(self):
        self.assertEqual(modules.resolve_action_argument('103',{}),'103')
        self.assertEqual(modules.resolve_action_argument(103,{}),103)
        self.assertIsNone(modules.resolve_action_argument(None,{}))
        self.assertEqual(modules.resolve_action_argument({'kind':'literal','value':'103'},{}),{'kind':'literal','value':'103'})
        self.assertEqual(modules.unwrap_literal({'kind':'literal','value':'{{ raw }}'}),'{{ raw }}')

    def test_endpoint_is_japi_only(self):
        self.assertEqual(validate_endpoint('/v1/mdserver/sessions'),'/v1/mdserver/sessions')
        self.assertEqual(validate_endpoint('/v1/mdserver/workstations?include_users=true'),'/v1/mdserver/workstations?include_users=true')
        for endpoint in ('file:///etc/passwd','https://evil.example/v1/foo',
                         '/v1/../foo','/v1/%2e%2e/foo','//evil','/v1/a#fragment'):
            with self.subTest(endpoint=endpoint),self.assertRaises(ValueError):
                validate_endpoint(endpoint)

    async def test_custom_variable_action(self):
        with patch.dict(os.environ,{'JRULE_ENABLE_ACTIONS':'1'}):
            await modules.invoke('variables.set','',{'name':'armed','value':True},{})
        result=await state.pool()
        self.assertTrue(result['variables']['properties']['armed']['current'])
        with patch.dict(os.environ,{'JRULE_ENABLE_ACTIONS':'1'}):
            await modules.invoke('variables.set','',{'name':'armed','value':False},{})
        result=await state.pool()
        self.assertTrue(result['variables']['properties']['armed']['last'])
        self.assertFalse(result['variables']['properties']['armed']['current'])

    async def test_event_source_filter(self):
        await db.log_event(level='info',event_type='source_poll',message='presence poll',
                           details={'response':{'workstation_users':{'ESC-R1':['jordan.grey']}}})
        await db.log_event(level='error',event_type='source_poll_error',message='timeout')
        async with state.connection() as conn:
            rows=await (await conn.execute(
                "SELECT event_type,details_json FROM event_log WHERE event_type LIKE 'source_%' ORDER BY id"
            )).fetchall()
        self.assertEqual([row['event_type'] for row in rows],['source_poll','source_poll_error'])
        self.assertEqual(json.loads(rows[0]['details_json'])['response']['workstation_users']['ESC-R1'],['jordan.grey'])

    async def test_log_retention_prunes_only_expired_events(self):
        from datetime import datetime,timezone,timedelta
        old=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()
        now=db.utc_now()
        async with state.connection() as conn:
            await conn.execute("INSERT INTO event_log(created_at,level,event_type,message) VALUES(?,?,?,?)",(old,'info','source_poll','old'))
            await conn.execute("INSERT INTO event_log(created_at,level,event_type,message) VALUES(?,?,?,?)",(now,'info','source_poll','recent'))
            await conn.commit()
        self.assertEqual(await db.prune_event_logs(1),1)
        async with state.connection() as conn:
            rows=await (await conn.execute('SELECT message FROM event_log')).fetchall()
        self.assertEqual([r['message'] for r in rows],['recent'])
        with self.assertRaises(ValueError):
            await db.prune_event_logs(0)

    def test_japi_url_uses_server_side_base(self):
        from app.http_services import japi_url
        with patch.dict(os.environ,{'JRULE_JAPI_BASE_URL':'http://127.0.0.1:8088'}):
            self.assertEqual(japi_url('/v1/sql/ping'),'http://127.0.0.1:8088/v1/sql/ping')

    def test_key_read_from_private_file(self):
        from app.http_services import headers_for
        keyfile=Path(self.tmp.name)/'key'
        keyfile.write_text('some-test-key\n')
        with patch.dict(os.environ,{'JRULE_JAPI_KEY_FILE':str(keyfile)}):
            self.assertEqual(headers_for(),{'X-API-Key':'some-test-key'})

if __name__=='__main__':unittest.main()