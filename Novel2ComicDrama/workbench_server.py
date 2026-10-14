"""Loopback-only Python/HTML workbench, atomic projects and checked exports."""
import copy
import json
import os
from pathlib import Path
import queue
import secrets
import socket
import sys
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(ROOT, 'workbench_ui')
OUTPUT_ROOT = os.environ.get('AIMANJU_OUTPUT_DIR', os.path.join(ROOT, 'output'))
OUTPUT_DIR = os.path.join(OUTPUT_ROOT, 'browser')
VIDEOS_DIR = os.path.join(OUTPUT_ROOT, 'browser_videos')
STORYBOARD_PATH = os.path.join(OUTPUT_DIR, 'storyboard.json')
STATE_PATH = os.path.join(OUTPUT_DIR, 'session_state.json')
TASK_PATH = os.path.join(OUTPUT_DIR, 'tasks.json')
SESSION_KEY = secrets.token_urlsafe(32)
STATE_LOCK = threading.RLock()
SESSION = {'storyboard': None, 'project_id': '', 'project_folder': '', 'project_opened': False,
           'inputs': {}, 'profile': {}, 'interview': {'answers': {}, 'done': False}}
COMPOSE_TASKS = {}
QUEUES = {}
sys.path.insert(0, ROOT)
from modules.prompt_library import PromptLibrary
from modules.browser_workflow import build_storyboard, save_storyboard
from modules.project_store import ProjectStore, atomic_json, bounded, validate_storyboard
from modules.media_export import scan, snapshot, export, OPEN_TYPES
from modules.model_service import ModelService
from modules.curated_prompts import adapt
from modules.creative import build

STORE = ProjectStore(OUTPUT_DIR)
PROMPTS = PromptLibrary(os.path.join(ROOT, 'data', 'prompts', 'builtin.json'),
                       os.environ.get('AIMANJU_PRIVATE_DATA_DIR', os.path.join(ROOT, 'data', 'private')))
MODELS = ModelService(PROMPTS.private_dir)

def resolve_project_path(value):
    return os.path.realpath(value if os.path.isabs(value) else os.path.join(ROOT, value)) if value else ''

def project_relative(path):
    try:
        return os.path.relpath(os.path.realpath(path), ROOT).replace('\\', '/')
    except ValueError:
        return os.path.realpath(path).replace('\\', '/')

def apply_record(record):
    SESSION.update({k: copy.deepcopy(record[k]) for k in ('storyboard', 'inputs', 'profile', 'interview')})
    SESSION.update(project_id=record['project_id'], revision=record['revision'],
                   project_folder=project_relative(STORE.media_folder(record)), bindings=record['bindings'])

def save_session_state():
    atomic_json(STATE_PATH, SESSION)

def load_session_state():
    with STATE_LOCK:
        if Path(STATE_PATH).exists():
            SESSION.update(json.loads(Path(STATE_PATH).read_text(encoding='utf-8')))
            if SESSION.get('project_id'):
                apply_record(STORE.load(SESSION['project_id']))
        if Path(TASK_PATH).exists():
            COMPOSE_TASKS.update(json.loads(Path(TASK_PATH).read_text(encoding='utf-8')))
            changed = False
            for task in COMPOSE_TASKS.values():
                if task['status'] in ('queued', 'running'):
                    task.update(status='interrupted', step='服务重启，任务已中断；可重新导出')
                    changed = True
            if changed:
                atomic_json(TASK_PATH, COMPOSE_TASKS)

load_session_state()

def find_free_port(start=8765, tries=10):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]

def task_update(tid, **fields):
    with STATE_LOCK:
        COMPOSE_TASKS[tid].update(fields)
        atomic_json(TASK_PATH, COMPOSE_TASKS)

def queue_export(pid, tid, snapshot_path, folder, settings):
    if pid not in QUEUES:
        jobs = QUEUES[pid] = queue.Queue()
        def worker():
            while True:
                task_id, snap, task_folder, opts = jobs.get()
                try:
                    task_update(task_id, status='running', step='开始合成')
                    final = export(snap, task_folder, opts,
                                   lambda step, percent: task_update(task_id, step=step, progress=percent))
                    task_update(task_id, status='done', progress=100, step='合成完成',
                                final_path=project_relative(final))
                except Exception as exc:
                    task_update(task_id, status='error', step='合成失败', error=str(exc))
                finally:
                    jobs.task_done()
        threading.Thread(target=worker, daemon=True).start()
    QUEUES[pid].put((tid, snapshot_path, folder, copy.deepcopy(settings)))

