"""Explicit provider setup, user-encrypted credentials and safe call status."""
import copy
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .credential_store import CredentialStore
from .project_store import atomic_json, validate_storyboard


DEFAULT_INSTRUCTION = '把故事扩写为可拍摄的漫剧分镜，保留人物、事件顺序、对白归属和结尾，保持场景与角色一致。'


class ModelService:
    def __init__(self, private_dir):
        self.path = Path(private_dir) / 'model-settings.json'
        self.call_path = Path(private_dir) / 'model-call.json'
        self.lock = threading.RLock()
        self.call = {'status': 'idle', 'message': '尚未调用模型；模型效果未验证。'}
        if self.call_path.exists():
            self.call = json.loads(self.call_path.read_text(encoding='utf-8'))
            if self.call['status'] == 'running':
                self.call.update(status='error', message='上次模型调用因服务重启中断，请重新生成；内容效果未验证。')
        self.credentials = CredentialStore(private_dir)
        self.fetching = False
        self.lists = {}
        stored = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        self.profiles = stored.get('profiles', {})
        self.settings = stored.get('active', stored) or {
            'provider': 'siliconflow', 'base_url': 'https://api.siliconflow.cn/v1',
            'model': '', 'key_file': '', 'timeout': 60, 'credential_mode': 'saved'}
        if not stored and os.getenv('EXPAND_LLM_BASE_URL'):
            self.settings.update(provider='custom', base_url=os.getenv('EXPAND_LLM_BASE_URL'),
                model=os.getenv('EXPAND_LLM_MODEL', ''), credential_mode='legacy')
        self.settings.setdefault('provider', 'custom')
        self.settings.setdefault('credential_mode', 'legacy')
        self.profiles[self.credentials.ref(self.settings['provider'], self.settings['base_url'])] = copy.deepcopy(self.settings)

    def _legacy_key(self, settings):
        if settings.get('key_file'):
            try:
                path = Path(settings['key_file'])
                if not path.is_absolute() or path.stat().st_size > 16384:
                    return ''
                key = path.read_text(encoding='utf-8-sig').strip()
            except (OSError, UnicodeError):
                return ''
        else:
            # Compatibility only when legacy mode is explicitly selected.
            key = os.getenv('EXPAND_LLM_API_KEY', '').strip()
        return key if key and not any(c.isspace() for c in key) else ''

    def _validate(self, settings):
        endpoint = urlsplit(settings.get('base_url', ''))
        if (endpoint.scheme not in ('https', 'http') or not endpoint.hostname or
                endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
            raise ValueError('填写不含凭据、查询参数的完整接口地址，例如 https://服务商域名/v1')
        if endpoint.scheme == 'http' and endpoint.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('外部模型接口必须使用 HTTPS；本地接口可使用 HTTP')
        if not settings.get('model', '').strip():
            raise ValueError('请填写服务商提供的模型 ID，不根据账号名称猜测')
        if not 5 <= int(settings.get('timeout', 60)) <= 180:
            raise ValueError('等待时限应为 5–180 秒')
        if settings.get('key_file') and not Path(settings['key_file']).is_absolute():
            raise ValueError('凭据文件需要完整路径')

    def _validate_endpoint(self, settings):
        endpoint = urlsplit(settings.get('base_url', ''))
        if (endpoint.scheme not in ('https', 'http') or not endpoint.hostname or
                endpoint.username or endpoint.password or endpoint.query or endpoint.fragment):
            raise ValueError('填写不含凭据、查询参数的完整接口地址')
        if endpoint.scheme == 'http' and endpoint.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('外部模型接口必须使用 HTTPS；本地接口可使用 HTTP')
        if not 5 <= int(settings.get('timeout', 60)) <= 180:
            raise ValueError('等待时限应为 5–180 秒')
        if settings.get('key_file') and not Path(settings['key_file']).is_absolute():
            raise ValueError('凭据文件需要完整路径')

    def _key(self, settings):
        if settings.get('credential_mode') == 'saved':
            return self.credentials.get(self.credentials.ref(settings['provider'], settings['base_url']))
        return self._legacy_key(settings)

    def status(self):
        with self.lock:
            settings = copy.deepcopy(self.settings)
            try:
                key = self._key(settings)
                present = bool(key)
                self._validate(settings)
                ready = present
                reason = ('配置完整；接口可用性、额度和模型效果未验证。' if ready else
                          '尚未填写 API Key；请在这里安全保存，或在高级入口配置旧凭据。')
            except (ValueError, TypeError):
                ready = False
                try:
                    present = bool(self._key(settings))
                    reason = '已本地保存 API Key；请获取模型并选择模型 ID。' if present else '尚未填写 API Key 或模型 ID。'
                except ValueError:
                    present = False
                    reason = '安全凭据无法解密，请重新输入 API Key。'
            public = {k: settings.get(k, '') for k in ('provider', 'base_url', 'model', 'key_file', 'timeout', 'credential_mode')}
            scope = self.credentials.ref(settings['provider'], settings['base_url'])
            listing = copy.deepcopy(self.lists.get(scope, {'status': 'idle', 'models': [], 'message': '尚未获取模型列表'}))
            return {'settings': public, 'ready': ready, 'has_key': present, 'credential_present': present,
                    'credential_storage': 'Windows 当前用户加密' if self.credentials.persistent else '仅本次服务会话',
                    'message': reason, 'model_list': listing, 'call': copy.deepcopy(self.call),
                    'material': '文本模型与 Seedance 素材平台分别使用；复制提示词到豆包/即梦生成。实际效果未验证。'}

    def save(self, data):
        allowed = {'provider', 'base_url', 'model', 'key_file', 'timeout', 'project_id',
                   'api_key', 'key_action', 'credential_mode'}
        if set(data) - allowed:
            raise ValueError('模型设置包含不支持的字段')
        provider = data.get('provider', 'custom' if data.get('base_url') else self.settings['provider'])
        if provider not in ('siliconflow', 'custom'):
            raise ValueError('请选择硅基流动或自定义兼容接口')
        endpoint = 'https://api.siliconflow.cn/v1' if provider == 'siliconflow' else str(data.get('base_url', '')).strip().rstrip('/')
        scope = self.credentials.ref(provider, endpoint)
        with self.lock:
            if self.call['status'] == 'running' or self.fetching:
                raise ValueError('模型请求中，请完成后再修改配置')
            previous = self.profiles.get(scope, {})
            mode = data.get('credential_mode', 'legacy' if 'key_file' in data and 'provider' not in data else previous.get('credential_mode', 'saved'))
            if mode not in ('saved', 'legacy'):
                raise ValueError('凭据模式无效')
            settings = {'provider': provider, 'base_url': endpoint,
                        'model': str(data.get('model', previous.get('model', ''))).strip(),
                        'timeout': int(data.get('timeout', previous.get('timeout', 60))),
                        'key_file': str(data.get('key_file', previous.get('key_file', ''))).strip() if mode == 'legacy' else '',
                        'credential_mode': mode}
            self._validate_endpoint(settings)
            action = data.get('key_action', 'keep')
            key = '' if action == 'clear' else str(data.get('api_key', '')).strip()
            if action not in ('keep', 'replace', 'clear'):
                raise ValueError('API Key 操作无效')
            if action == 'replace' and not key:
                raise ValueError('更换 Key 时请填写新 API Key')
            if key and (len(key) < 8 or len(key) > 8192 or any(c.isspace() for c in key)):
                raise ValueError('API Key 格式不正确')
            if key and any(key in str(v) for v in settings.values()):
                raise ValueError('API Key 只能填写在专用输入框')
            if action == 'clear':
                self.credentials.clear(scope)
                settings.update(credential_mode='saved', key_file='')
                self.lists.pop(scope, None)
            elif key:
                self.credentials.put(scope, key)
                settings.update(credential_mode='saved', key_file='')
                self.lists.pop(scope, None)
            self.settings = settings
            self.profiles[scope] = copy.deepcopy(settings)
            atomic_json(self.path, {'active': settings, 'profiles': self.profiles})
            self.call = {'status': 'idle', 'message': '配置已本地保存；尚未生成分镜。'}
            atomic_json(self.call_path, self.call)
        return self.status()

    def _provider_json(self, suffix, payload=None):
        settings = copy.deepcopy(self.settings)
        key = self._key(settings)
        if not key:
            raise ValueError('请先保存 API Key')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        req = urllib.request.Request(settings['base_url'].rstrip('/') + suffix,
            data=json.dumps(payload).encode('utf-8') if payload is not None else None,
            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'},
            method='POST' if payload is not None else 'GET')
        try:
            with urllib.request.build_opener(NoRedirect).open(req, timeout=settings['timeout']) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError('响应过大')
            # Providers must never reflect an account credential into public data.
            return json.loads(raw.decode('utf-8').replace(key, '[凭据已隐藏]'))
        except urllib.error.HTTPError as exc:
            messages = {401: '认证失败', 403: '账号权限不足', 404: '接口或模型不存在',
                        429: '额度不足或限流', 301: '重定向被拒绝', 302: '重定向被拒绝',
                        307: '重定向被拒绝', 308: '重定向被拒绝'}
            raise ValueError(f"请求失败（HTTP {exc.code}）：{messages.get(exc.code, '服务商错误')}") from None
        except (TimeoutError, OSError, urllib.error.URLError):
            raise ValueError('网络连接失败或等待超时；配置已保留，可以重试') from None
        except (ValueError, UnicodeError, TypeError):
            raise ValueError('服务商响应格式无效；配置已保留') from None

    def fetch_models(self):
        with self.lock:
            if self.fetching or self.call['status'] == 'running':
                raise ValueError('已有模型请求，请等待完成')
            self._validate_endpoint(self.settings)
            scope = self.credentials.ref(self.settings['provider'], self.settings['base_url'])
            self.fetching = True
        try:
            suffix = '/models?sub_type=chat' if self.settings['provider'] == 'siliconflow' else '/models'
            result = self._provider_json(suffix)
            rows = result.get('data')
            if not isinstance(rows, list):
                raise ValueError('模型列表格式无效')
            ids = sorted(set(r['id'] for r in rows if isinstance(r, dict) and
                isinstance(r.get('id'), str) and 0 < len(r['id']) <= 200 and '[凭据已隐藏]' not in r['id'] and
                r.get('sub_type', 'chat') == 'chat' and r.get('type', 'text') == 'text' and
                not re.search(r'embedding|rerank|whisper|tts|speech|flux|stable.diffusion|seedance', r['id'], re.I)))
            self.lists[scope] = {'status': 'authenticated', 'models': ids,
                'message': '模型列表认证通过；具体模型权限、额度和分镜效果未验证。'}
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            self.lists[scope] = {'status': 'error', 'models': [], 'message': str(exc)}
        finally:
            self.fetching = False
        return self.status()

    def test_request(self):
        with self.lock:
            if self.fetching or self.call['status'] == 'running':
                raise ValueError('已有模型请求，请等待完成')
            self._validate(self.settings)
            self.call = {'status': 'running', 'mode': 'test', 'model': self.settings['model'], 'message': '正在发送测试请求…'}
        try:
            value = self._provider_json('/chat/completions', {'model': self.settings['model'],
                'messages': [{'role': 'user', 'content': '只返回 JSON：{"ok":true}。这是连接测试。'}], 'max_tokens': 64})
            choice = value['choices'][0]
            if choice.get('finish_reason') in ('length', 'content_filter') or json.loads(choice['message']['content']) != {'ok': True}:
                raise ValueError('测试响应不符合 JSON 要求')
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            return self._fail('测试响应格式无效' if isinstance(exc, (json.JSONDecodeError, KeyError, IndexError, TypeError)) else '测试失败：' + str(exc))
        self.finish('test', message='测试 JSON 请求成功；故事分镜质量未验证。')
        return self.status()

    def prepare(self, inputs, instruction=''):
        text = str(inputs.get('text', '')).strip()
        if not text:
            raise ValueError('请先输入故事内容')
        context = {k: inputs.get(k) for k in ('text', 'style', 'subject', 'character_desc',
                   'target_shots', 'min_sec', 'max_sec', 'location', 'time_of_day', 'mood',
                   'creative_point', 'key_shot', 'characters', 'scenes') if k in inputs}
        schema = ('只返回 JSON 对象，含 title、expanded_text、characters（对象数组）、scenes（对象数组）、shots（非空数组）。'
                  '每镜含唯一 shot_id、description、duration（秒）、dialogue、narration、speaker、'
                  'camera_movement、characters（角色名数组）、scene_id、prompt。镜头最多300个。'
                  '不要把资料中的示例人物当成故事人物。按完整约束生成，不省略结尾。')
        return (instruction.strip() or DEFAULT_INSTRUCTION) + '\n\n' + schema + '\n\n我的故事与约束：\n' + json.dumps(context, ensure_ascii=False)

    def generate(self, inputs, instruction=''):
        content = self.prepare(inputs, instruction)
        with self.lock:
            if self.call['status'] == 'running' or self.fetching:
                raise ValueError('模型调用已在进行中，请等待完成')
            status = self.status()
            if not status['ready']:
                self.call = {'status': 'error', 'mode': 'api', 'message': status['message']}
                atomic_json(self.call_path, self.call)
                raise ValueError(status['message'])
            settings = copy.deepcopy(self.settings)
            key = self._key(settings)
            self.call = {'status': 'running', 'mode': 'api', 'model': settings['model'],
                         'started_at': time.time(), 'message': '模型正在扩写分镜…'}
            atomic_json(self.call_path, self.call)
        try:
            request = urllib.request.Request(settings['base_url'].rstrip('/') + '/chat/completions',
                data=json.dumps({'model': settings['model'], 'messages': [{'role': 'user', 'content': content}],
                                 'temperature': .7, 'max_tokens': 8000}).encode('utf-8'),
                headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key}, method='POST')
            # Redirects must not forward credentials to another endpoint.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs):
                    return None
            with urllib.request.build_opener(NoRedirect).open(request, timeout=settings['timeout']) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError('模型响应过大，请减少镜头数')
            data = json.loads(raw.decode('utf-8').replace(key, '[凭据已隐藏]'))
            choice = data['choices'][0]
            if choice.get('finish_reason') in ('length', 'content_filter'):
                raise ValueError('模型输出被截断或过滤，请减少镜头数或调整故事')
            body = choice['message']['content']
            body = re.sub(r'^```(?:json)?\s*|\s*```$', '', body.strip())
            result = validate_storyboard(json.loads(body))
        except urllib.error.HTTPError as exc:
            reasons = {401: '认证失败，请核对当前供应商 API Key', 403: '模型或账号无调用权限',
                       404: '接口或模型 ID 不存在', 429: '额度不足或请求限流',
                       301: '接口重定向被拒绝，请填写最终接口地址', 302: '接口重定向被拒绝，请填写最终接口地址'}
            return self._fail(f"模型调用失败（HTTP {exc.code}）：{reasons.get(exc.code, '服务商返回错误，请核对服务状态')}")
        except (TimeoutError, urllib.error.URLError, OSError):
            return self._fail('模型连接失败或等待超时，请检查接口地址、网络和等待时限')
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            # Do not echo provider output, request headers or raw error bodies.
            return self._fail('模型结果无效、被截断或被过滤：需要完整的分镜 JSON；旧分镜已保留，可调整指令后重试')
        return result

    def _fail(self, message):
        with self.lock:
            self.call.update(status='error', finished_at=time.time(), message=message)
            atomic_json(self.call_path, self.call)
        raise ValueError(message)

    def finish(self, mode, success=True, message=''):
        with self.lock:
            self.call.update(status='success' if success else 'error', mode=mode,
                             finished_at=time.time(), message=message)
            atomic_json(self.call_path, self.call)
