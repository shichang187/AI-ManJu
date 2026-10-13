import copy
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class WorkbenchAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.scratch = tempfile.TemporaryDirectory()
        cls.root = Path(cls.scratch.name)
        with patch.dict(os.environ, {'AIMANJU_OUTPUT_DIR': str(cls.root / 'output'),
                                    'AIMANJU_PRIVATE_DATA_DIR': str(cls.root / 'private'),
                                    'EXPAND_LLM_API_KEY': ''}):
            cls.module = importlib.reload(importlib.import_module('workbench_server'))
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), cls.module.WorkbenchHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.scratch.cleanup()

    def request(self, path, data=None, headers=None):
        request = Request(self.url + path, data=None if data is None else json.dumps(data).encode(),
                          headers={'Content-Type': 'application/json',
                                   'X-Workbench-Session': self.module.SESSION_KEY, **(headers or {})})
        try:
            response = urlopen(request, timeout=10)
        except HTTPError as response:
            return response.code, json.load(response)
        with response:
            return response.status, json.load(response)

    def test_library_detail_and_invalid_paging(self):
        code, data = self.request('/api/prompts')
        self.assertEqual(code, 200)
        self.assertEqual(data['builtin_count'], 7)
        code, entry = self.request('/api/prompts/detail?id=starter-camera')
        self.assertEqual(code, 200)
        self.assertIn('{{角色}}', entry['text'])
        self.assertEqual(self.request('/api/prompts?page=broken')[0], 400)
        self.assertEqual(self.request('/api/prompts/detail?id=absent')[0], 400)

    def test_import_origin_validation_and_private_persistence(self):
        folder = self.root / 'input'
        folder.mkdir(exist_ok=True)
        (folder / '人物.txt').write_text('人物固定服装', encoding='utf8')
        self.assertEqual(self.request('/api/prompts/import', {'folder': str(folder)},
                                     {'Origin': 'https://other.example'})[0], 403)
        code, result = self.request('/api/prompts/import', {'folder': str(folder)}, {'Origin': self.url})
        self.assertEqual((code, result['status']), (200, 'running'))
        deadline = time.monotonic() + 10
        while self.request('/api/prompts/import-progress')[1]['status'] == 'running':
            self.assertLess(time.monotonic(), deadline)
            time.sleep(.01)
        self.assertEqual(self.request('/api/prompts/import-progress')[1]['report']['entries'], 1)
        self.assertTrue((self.root / 'private/library.json').exists())
        self.assertEqual(self.request('/api/prompts/import', {'folder': '.'})[0], 400)

    def test_favorites_api_rejects_cross_origin_and_bad_data(self):
        self.assertEqual(self.request('/api/prompts/favorites', {'favorites': ['starter-camera']},
                                     {'Origin': 'https://other.example'})[0], 403)
        self.assertEqual(self.request('/api/prompts/favorites', {'favorites': 'bad'})[0], 400)
        self.assertEqual(self.request('/api/prompts/favorites', {'favorites': ['starter-camera']})[0], 200)
        self.assertEqual(self.request('/api/prompts?favorites=1')[1]['total'], 1)

    def test_existing_storyboard_flow_and_library_undo_metadata(self):
        with patch.dict(os.environ, {'EXPAND_LLM_API_KEY': ''}):
            code, data = self.request('/api/expand', {'text': '少女在雨夜车站等待朋友。',
                                     'character_desc': '红衣少女', 'target_shots': 5, 'min_sec': 3, 'max_sec': 6})
        self.assertEqual(code, 200)
        sb = data['storyboard']
        self.assertEqual(len(sb['shots']), 5)
        original = copy.deepcopy(sb)
        sb['shots'][0]['prompt_before_library'] = sb['shots'][0]['prompt']
        sb['shots'][0]['prompt'] = '修改后的镜头提示词'
        sb['shots'][0]['prompt_library_id'] = 'starter-camera'
        self.assertEqual(self.request('/api/save-storyboard', sb)[0], 200)
        persisted = self.request('/api/state')[1]['state']['storyboard']
        self.assertEqual(persisted['environment_anchor'], original['environment_anchor'])
        self.assertEqual(persisted['shots'][1:], original['shots'][1:])
        self.assertEqual(persisted['shots'][0]['prompt_before_library'], original['shots'][0]['prompt'])
        sb['shots'][0]['prompt'] = sb['shots'][0].pop('prompt_before_library')
        del sb['shots'][0]['prompt_library_id']
        self.request('/api/save-storyboard', sb)
        self.assertEqual(self.request('/api/state')[1]['state']['storyboard'], original)


if __name__ == '__main__':
    unittest.main()
