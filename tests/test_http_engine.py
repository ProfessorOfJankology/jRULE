"""Run with: python -m unittest discover -s tests -v (in installed jRULE venv)."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import db, state, modules
from app.http_services import json_path, select_properties, validate_endpoint, render_body
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

    def test_json_mapping(self):
        data={'ok':True,'workstations':[{'hostname':'ESC-R1','username':'JGRA'}]}
        self.assertEqual(json_path(data,'workstations.0.username'),'JGRA')
        self.assertEqual(select_properties(data,{'current_user':'workstations.0.username'}),{'current_user':'JGRA'})
        presence={'connected_workstations':['ESC-R1'],'workstation_users':{'ESC-R1':['JGRA']}}
        mapping={
            'current_user':{'path':'workstation_users.ESC-R1.0','op':'value','default':None},
            'present':{'path':'connected_workstations','op':'contains','value':'ESC-R1','default':False},
        }
        self.assertEqual(select_properties(presence,mapping),{'current_user':'JGRA','present':True})
        gone={'connected_workstations':[],'workstation_users':{}}
        self.assertEqual(select_properties(gone,mapping),{'current_user':None,'present':False})

    def test_recursive_body_template(self):
        payload={'username':'{{ current.presence.user }}','value':'{{ args.message }}','other':[1,True]}
        ctx={'current':{'presence':{'user':'JGRA'}},'args':{'message':'Hi'}}
        self.assertEqual(render_body(payload,ctx),{'username':'JGRA','value':'Hi','other':[1,True]})

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