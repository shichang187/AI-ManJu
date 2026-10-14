"""Isolated DPAPI and HTTP fixtures, never account keys or live providers."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.model_service import ModelService
from modules.credential_store import CredentialStore
from modules.curated_prompts import adapt, approved
from modules.prompt_library import PromptLibrary


class Fixture(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass
    def do_GET(self):
        self.server.paths.append(self.path)
        if self.server.error:
            self.send_response(self.server.error)
            if self.server.error in (302,307):
                self.send_header('Location', self.server.url+'/leak-target')
            self.end_headers()
            self.wfile.write(self.headers.get('Authorization','').encode())
            return
        data={'data':[{'id':'fixture-chat','sub_type':'chat'},
                      {'id':'fixture-embedding','sub_type':'embedding'},
                      {'id':'fixture-image','type':'image'},
                      {'id':self.server.sentinel,'sub_type':'chat'}]}
        raw=json.dumps(data).encode();self.send_response(200);self.end_headers();self.wfile.write(raw)
    def do_POST(self):
        self.server.posts+=1
        self.rfile.read(int(self.headers.get('Content-Length',0)))
        raw=json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}]}).encode()
        self.send_response(200);self.end_headers();self.wfile.write(raw)


class ProviderSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Fixture)
        self.server.url=f'http://127.0.0.1:{self.server.server_address[1]}'
        self.server.sentinel='fictional-sentinel-for-unit-tests'
        self.server.error=0;self.server.paths=[];self.server.posts=0
        self.worker=threading.Thread(target=self.server.serve_forever,daemon=True);self.worker.start()
        self.service=ModelService(self.root/'private')
        self.config={'provider':'custom','base_url':self.server.url+'/v1',
            'model':'fixture-chat','timeout':5,'api_key':self.server.sentinel}
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.tmp.cleanup()

    def test_default_preset_without_directory_preparation_or_model_guess(self):
        s=self.service.status()
        self.assertEqual(s['settings']['provider'],'siliconflow')
        self.assertEqual(s['settings']['base_url'],'https://api.siliconflow.cn/v1')
        self.assertFalse(s['has_key']);self.assertFalse(s['ready']);self.assertEqual(s['settings']['model'],'')

    def test_encryption_restart_and_no_cleartext_in_any_saved_file(self):
        status=self.service.save(self.config)
        self.assertTrue(status['has_key']);self.assertNotIn(self.server.sentinel,json.dumps(status))
        for file in (self.root/'private').rglob('*'):
            if file.is_file():self.assertNotIn(self.server.sentinel.encode(),file.read_bytes())
        if os.name=='nt':self.assertTrue(ModelService(self.root/'private').status()['ready'])

    def test_empty_keep_replacement_and_clear(self):
        self.service.save(self.config)
        self.assertTrue(self.service.save({**self.config,'api_key':''})['ready'])
        self.service.save({**self.config,'key_action':'replace','api_key':'fictional-replacement-for-tests'})
        ref=self.service.credentials.ref('custom',self.config['base_url'])
        self.assertEqual(self.service.credentials.get(ref),'fictional-replacement-for-tests')
        self.assertFalse(self.service.save({**self.config,'api_key':'','key_action':'clear'})['has_key'])

    def test_supplier_and_endpoint_isolation_and_restore_model(self):
        self.service.save(self.config)
        self.assertFalse(self.service.save({'provider':'siliconflow'})['has_key'])
        self.assertFalse(self.service.save({'provider':'custom','base_url':self.server.url+'/other'})['has_key'])
        restored=self.service.save({'provider':'custom','base_url':self.config['base_url']})
        self.assertTrue(restored['has_key']);self.assertEqual(restored['settings']['model'],'fixture-chat')

    def test_decryption_failure_is_explicit_and_never_falls_back_to_environment(self):
        self.service.save(self.config)
        if os.name=='nt':
            ref=self.service.credentials.ref('custom',self.config['base_url'])
            self.service.credentials._path(ref).write_bytes(b'corrupt-ciphertext')
            with patch.dict(os.environ,{'EXPAND_LLM_API_KEY':'fictional-hidden-environment'}):
                status=ModelService(self.root/'private').status()
            self.assertFalse(status['ready']);self.assertIn('解密',status['message'])

    def test_models_filter_and_auth_does_not_generate(self):
        self.service.save(self.config);result=self.service.fetch_models()
        self.assertEqual(result['model_list']['status'],'authenticated')
        self.assertIn('fixture-chat',result['model_list']['models'])
        self.assertNotIn('fixture-embedding',result['model_list']['models'])
        self.assertNotIn('fixture-image',result['model_list']['models'])
        self.assertNotIn(self.server.sentinel,json.dumps(result));self.assertEqual(self.server.posts,0)

    def test_siliconflow_uses_chat_filter(self):
        self.service.save({'provider':'siliconflow','api_key':self.server.sentinel})
        # The fake service remains the only network destination in this test.
        with patch.object(self.service,'_provider_json',return_value={'data':[{'id':'a'}]}) as request:
            self.service.fetch_models();self.assertEqual(request.call_args.args[0],'/models?sub_type=chat')

    def test_model_list_failures_keep_configuration_and_reject_redirects(self):
        self.service.save(self.config)
        for error in (401,403,404,429,500,302,307):
            self.server.error=error;before=copy.deepcopy(self.service.settings)
            result=self.service.fetch_models()
            self.assertEqual(result['model_list']['status'],'error')
            self.assertIn(str(error),result['model_list']['message'])
            self.assertNotIn(self.server.sentinel,json.dumps(result));self.assertEqual(before,self.service.settings)
        self.assertTrue(all('leak-target' not in p for p in self.server.paths))

    def test_network_failure_and_explicit_test_json_call(self):
        self.service.save(self.config)
        with patch('urllib.request.OpenerDirector.open',side_effect=TimeoutError):
            result=self.service.fetch_models()
        self.assertIn('超时',result['model_list']['message']);self.assertTrue(result['ready'])
        self.assertEqual(self.server.posts,0)
        self.assertEqual(self.service.test_request()['call']['status'],'success');self.assertEqual(self.server.posts,1)

    def test_session_only_platform_does_not_write_plaintext(self):
        with patch.object(self.service.credentials,'persistent',False):
            self.service.save(self.config)
            self.assertTrue(self.service.status()['has_key'])
            for file in (self.root/'private').rglob('*'):
                if file.is_file():self.assertNotIn(self.server.sentinel.encode(),file.read_bytes())

    def test_server_session_guard_rejects_key_post_and_list_without_session(self):
        with patch.dict(os.environ,{'AIMANJU_OUTPUT_DIR':str(self.root/'output'),
            'AIMANJU_PRIVATE_DATA_DIR':str(self.root/'http-private')}):
            module=importlib.reload(importlib.import_module('workbench_server'))
        host=ThreadingHTTPServer(('127.0.0.1',0),module.WorkbenchHandler)
        thread=threading.Thread(target=host.serve_forever,daemon=True);thread.start()
        base=f'http://127.0.0.1:{host.server_address[1]}'
        try:
            for endpoint in ('/api/model-settings','/api/model-list','/api/model-test'):
                request=Request(base+endpoint,data=json.dumps(self.config if endpoint.endswith('settings') else {}).encode(),headers={'Content-Type':'application/json'})
                with self.assertRaises(HTTPError) as err:urlopen(request)
                self.assertEqual(err.exception.code,403)
            request=Request(base+'/api/model-settings',data=json.dumps(self.config).encode(),
                headers={'Content-Type':'application/json','X-Workbench-Session':module.SESSION_KEY})
            status=json.load(urlopen(request));self.assertTrue(status['has_key']);self.assertNotIn(self.server.sentinel,json.dumps(status))
        finally:host.shutdown();host.server_close()


class CuratedTests(unittest.TestCase):
    def entry(self):
        return {'id':'fixture-shot','title':'测试原创镜头','text':'角色 {{角色}}；环境 {{场景}}；动作 {{动作}}；时长 {{时长}}；{{前段}}–{{后段}}；说话者 {{说话者}}；对白 {{对白}}；画幅 {{画幅}}',
            'purpose_override':'shot','model_version':'Seedance 2.0','scope':'单镜','duration_range':[3,10],
            'review_status':'内容审核通过','completeness':'complete','scores':[2,2,2,2,1],
            'required_assets':[],'stage':'运镜','kind':'测试原创','usage':'模板','sources':[]}
    def storyboard(self):
        return {'characters':[{'name':'小雨','description':'黄衣'},{'name':'阿明','description':'蓝衣'}],
            'scenes':[{'scene_id':'room','location':'教室','time_of_day':'白天'}, {'scene_id':'street','location':'街道'}],
            'shots':[{'shot_id':'stable-id','description':'小雨打开抽屉','characters':['小雨'],'scene_id':'room',
                      'duration':4,'speaker':'小雨','dialogue':'找到了。'}]}

    def test_context_profiles_scene_time_speaker_and_no_story_mutation(self):
        board=self.storyboard();before=copy.deepcopy(board)
        result=adapt(self.entry(),board,'stable-id',{'style':'动漫'})
        self.assertIn('小雨：黄衣',result['text']);self.assertNotIn('阿明',result['text'])
        self.assertIn('白天，教室',result['text']);self.assertNotIn('街道',result['text'])
        self.assertIn('时长 4',result['text']);self.assertIn('1–3',result['text'])
        self.assertIn('说话者 小雨',result['text']);self.assertEqual(result['missing_variables'],[])
        self.assertEqual(board,before)

    def test_gate_and_multi_shot_or_duration_mismatch(self):
        entry=self.entry()
        for field,value in [('completeness','truncated'),('scores',[1,1,1,1,1]),('scores',[10]),('scores',[3,2,2,2,1]),('model_version','Seedance 2.5'),('hard_defects',['missing assets'])]:
            self.assertFalse(approved({**entry,field:value}))
        for changed in ({**entry,'scope':'连续多镜'},{**entry,'duration_range':[12,15]}):
            with self.assertRaises(ValueError):adapt(changed,self.storyboard(),'stable-id')

    def test_environment_and_dialogue_timing_protection(self):
        entry=self.entry();board=self.storyboard()
        with self.assertRaisesRegex(ValueError,'环境镜'):
            adapt({**entry,'category':'环境空镜'},board,'stable-id')
        board['shots'][0]['dialogue']='这是一段无法在四秒内自然说完而需要按语义拆分的长对白。'
        with self.assertRaisesRegex(ValueError,'对白过长'):
            adapt(entry,board,'stable-id')

    def test_missing_speaker_and_reference_assets_are_visible(self):
        board=self.storyboard();board['shots'][0]['speaker']=''
        result=adapt({**self.entry(),'required_assets':['首帧角色图']},board,'stable-id')
        self.assertIn('说话者',result['missing_variables']);self.assertEqual(result['required_assets'],['首帧角色图'])

    def test_unreviewed_old_catalog_excluded_without_changing_original(self):
        with tempfile.TemporaryDirectory() as folder:
            private=Path(folder);original={'entries':[{'id':'old','title':'旧镜头模板','text':'运镜：平移','kind':'入门模板','stage':'运镜','usage':'镜头模板','sources':[]}],'report':None}
            (private/'library.json').write_text(json.dumps(original),encoding='utf-8')
            (private/'curated.json').write_text(json.dumps([self.entry()]),encoding='utf-8')
            builtin=private/'builtin.json';builtin.write_text('[]')
            lib=PromptLibrary(builtin,private)
            self.assertEqual(lib.query({'collection':['curated']})['total'],1)
            self.assertEqual(lib.query({'collection':['reference']})['total'],1)
            self.assertEqual(json.loads((private/'library.json').read_text()),original)


if __name__=='__main__':unittest.main()
