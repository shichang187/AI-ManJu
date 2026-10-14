"""Real local HTTP fixtures; never uses external services or production data."""
import copy
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.model_service import ModelService
from modules.prompt_library import purpose, PromptLibrary


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        self.server.received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
        self.server.entered.set()
        self.server.release.wait(8)
        if self.server.error:
            self.send_response(self.server.error)
            if self.server.error == 302:
                self.send_header('Location', self.server.url + '/redirect-target')
            self.end_headers()
            self.wfile.write(b'provider-private-error-body')
            return
        body = json.dumps({'choices': [{'message': {'content': self.server.content},
                                       'finish_reason': self.server.finish_reason}]}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ModelWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory()
        cls.root = Path(cls.scratch.name)
        with patch.dict(os.environ, {'AIMANJU_OUTPUT_DIR': str(cls.root / 'output'),
                    'AIMANJU_PRIVATE_DATA_DIR': str(cls.root / 'private'),
                    'EXPAND_LLM_API_KEY': '', 'EXPAND_LLM_BASE_URL': '', 'EXPAND_LLM_MODEL': ''}):
            cls.module = importlib.reload(importlib.import_module('workbench_server'))
        cls.host = ThreadingHTTPServer(('127.0.0.1', 0), cls.module.WorkbenchHandler)
        cls.host_thread = threading.Thread(target=cls.host.serve_forever, daemon=True)
        cls.host_thread.start()
        cls.url = f'http://127.0.0.1:{cls.host.server_address[1]}'
        cls.provider = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
        cls.provider.url = f'http://127.0.0.1:{cls.provider.server_address[1]}'
        cls.provider_thread = threading.Thread(target=cls.provider.serve_forever, daemon=True)
        cls.provider_thread.start()
        # Fictional fixture, never an account credential.
        cls.key_file = cls.root / 'fixture-credential.txt'
        cls.key_file.write_text('fictional-test-credential', encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.provider.release.set()
        for server in (cls.host, cls.provider):
            server.shutdown()
            server.server_close()
        cls.host_thread.join()
        cls.provider_thread.join()
        cls.scratch.cleanup()

    def request(self, path, data=None, headers=None):
        request = Request(self.url + path, data=None if data is None else json.dumps(data).encode(),
                headers={'Content-Type': 'application/json', 'X-Workbench-Session': self.module.SESSION_KEY,
                         **(headers or {})})
        try:
            response = urlopen(request, timeout=12)
        except HTTPError as response:
            return response.code, json.load(response)
        with response:
            return response.status, json.load(response)

    def setUp(self):
        self.request('/api/reset', {})
        self.module.MODELS = ModelService(self.root / 'private')
        self.provider.error = 0
        self.provider.finish_reason = 'stop'
        self.provider.received = []
        self.provider.entered = threading.Event()
        self.provider.release = threading.Event()
        self.provider.release.set()
        self.provider.content = json.dumps({'title': '原创测试：蓝色钥匙',
          'characters': [{'name': '小雨', 'description': '黄衣'}],
          'scenes': [{'scene_id': 'room', 'location': '教室'}],
          'shots': [{'shot_id': 'a', 'description': '小雨打开抽屉。', 'duration': 3, 'scene_id': 'room'},
                    {'shot_id': 'b', 'description': '她找到蓝色钥匙。', 'duration': 4, 'scene_id': 'room'}]})
        self.assertEqual(self.request('/api/model-settings', {'base_url': self.provider.url + '/v1',
             'model': 'fixture-model', 'key_file': str(self.key_file), 'timeout': 5})[0], 200)
        self.args = {'text': '小雨打开抽屉，找到蓝色钥匙。', 'generation_mode': 'api',
                     'director_instruction': '保持蓝色钥匙与结尾', 'target_shots': 2,
                     'location': '教室', 'time_of_day': '白天', 'min_sec': 3, 'max_sec': 6}

    def test_config_private_restart_and_no_credential_response(self):
        status = self.request('/api/model-status')[1]
        self.assertTrue(status['ready'])
        self.assertNotIn('fictional-test-credential', json.dumps(status))
        restored = ModelService(self.root / 'private').status()
        self.assertEqual(restored['settings'], status['settings'])
        self.assertTrue(restored['ready'])
        self.assertIn('未验证', status['message'])
        self.assertEqual(self.provider.received, [])

    def test_config_origin_validation_and_missing_file(self):
        config = self.request('/api/model-status')[1]['settings']
        self.assertEqual(self.request('/api/model-settings', config, {'Origin': 'https://foreign.example'})[0], 403)
        self.assertEqual(self.request('/api/model-settings', {**config, 'api_key': 'not-accepted'})[0], 400)
        for endpoint in ('http://example.test/v1', 'https://user:password@example.test/v1', 'https://example.test/v1?secret=x'):
            self.assertEqual(self.request('/api/model-settings', {**config, 'base_url': endpoint})[0], 400)
        self.request('/api/model-settings', {**config, 'key_file': str(self.root / 'missing.txt')})
        self.assertFalse(self.request('/api/model-status')[1]['ready'])
        self.assertEqual(self.request('/api/expand', self.args)[0], 400)
        self.assertEqual(self.provider.received, [])

    def test_real_local_request_context_source_and_profiles(self):
        code, result = self.request('/api/expand', self.args)
        self.assertEqual(code, 200)
        self.assertEqual(result['generation']['mode'], 'api')
        self.assertEqual(result['storyboard']['shots'][0]['scene_id'], 'room')
        self.assertEqual(result['storyboard']['characters'][0]['name'], '小雨')
        sent = self.provider.received[0]
        self.assertEqual(sent['model'], 'fixture-model')
        prompt = sent['messages'][0]['content']
        self.assertIn('保持蓝色钥匙与结尾', prompt)
        self.assertIn('教室', prompt)
        self.assertIn('白天', prompt)
        self.assertEqual(self.request('/api/model-status')[1]['call']['status'], 'success')
        self.assertEqual(ModelService(self.root / 'private').status()['call']['status'], 'success')
        self.assertEqual(self.request('/api/state')[1]['state']['storyboard'], result['storyboard'])
        self.request('/api/create-project', {})
        state = self.request('/api/state')[1]['state']
        self.assertEqual(self.module.STORE.load(state['project_id'])['storyboard']['generation_info']['mode'], 'api')

    def test_http_failure_and_invalid_json_preserve_previous_storyboard(self):
        previous = self.request('/api/expand', {**self.args, 'generation_mode': 'local'})[1]['storyboard']
        for status in (401, 403, 404, 429, 500, 302):
            self.provider.error = status
            code, result = self.request('/api/expand', self.args)
            self.assertEqual(code, 400)
            self.assertIn(str(status), result['error'])
            self.assertNotIn('provider-private-error-body', result['error'])
            self.assertNotIn('fictional-test-credential', json.dumps(result))
            self.assertEqual(self.request('/api/state')[1]['state']['storyboard'], previous)
            self.assertEqual(self.request('/api/model-status')[1]['call']['status'], 'error')
        self.provider.error = 0
        self.provider.content = '{"shots":[{"duration":-1}]}'
        self.assertEqual(self.request('/api/expand', self.args)[0], 400)
        self.assertEqual(self.request('/api/state')[1]['state']['storyboard'], previous)

    def test_timeout_truncated_output_and_invalid_constraints(self):
        with patch('urllib.request.OpenerDirector.open', side_effect=TimeoutError):
            with self.assertRaisesRegex(ValueError, '超时'):
                self.module.MODELS.generate(self.args)
        self.assertEqual(self.request('/api/model-status')[1]['call']['status'], 'error')
        self.provider.finish_reason = 'length'
        self.assertEqual(self.request('/api/expand', self.args)[0], 400)
        self.provider.received.clear()
        self.assertEqual(self.request('/api/expand', {**self.args, 'min_sec': 7, 'max_sec': 3})[0], 400)
        self.assertEqual(self.provider.received, [])

    def test_local_mode_never_contacts_provider_or_hidden_environment(self):
        with patch('modules.browser_workflow._llm_expand', side_effect=AssertionError('hidden call')):
            code, result = self.request('/api/expand', {**self.args, 'generation_mode': 'local'})
        self.assertEqual(code, 200)
        self.assertEqual(result['generation']['mode'], 'local')
        self.assertIn('未调用模型', result['generation']['message'])
        self.assertEqual(self.provider.received, [])

    def test_status_responsive_duplicate_rejected_and_project_change_preserved(self):
        previous = self.request('/api/expand', {**self.args, 'generation_mode': 'local'})[1]['storyboard']
        self.provider.release.clear()
        with ThreadPoolExecutor(1) as executor:
            future = executor.submit(self.request, '/api/expand', self.args)
            try:
                self.assertTrue(self.provider.entered.wait(3))
                self.assertEqual(self.request('/api/model-status')[1]['call']['status'], 'running')
                self.assertIn('服务重启', ModelService(self.root / 'private').status()['call']['message'])
                self.assertEqual(self.request('/api/expand', self.args)[0], 400)
                self.assertEqual(self.request('/api/state', {'state': {'inputs': {'text': '编辑期间的新故事'}}})[0], 200)
            finally:
                self.provider.release.set()
            self.assertEqual(future.result()[0], 400)
        saved = self.request('/api/state')[1]['state']
        self.assertEqual(saved['storyboard'], previous)
        self.assertEqual(saved['inputs']['text'], '编辑期间的新故事')

    def test_manual_request_matches_direct_call_and_does_not_invoke_provider(self):
        prompt = self.request('/api/model-request', self.args)[1]['request']
        self.assertEqual(self.provider.received, [])
        self.request('/api/expand', self.args)
        self.assertEqual(self.provider.received[0]['messages'][0]['content'], prompt)


class PurposeTests(unittest.TestCase):
    def test_conservative_content_routing(self):
        self.assertEqual(purpose({'text': '你是分镜师。按故事输出镜头。', 'usage': '先给对话模型'})[0], 'instruction')
        self.assertEqual(purpose({'text': '你是库存清点员。输出数量。', 'usage': '先给对话模型'})[0], 'reference')
        self.assertEqual(purpose({'text': '点击安装后开始运镜教程。', 'usage': '填写后使用'})[0], 'reference')
        self.assertEqual(purpose({'text': '运镜：平移\n画面：雨伞', 'usage': '选取并改写'})[0], 'shot')
        self.assertEqual(purpose({'text': '长篇原文风格' * 1200, 'original_kind': '资料原文', 'usage': '先给对话模型'})[0], 'reference')

    def test_personal_reclassification_filter_and_original_unchanged(self):
        with tempfile.TemporaryDirectory() as scratch:
            lib = PromptLibrary(Path(__file__).parents[1] / 'data/prompts/builtin.json', Path(scratch))
            before = copy.deepcopy(lib.builtin)
            self.assertEqual(lib.query({'purpose': ['shot']})['total'], 2)
            for use in ('instruction', 'shot', 'reference'):
                result = lib.query({'purpose': [use]})
                self.assertTrue(all(e['purpose'] == use for e in result['items']))
            item = lib.save_personal({'source_id': 'starter-board', 'text': '原创个人镜头模板', 'purpose': 'shot'})
            self.assertEqual(lib.detail(item['id'])['purpose'], 'shot')
            self.assertEqual(lib.builtin, before)
            with self.assertRaises(ValueError):
                lib.query({'purpose': ['unknown']})


if __name__ == '__main__':
    unittest.main()
