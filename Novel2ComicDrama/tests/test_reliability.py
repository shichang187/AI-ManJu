import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.project_store import ProjectStore, atomic_json
from modules.media_export import scan, snapshot, export, probe, run
from modules.browser_workflow import build_storyboard
from modules.creative import parse_model_result
from modules.prompt_library import PromptLibrary
from modules.catalog_import import content_labels

if os.environ.get('AIMANJU_REQUIRE_MEDIA_TESTS') == '1' and not (shutil.which('ffmpeg') and shutil.which('ffprobe')):
    raise RuntimeError('Required media integration dependencies are unavailable')

def story(n=3):
    return {'title': '同名故事', 'shots': [{'shot_id': i, 'duration': .4, 'description': f'动作{i}',
            'dialogue': f'对白{i}', 'narration': f'旁白{i}', 'prompt': f'提示词{i}'} for i in range(1,n+1)]}

class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=ProjectStore(self.root/'browser')
    def test_same_title_save_restore_and_conflict(self):
        a=self.store.create(story()); b=self.store.create(story())
        self.assertNotEqual(a['project_id'],b['project_id'])
        sb=copy.deepcopy(a['storyboard']);sb['shots'][0].update(prompt='新提示词',dialogue='新对白')
        saved=self.store.save(a['project_id'],sb,expected=1)
        loaded=ProjectStore(self.root/'browser').load(a['project_id'])
        self.assertEqual(loaded,saved)
        self.assertEqual(self.store.load(b['project_id'])['storyboard'],b['storyboard'])
        with self.assertRaises(ValueError):self.store.save(a['project_id'],sb,expected=1)
        self.assertEqual(self.store.restore(a['project_id'],1)['storyboard'],a['storyboard'])
    def test_legacy_migration_keeps_all_original_bytes(self):
        old=self.root/'browser'/'旧项目'; old.mkdir(parents=True)
        atomic_json(old/'storyboard.json',story())
        (old/'1.mp4').write_bytes(b'old-media'); (old/'final.mp4').write_bytes(b'old-export')
        before={p.name:p.read_bytes() for p in old.iterdir()}
        record=self.store.migrate(old)
        self.assertEqual(self.store.migrate(old)['project_id'],record['project_id'])
        self.store.save(record['project_id'],story(2))
        self.assertEqual({p.name:p.read_bytes() for p in old.iterdir()},before)
        with self.assertRaises(ValueError):self.store.migrate(self.root)
    def test_missing_duplicate_and_group_mapping(self):
        r=self.store.create(story()); folder=self.store.folder(r['project_id'])
        for name in ('1.mp4','3.mp4'):(folder/name).write_bytes(b'placeholder')
        data=scan(r,folder)
        self.assertFalse(data['ready']);self.assertTrue(any(i['shot_id']=='2' for i in data['issues']))
        (folder/'2.mp4').write_bytes(b'x');(folder/'2.mov').write_bytes(b'y')
        self.assertFalse(scan(r,folder)['ready'])
        bindings=[{'shot_ids':[1,2],'filename':'1.mp4'},{'shot_ids':[3],'filename':'3.mp4'}]
        self.assertTrue(scan({**r,'bindings':bindings},folder)['ready'])
        self.assertFalse(scan({**r,'bindings':[{'shot_ids':[1,2,3],'filename':'../outside.mp4'}]},folder)['ready'])
    def test_long_story_quotes_and_literal_braces(self):
        text='小雨在白天教室整理书本，说：“等我！”\n'+''.join(f'她检查第{i}本书。' for i in range(35))+'最后找到了蓝色钥匙。'
        with patch.dict(os.environ,{'EXPAND_LLM_API_KEY':''}):
            sb=build_storyboard(text,target_shots=10,location='教室',time_of_day='白天')
        self.assertEqual(len(sb['shots']),10)
        self.assertNotIn('夜色',sb['expanded_text'])
        self.assertIn('蓝色钥匙',''.join(s['description'] for s in sb['shots']))
        self.assertEqual(sum('等我！' in s['dialogue'] for s in sb['shots']),1)
        with patch('modules.browser_workflow._llm_expand',return_value={'shots':[{'description':'墙上写着 {符号}'}]}) as mocked:
            v=build_storyboard('走进教室',location='教室',time_of_day='白天')
            self.assertIn('{符号}',v['shots'][0]['description'])
            self.assertEqual(mocked.call_args.args[2]['location'],'教室')
        with self.assertRaises(ValueError):parse_model_result('{"shots":[{"shot_id":1},{"shot_id":1}]}')
    def test_incremental_pack_provenance_failure_and_undo(self):
        builtin=self.root/'builtin.json';builtin.write_text('[]')
        lib=PromptLibrary(builtin,self.root/'private')
        source=self.root/'source';source.mkdir()
        (source/'prompt.md').write_text('# 一\n你是一位编导。输出格式必须保持角色一致性。'+('文档内容'*15)+'\n# 二\n三视图与服装版本。'+('角色内容'*15),encoding='utf8')
        first=lib.import_folder(str(source))
        self.assertEqual(first['entries'],3)
        again=lib.import_folder(str(source));self.assertEqual(again['reused_files'],1)
        self.assertEqual(again['parsed_files'],0)
        self.assertTrue(all('sha256' in s and 'location' in s for e in lib.all_entries() for s in e['sources']))
        other=self.root/'other';other.mkdir();(other/'p.txt').write_text('另一个资料包',encoding='utf8')
        lib.import_folder(str(other));self.assertEqual(len(lib.catalog['packages']),2)
        lib.undo_import();self.assertEqual(len(lib.catalog['packages']),1)
        (source/'prompt.md').write_bytes(b'\xff\xff\xff')
        result=lib.import_folder(str(source));self.assertEqual(result['entries'],3)
        self.assertEqual(len(result['issues']),1)
        item=lib.all_entries()[0]
        personal=lib.save_personal({'source_id':item['id'],'text':'改写版','stage':'角色','evaluation':'待实际生成'})
        restored=PromptLibrary(builtin,self.root/'private')
        self.assertEqual(restored.detail(personal['id'])['evaluation'],'待实际生成')
    def test_content_suggestions_distinguish_mixed_rows_and_generic_instructions(self):
        # Original fictional fixtures, never downloaded source examples.
        self.assertEqual(content_labels('测试','你是一位材料清点员。输出格式：数量。','test.txt','扩展')['kind'],'完整指令')
        self.assertEqual(content_labels('测试','镜头：平移\n动作：举起雨伞\n风格：暖色','test.csv','风格')['kind'],'组合条目')
        self.assertNotEqual(content_labels('测试','将长句替换为短句的写作说明。','test.txt','扩展')['kind'],'可编辑模板')