class WorkbenchHandler(BaseHTTPRequestHandler):
    server_version = 'Novel2ComicDrama/0.8'
    def log_message(self, *_):
        pass
    def _send_json(self, code, payload):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode('utf-8'), 'application/json; charset=utf-8')
    def _send(self, code, body, content_type):
        self.send_response(code)
        for k, v in [('Content-Type', content_type), ('Content-Length', str(len(body))),
                     ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff'),
                     ('Content-Security-Policy', "frame-ancestors 'none'; object-src 'none'; base-uri 'self'")]:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)
    def _guard(self, write=False):
        host = self.headers.get('Host', '')
        port = self.server.server_address[1]
        if host not in (f'127.0.0.1:{port}', f'localhost:{port}'):
            raise PermissionError('请通过本机地址访问工作台')
        origin = self.headers.get('Origin')
        if (origin and origin != 'http://' + host) or self.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise PermissionError('请在本地工作台操作')
        if write:
            if not secrets.compare_digest(self.headers.get('X-Workbench-Session', ''), SESSION_KEY):
                raise PermissionError('会话已变化，请刷新工作台')
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                raise ValueError('请求必须使用 application/json')
    def _read_json(self):
        length = int(self.headers.get('Content-Length', 0))
        if not 0 < length <= 4 * 1024 * 1024:
            raise ValueError('请求大小不合法')
        body = self.rfile.read(length)
        self._body_consumed = True
        value = json.loads(body.decode('utf-8'))
        if not isinstance(value, dict):
            raise ValueError('请求必须是 JSON 对象')
        return value
    def _discard_rejected_body(self):
        # Closing Windows sockets with unread POST data can reset the connection
        # before the browser receives the rejection. Drain bounded bodies only.
        if getattr(self, '_body_consumed', False):
            return
        try:
            length = int(self.headers.get('Content-Length', 0))
            if 0 < length <= 4 * 1024 * 1024:
                self.connection.settimeout(1)
                self.rfile.read(length)
        except (OSError, ValueError):
            pass
    def _current(self, data=None):
        pid = (data or {}).get('project_id', SESSION.get('project_id'))
        if not pid:
            raise ValueError('请先创建或打开项目')
        if pid != SESSION.get('project_id'):
            raise ValueError('当前项目已变化，请重新打开项目')
        return STORE.load(pid)
    def do_GET(self):
        try:
            self._guard()
            path = urlparse(self.path).path
            params = parse_qs(urlparse(self.path).query)
            if path in ('/', '/index.html'):
                body = Path(UI_DIR, 'index.html').read_text(encoding='utf-8')
                body = body.replace('</head>', '<meta name="workbench-session" content="' + SESSION_KEY + '"></head>')
                return self._send(200, body.encode('utf-8'), 'text/html; charset=utf-8')
            if path.startswith('/assets/'):
                name = path.rsplit('/', 1)[1]
                if name not in ('prompt-library.js', 'prompt-library.css', 'upgrade.js', 'upgrade.css', 'experience.js', 'experience.css'):
                    raise ValueError('资源不存在')
                return self._send(200, Path(UI_DIR, name).read_bytes(),
                                  'text/javascript; charset=utf-8' if name.endswith('.js') else 'text/css; charset=utf-8')
            if path == '/favicon.ico':
                return self._send(204, b'', 'image/x-icon')
            if path == '/api/health':
                result = {'ok': True, 'version': '0.8.0'}
            elif path in ('/api/prompts', '/api/prompts/detail'):
                result = PROMPTS.detail(params.get('id', [''])[0]) if path.endswith('/detail') else PROMPTS.query(params)
            elif path == '/api/model-status':
                result = MODELS.status()
            elif path == '/api/projects':
                result = {'projects': STORE.listing()}
            elif path == '/api/project-history':
                r = self._current()
                revisions = STORE.folder(r['project_id']) / 'revisions'
                result = {'revisions': sorted([int(p.stem) for p in revisions.glob('*.json')], reverse=True),
                          'revision': r['revision']}
            elif path == '/api/state':
                with STATE_LOCK:
                    if SESSION.get('project_id'):
                        apply_record(STORE.load(SESSION['project_id']))
                    result = {'state': copy.deepcopy(SESSION), 'tasks': copy.deepcopy(COMPOSE_TASKS)}
            elif path == '/api/videos':
                if not SESSION.get('project_id'):
                    result = {'videos': [], 'count': 0, 'videos_dir': '', 'need_project': True, 'issues': []}
                else:
                    r = self._current()
                    folder = STORE.media_folder(r)
                    result = scan(r, folder, params.get('probe') == ['1'])
                    result['videos_dir'] = project_relative(folder)
            elif path == '/api/tasks':
                result = {'tasks': list(copy.deepcopy(COMPOSE_TASKS).values())}
            elif path == '/api/compose-progress':
                result = COMPOSE_TASKS.get(params.get('task_id', [''])[0])
                if result is None:
                    return self._send_json(404, {'error': '任务不存在'})
            elif path == '/api/prompts/import-progress':
                result = PROMPTS.import_status()
            else:
                return self._send_json(404, {'error': '接口不存在'})
            self._send_json(200, result)
        except PermissionError as exc:
            self._send_json(403, {'error': str(exc)})
        except (ValueError, KeyError, OSError) as exc:
            self._send_json(400, {'error': str(exc)})
    def do_POST(self):
        self._body_consumed = False
        try:
            self._guard(write=True)
            path = urlparse(self.path).path
            data = self._read_json()
            if path == '/api/prompts/adapt':
                with STATE_LOCK:
                    result = adapt(PROMPTS.detail(data.get('id', '')), copy.deepcopy(SESSION.get('storyboard') or {}),
                                   data.get('shot_id'), SESSION.get('inputs', {}), data.get('aspect', '9:16'))
                return self._send_json(200, result)
            if path == '/api/model-settings':
                return self._send_json(200, MODELS.save(data))
            if path == '/api/model-list':
                return self._send_json(200, MODELS.fetch_models())
            if path == '/api/model-test':
                return self._send_json(200, MODELS.test_request())
            if path == '/api/model-request':
                return self._send_json(200, {'request': MODELS.prepare(data, data.get('director_instruction', ''))})
            if path == '/api/expand':
                # Never hold STATE_LOCK over network IO; status stays readable.
                args = {k: data[k] for k in ('text', 'style', 'min_sec', 'max_sec', 'target_shots',
                        'subject', 'character_desc', 'location', 'time_of_day', 'mood', 'creative_point', 'key_shot') if k in data}
                mode = data.get('generation_mode', 'local')
                if mode not in ('api', 'local'):
                    raise ValueError('请选择真实模型调用或本地规则分段；手动结果从高级入口导入')
                # Validate constraints before any paid or external request.
                draft = build(**args, llm_result=None)
                with STATE_LOCK:
                    captured = json.dumps(SESSION, sort_keys=True, ensure_ascii=False)
                llm = MODELS.generate(data, data.get('director_instruction', '')) if mode == 'api' else None
                try:
                    sb = build(**args, llm_result=llm) if llm is not None else draft
                    info = {'mode': mode, 'model': MODELS.status()['settings']['model'] if mode == 'api' else '',
                            'message': '真实模型返回；内容效果待审' if mode == 'api' else '本地规则分段，未调用模型'}
                    sb['generation_info'] = info
                    with STATE_LOCK:
                        if captured != json.dumps(SESSION, sort_keys=True, ensure_ascii=False):
                            raise ValueError('等待期间项目已变化，请在原项目重新生成；当前内容已保留')
                        SESSION.update(storyboard=sb, project_id='', project_folder='', project_opened=False,
                                       inputs=data, profile=data.get('profile', {}), interview={'answers': {k: data[k] for k in
                                       ('location', 'time_of_day', 'mood', 'creative_point', 'key_shot') if data.get(k)}, 'done': False})
                        SESSION.pop('revision', None)
                        SESSION.pop('bindings', None)
                        save_session_state()
                    if mode == 'api':
                        MODELS.finish(mode, message=info['message'])
                    return self._send_json(200, {'storyboard': sb, 'storyboard_path': '', 'generation': info})
                except Exception:
                    if mode == 'api':
                        MODELS.finish(mode, False, '生成未保存：结果或当前项目发生变化，旧内容已保留')
                    raise
            with STATE_LOCK:
                if path == '/api/prompts/import':
                    result = PROMPTS.start_import(str(data.get('folder', '')))
                elif path == '/api/prompts/favorites':
                    result = PROMPTS.save_favorites(data.get('favorites', []))
                elif path == '/api/prompts/personal':
                    result = PROMPTS.save_personal(data)
                elif path == '/api/prompts/undo-import':
                    result = PROMPTS.undo_import()
                elif path == '/api/save-storyboard':
                    sb = validate_storyboard(data.get('storyboard', data))
                    if SESSION.get('project_id'):
                        record = self._current(data)
                        apply_record(STORE.save(record['project_id'], storyboard=sb, expected=data.get('revision')))
                        dest = STORE.folder(record['project_id'])
                    else:
                        SESSION['storyboard'] = sb
                        dest = Path(OUTPUT_DIR)
                        save_storyboard(sb, str(dest))
                    save_session_state()
                    result = {'storyboard_path': project_relative(dest / 'storyboard.json'),
                              'revision': SESSION.get('revision'), 'storyboard': SESSION['storyboard']}
                elif path == '/api/state':
                    state = data.get('state', data)
                    if not isinstance(state, dict):
                        raise ValueError('state 必须为对象')
                    if data.get('project_id') and data['project_id'] != SESSION.get('project_id'):
                        raise ValueError('当前项目已变化，请重新打开项目')
                    fields = {k: state[k] for k in ('inputs', 'profile', 'interview') if k in state}
                    if SESSION.get('project_id'):
                        record = self._current(data)
                        apply_record(STORE.save(record['project_id'], **fields))
                    else:
                        SESSION.update(fields)
                    save_session_state()
                    result = {'ok': True, 'revision': SESSION.get('revision')}
                elif path == '/api/reset':
                    SESSION.clear()
                    SESSION.update(storyboard=None, project_id='', project_folder='', project_opened=False,
                                   inputs={}, profile={}, interview={'answers': {}, 'done': False})
                    save_session_state()
                    result = {'ok': True}
                elif path == '/api/create-project':
                    record = STORE.create(data.get('storyboard') or SESSION.get('storyboard'),
                                          inputs=SESSION.get('inputs'), profile=SESSION.get('profile'),
                                          interview=SESSION.get('interview'))
                    apply_record(record)
                    SESSION['project_opened'] = False
                    save_session_state()
                    result = {'project_id': record['project_id'], 'revision': record['revision'],
                              'project_folder': SESSION['project_folder']}
                elif path == '/api/select-project':
                    record = STORE.migrate(Path(OUTPUT_DIR) / data['legacy_folder']) if data.get('legacy_folder') else STORE.load(data['project_id'])
                    apply_record(record)
                    SESSION['project_opened'] = False
                    save_session_state()
                    result = {'state': copy.deepcopy(SESSION)}
                elif path == '/api/restore-project':
                    record = self._current(data)
                    apply_record(STORE.restore(record['project_id'], data['revision']))
                    save_session_state()
                    result = {'state': copy.deepcopy(SESSION)}
                elif path == '/api/bind-assets':
                    record = self._current(data)
                    candidate = {**record, 'bindings': data['bindings']}
                    inventory = scan(candidate, STORE.media_folder(record))
                    if inventory['issues']:
                        raise ValueError('；'.join(i['reason'] for i in inventory['issues']))
                    apply_record(STORE.save(record['project_id'], bindings=inventory['bindings']))
                    save_session_state()
                    result = inventory
                elif path in ('/api/open-folder', '/api/open-file'):
                    record = self._current(data)
                    if path.endswith('folder'):
                        folder = STORE.media_folder(record)
                        requested = data.get('folder')
                        if requested and Path(resolve_project_path(requested)) != folder:
                            raise ValueError('只能打开当前项目目录')
                        os.startfile(str(folder))
                        SESSION['project_opened'] = True
                        save_session_state()
                    else:
                        target = bounded(resolve_project_path(data.get('path', '')), OUTPUT_ROOT)
                        if not target.is_file() or target.suffix.lower() not in OPEN_TYPES:
                            raise ValueError('只允许打开工作台媒体文件或字幕')
                        os.startfile(str(target))
                    result = {'ok': True}
                elif path == '/api/compose':
                    record = self._current(data)
                    tid = 'compose_' + uuid.uuid4().hex
                    folder = STORE.folder(record['project_id']) / 'exports' / tid
                    snap = snapshot(record, STORE.media_folder(record), folder, data.get('bgm_path', ''))
                    COMPOSE_TASKS[tid] = {'task_id': tid, 'project_id': record['project_id'],
                                         'revision': record['revision'], 'status': 'queued', 'progress': 0, 'step': '排队中'}
                    atomic_json(TASK_PATH, COMPOSE_TASKS)
                    queue_export(record['project_id'], tid, snap, folder, data)
                    result = {'task_id': tid}
                else:
                    return self._send_json(404, {'error': '接口不存在'})
            self._send_json(200, result)
        except PermissionError as exc:
            self._discard_rejected_body()
            self._send_json(403, {'error': str(exc)})
        except (ValueError, KeyError, TypeError) as exc:
            self._discard_rejected_body()
            self._send_json(400, {'error': str(exc)})
        except Exception as exc:
            self._send_json(500, {'error': str(exc)})

def main():
    from modules.media_export import run
    for name in ('ffmpeg', 'ffprobe'):
        run([name, '-version'], timeout=15)
    server = ThreadingHTTPServer(('127.0.0.1', 0), WorkbenchHandler)
    url = f'http://127.0.0.1:{server.server_address[1]}/'
    threading.Timer(.8, lambda: webbrowser.open(url)).start()
    print('AI漫剧工作台 0.7.0 已启动：' + url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()

if __name__ == '__main__':
    main()
