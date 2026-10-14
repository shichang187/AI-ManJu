"""Explicit model calls, file-based credentials and safe, inspectable status."""
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
        self.settings = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {
            'base_url': os.getenv('EXPAND_LLM_BASE_URL', ''),
            'model': os.getenv('EXPAND_LLM_MODEL', ''), 'key_file': '', 'timeout': 60}

    def _key(self, settings):
        if settings.get('key_file'):
            try:
                path = Path(settings['key_file'])
                if not path.is_absolute() or path.stat().st_size > 16384:
                    return ''
                key = path.read_text(encoding='utf-8-sig').strip()
            except (OSError, UnicodeError):
                return ''
        else:
            # Compatibility for existing installations; new credentials use a file.
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

    def status(self):
        with self.lock:
            settings = copy.deepcopy(self.settings)
            try:
                self._validate(settings)
                ready = bool(self._key(settings))
                reason = '配置完整；接口可用性、额度和模型效果未验证。' if ready else '凭据文件不可读或为空；请在本机配置。'
            except (ValueError, TypeError):
                ready, reason = False, '尚未配置完整接口地址和模型 ID。'
            return {'settings': settings, 'ready': ready, 'message': reason,
                    'credential_present': bool(self._key(settings)), 'call': copy.deepcopy(self.call),
                    'material': '素材生成使用外部平台页面：复制镜头提示词、生成并下载到当前项目。未接入素材生成 API，实际效果未验证。'}

    def save(self, data):
        # Never accept or return a raw credential in an HTTP payload.
        if set(data) - {'base_url', 'model', 'key_file', 'timeout', 'project_id'}:
            raise ValueError('只接受接口地址、模型 ID、凭据文件路径和等待时限')
        settings = {k: str(data.get(k, '')).strip() for k in ('base_url', 'model', 'key_file')}
        settings['timeout'] = int(data.get('timeout', 60))
        self._validate(settings)
        with self.lock:
            if self.call['status'] == 'running':
                raise ValueError('模型调用中，请完成后再修改配置')
            atomic_json(self.path, settings)
            self.settings = settings
            self.call = {'status': 'idle', 'message': '配置已保存；尚未验证接口。'}
            atomic_json(self.call_path, self.call)
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
            if self.call['status'] == 'running':
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
            data = json.loads(raw)
            choice = data['choices'][0]
            if choice.get('finish_reason') in ('length', 'content_filter'):
                raise ValueError('模型输出被截断或过滤，请减少镜头数或调整故事')
            body = choice['message']['content']
            body = re.sub(r'^```(?:json)?\s*|\s*```$', '', body.strip())
            result = validate_storyboard(json.loads(body))
        except urllib.error.HTTPError as exc:
            reasons = {401: '认证失败，请核对本地凭据文件', 403: '模型或账号无调用权限',
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