@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg integration requires both binaries')
class MediaTests(unittest.TestCase):
    setUp = ProjectTests.setUp
    def make_media(self, folder, name, audio=False, width=96, height=64, fps=12):
        args=['ffmpeg','-v','error','-y','-f','lavfi','-i',f'color=c=blue:s={width}x{height}:r={fps}']
        if audio:args+=['-f','lavfi','-i','sine=frequency=440:sample_rate=48000']
        args+=['-t','0.5','-c:v','libx264','-pix_fmt','yuv420p']
        if audio:args+=['-c:a','aac']
        run(args+[str(folder/name)])
    def test_audio_silent_mixed_spec_snapshot_and_cues(self):
        r=self.store.create(story(2));folder=self.store.folder(r['project_id'])
        self.make_media(folder,'1.mp4');self.make_media(folder,'2.mp4',audio=True,width=64,height=96,fps=24)
        sb=copy.deepcopy(r['storyboard']);sb['shots'][0]['dialogue']='保存后的对白';
        sb['shots'][0]['subtitle_cues']=[{'start':0,'end':.4,'text':'保存后的对白'}]
        sb['shots'][1]['subtitle_cues']=[{'start':0,'end':.4,'text':'第二镜旁白与对白'}]
        r=self.store.save(r['project_id'],sb)
        task=folder/'exports'/'first';snap=snapshot(r,folder,task)
        self.store.save(r['project_id'],story(2))
        final=export(snap,task,{'width':160,'height':90,'fps':30})
        info=probe(final,True)
        self.assertEqual((info['width'],info['height'],info['audio']),(160,90,True))
        self.assertAlmostEqual(info['duration'],1,delta=.15)
        self.assertIn('保存后的对白',(task/'subtitles.srt').read_text(encoding='utf8'))
        self.assertEqual(json.loads((task/'export.json').read_text(encoding='utf8'))['project_revision'],2)
        self.assertEqual(json.loads(snap.read_text(encoding='utf8'))['project']['storyboard'],sb)
    def test_failure_never_returns_or_changes_old_final_and_isolated_projects(self):
        outputs=[]
        for _ in range(2):
            r=self.store.create(story(1));folder=self.store.folder(r['project_id']);self.make_media(folder,'1.mp4')
            task=folder/'exports'/'ok';snap=snapshot(r,folder,task)
            outputs.append(export(snap,task,{'width':96,'height':64,'fps':12}))
        self.assertNotEqual(outputs[0],outputs[1])
        digest=hashlib.sha256(Path(outputs[0]).read_bytes()).hexdigest()
        r=self.store.load(r['project_id']);task=folder/'exports'/'bad';snap=snapshot(r,folder,task)
        with patch('modules.media_export.run',side_effect=RuntimeError('forced FFmpeg failure')):
            with self.assertRaises(RuntimeError):export(snap,task)
        self.assertFalse((task/'final.mp4').exists())
        self.assertEqual(hashlib.sha256(Path(outputs[0]).read_bytes()).hexdigest(),digest)
        (folder/'1.mp4').write_bytes(b'corrupt')
        with self.assertRaises(RuntimeError):probe(folder/'1.mp4')

class SecurityAndPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        with patch.dict(os.environ, {'AIMANJU_OUTPUT_DIR': str(self.root/'output'),
                                    'AIMANJU_PRIVATE_DATA_DIR': str(self.root/'private')}):
            self.module=importlib.import_module('workbench_server')
        self.names=['STORE','STATE_PATH','TASK_PATH','OUTPUT_DIR','OUTPUT_ROOT','SESSION','COMPOSE_TASKS','QUEUES']
        self.saved={n:getattr(self.module,n) for n in self.names}
        self.module.OUTPUT_ROOT=str(self.root);self.module.OUTPUT_DIR=str(self.root/'browser')
        self.module.STATE_PATH=str(self.root/'state.json');self.module.TASK_PATH=str(self.root/'tasks.json')
        self.module.STORE=ProjectStore(self.root/'browser');self.module.SESSION={'storyboard':None,'project_id':''}
        self.module.COMPOSE_TASKS={};self.module.QUEUES={}
        self.server=ThreadingHTTPServer(('127.0.0.1',0),self.module.WorkbenchHandler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.url=f'http://127.0.0.1:{self.server.server_address[1]}'
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        for n,v in self.saved.items():setattr(self.module,n,v)
        self.temp.cleanup()
    def request(self,path,data=None,headers=None):
        h={'Content-Type':'application/json','X-Workbench-Session':self.module.SESSION_KEY,**(headers or {})}
        req=Request(self.url+path,data=None if data is None else json.dumps(data).encode(),headers=h)
        try:r=urlopen(req,timeout=20)
        except HTTPError as r:return r.code,json.load(r)
        with r:return r.status,json.load(r)
    def test_every_write_rejects_origin_host_type_and_session(self):
        for endpoint in ('reset','save-storyboard','state','create-project','compose','open-file','open-folder','bind-assets','select-project'):
            self.assertEqual(self.request('/api/'+endpoint,{}, {'Origin':'https://external.example'})[0],403)
            self.assertEqual(self.request('/api/'+endpoint,{}, {'X-Workbench-Session':''})[0],403)
        self.assertEqual(self.request('/api/reset',{}, {'Host':'rebinding.example'})[0],403)
        self.assertEqual(self.request('/api/reset',{}, {'Content-Type':'text/plain'})[0],400)
        self.request('/api/create-project',{'storyboard':story()})
        forbidden=self.root/'bad.exe';forbidden.write_bytes(b'not executable')
        with patch.object(os,'startfile',create=True) as opened:
            self.assertEqual(self.request('/api/open-file',{'path':str(forbidden)})[0],400)
            self.assertEqual(self.request('/api/open-file',{'path':str(self.root.parent/'out.mp4')})[0],400)
            opened.assert_not_called()
    def test_api_save_refresh_reopen_no_stale_state_overwrite(self):
        _,a=self.request('/api/create-project',{'storyboard':story()})
        sb=story();sb['shots'][0].update(prompt='已编辑',dialogue='已编辑对白')
        self.assertEqual(self.request('/api/save-storyboard',{'project_id':a['project_id'],'storyboard':sb})[0],200)
        self.request('/api/state',{'state':{'storyboard':story()}})
        state=self.request('/api/state')[1]['state']
        self.assertEqual(state['storyboard']['shots'][0]['dialogue'],'已编辑对白')
        self.request('/api/create-project',{'storyboard':story()})
        self.assertEqual(self.request('/api/save-storyboard',{'project_id':a['project_id'],'storyboard':story()})[0],400)
        self.request('/api/select-project',{'project_id':a['project_id']})
        self.module.load_session_state()
        self.assertEqual(self.request('/api/state')[1]['state']['storyboard']['shots'][0]['prompt'],'已编辑')
    def test_restart_marks_unfinished_tasks_interrupted(self):
        self.request('/api/create-project',{'storyboard':story()})
        atomic_json(self.module.TASK_PATH,{'test':{'status':'running','step':'unfinished'}})
        self.module.load_session_state()
        self.assertEqual(self.module.COMPOSE_TASKS['test']['status'],'interrupted')
    def test_single_project_export_queue_is_ordered(self):
        order=[];started=threading.Event();release=threading.Event()
        def fake(snap,folder,settings,report):
            order.append(str(folder))
            if str(folder)=='one':started.set();release.wait(5)
            return str(self.root/'fake.mp4')
        self.module.COMPOSE_TASKS.update({i:{'status':'queued'} for i in ('one','two')})
        with patch.object(self.module,'export',side_effect=fake):
            with self.module.STATE_LOCK:
                self.module.queue_export('same','one',None,'one',{})
                self.module.queue_export('same','two',None,'two',{})
            self.assertTrue(started.wait(5));self.assertEqual(order,['one']);release.set()
            self.module.QUEUES['same'].join()
            self.assertEqual(order,['one','two'])

if __name__=='__main__':unittest.main()
