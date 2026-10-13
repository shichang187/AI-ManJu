"""Private incremental imports, provenance and editable personal prompt versions."""
import copy
import hashlib
import json
from pathlib import Path
import re
import threading
import time
import uuid
import zipfile

from .project_store import atomic_json


def sections(text):
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if re.match(
        r'^\s*(?:#{1,6}\s+|第[一二三四五六七八九十百\d]+[章节]|[一二三四五六七八九十]+[、．.]|\d{1,3}[、．.)）]\s*)\S', line)]
    if len(starts) < 2:
        return []
    result = []
    for a, b in zip(starts, starts[1:] + [len(lines)]):
        body = '\n'.join(lines[a:b]).strip()
        if len(body) >= 40:
            result.append((lines[a].strip()[:70], body, f'文本行 {a + 1}–{b}'))
    return result


def content_labels(title, body, source, base_stage):
    # These are explainable suggestions, never a human-reviewed selection.
    if re.fullmatch(r'https?://\S+', body.strip()):
        kind, usage = '参考链接', '参考链接'
    elif any(w in body[:6000] for w in ('角色定义', '系统指令', '你是一', '输出格式', '工作流程')):
        kind, usage = '导演指令', '先给对话模型'
    elif any(w in body[:3000] for w in ('教程', '点击', '操作步骤', '安装', '使用方法')):
        kind, usage = '教程', '阅读后操作'
    elif any(w in body for w in ('{{', '【角色】', '[角色]', '替换为', '填入')):
        kind, usage = '可编辑模板', '填写后使用'
    elif any(w in body[:2000] for w in ('示例', '案例', '例如')):
        kind, usage = '示例', '参考后改写'
    elif len(body) < 700 and any(w in body for w in ('推镜', '拉镜', '运镜', '镜头移动', 'pan', 'dolly')):
        kind, usage = '运镜片段', '组合进镜头'
    elif len(body) < 700 and any(w in body for w in ('风格', '质感', '配色', 'style', '光影')):
        kind, usage = '风格片段', '组合进镜头'
    else:
        kind, usage = '资料原文', '阅读并选取'
    stage = base_stage
    rules = [('角色', ('三视图', '角色一致', '人物外貌', '服装版本')),
             ('场景', ('场景一致', '地点设定', '环境设定')),
             ('剧本', ('剧本创作', '故事大纲', '剧情结构')),
             ('分镜', ('分镜表', '故事板', '首帧', '分镜图')),
             ('运镜', ('运镜', '推镜', '拉镜')), ('风格', ('美术风格', '风格提示词'))]
    for s, words in rules:
        if any(w in body[:4000] for w in words):
            stage = s
            break
    return {'kind': kind, 'usage': usage, 'stage': stage,
            'classification_basis': '内容规则建议；可保存个人重分类', 'reviewed': False,
            'verified': False}


class CatalogImportMixin:
    def _init_import(self):
        self.import_lock = threading.Lock()
        self.job = {'status': 'idle', 'progress': 0}
        prefs = self.private_dir / 'personal.json'
        self.personal = json.loads(prefs.read_text(encoding='utf-8')) if prefs.exists() else []

    def import_status(self):
        with self.lock:
            return copy.deepcopy(self.job)

    def start_import(self, folder):
        self._check_folder(folder)
        if not self.import_lock.acquire(blocking=False):
            raise ValueError('已有资料导入任务，请等待完成')
        self.job = {'status': 'running', 'progress': 0, 'read_files': 0}
        def worker():
            try:
                report = self._import_folder(folder, already_locked=True)
                self.job = {'status': 'done', 'progress': 100, 'report': report}
            except Exception as exc:
                self.job = {'status': 'error', 'error': str(exc), 'progress': 0}
            finally:
                self.import_lock.release()
        threading.Thread(target=worker, daemon=True).start()
        return {'status': 'running'}

    def _check_folder(self, folder):
        root = Path(folder).expanduser()
        if not root.is_absolute() or not root.is_dir():
            raise ValueError('请输入已下载资料的绝对文件夹路径')
        root = root.resolve()
        if root == Path(root.anchor) or any(p.lower() in ('.git', '.aws', '.ssh', '.codex') for p in root.parts):
            raise ValueError('请选择具体的提示词资料文件夹')
        if root == self.private_dir.resolve() or root.is_relative_to(self.private_dir.resolve()):
            raise ValueError('不能导入工作台私有库自身')
        return root

    def _import_folder(self, folder, already_locked=False):
        from .prompt_library import SUPPORTED, LIMIT, read_entries, make_entry, decode_text
        root = self._check_folder(folder)
        if not already_locked and not self.import_lock.acquire(blocking=False):
            raise ValueError('已有资料导入任务')
        try:
            pack_id = hashlib.sha256(str(root).encode()).hexdigest()[:20]
            with self.lock:
                old = copy.deepcopy(self.catalog)
            packages = old.get('packages', {})
            prior = packages.get(pack_id, {}).get('files', {})
            # Keep original v1 records until their files successfully reparse.
            retained = []
            inventory, entries, issues = {}, {}, []
            paths = [p for p in sorted(root.rglob('*')) if p.is_file() and not p.is_symlink()
                     and not any(x.startswith('.') for x in p.relative_to(root).parts)]
            read_count = reused = parsed = ignored = 0
            for n, path in enumerate(paths):
                rel = path.relative_to(root).as_posix()
                if not path.resolve().is_relative_to(root):
                    issues.append({'file': rel, 'reason': '路径经过链接指向资料目录以外；未读取'})
                    continue
                self.job = {'status': 'running', 'progress': round(95 * n / max(1, len(paths))),
                            'read_files': read_count, 'total_files': len(paths)}
                item = {'file': rel, 'package_id': pack_id, 'status': 'unextracted', 'entries': []}
                inventory[rel] = item
                if '.downloading' in path.name.lower() or path.suffix.lower() in ('.part', '.tmp', '.zip', '.rar', '.7z'):
                    item['reason'] = '下载未完成或压缩包尚未解压'
                    issues.append({'file': rel, 'reason': item['reason']})
                    continue
                if path.suffix.lower() not in SUPPORTED:
                    ignored += 1
                    reason = '图片 OCR / 视频内容尚未提取' if path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.mp4') else '文件类型不支持'
                    item['reason'] = reason
                    issues.append({'file': rel, 'reason': reason})
                    continue
                try:
                    before = path.stat()
                    if before.st_size > LIMIT:
                        raise ValueError('文件超过 32 MB 限制')
                    fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()
                    item['sha256'] = fingerprint
                    item['size'] = before.st_size
                    cache = prior.get(rel)
                    if cache and cache.get('sha256') == fingerprint and cache.get('status') == 'extracted':
                        item.update(copy.deepcopy(cache))
                        reused += 1
                    else:
                        if path.suffix.lower() == '.doc':
                            derived = self.private_dir / 'extracted' / (fingerprint + '.txt')
                            if not derived.exists():
                                raise ValueError('旧 DOC 缺少匹配指纹的禁宏提取文本')
                            raw_items = [(path.stem, decode_text(derived.read_bytes()), '资料原文')]
                        else:
                            raw_items = read_entries(path)
                        if not any(t.strip() for _, t, _ in raw_items):
                            raise ValueError('没有文本层或只有图片；未提取')
                        parsed += 1
                        for index, (title, text, kind) in enumerate(raw_items):
                            if not text.strip():
                                continue
                            location = title if kind == '表格条目' else '完整原文'
                            parts = [(title, text, location)] + (sections(text) if kind != '表格条目' else [])
                            for part_title, body, locator in parts:
                                e = make_entry(part_title, body, kind, rel, root.name)
                                e.update(content_labels(part_title, body, rel, e['stage']))
                                e['sources'][0].update(package_id=pack_id, sha256=fingerprint, location=locator)
                                e['original_kind'] = kind
                                item['entries'].append(e)
                            raw_dir = self.private_dir / 'raw'
                            raw_dir.mkdir(parents=True, exist_ok=True)
                            (raw_dir / (fingerprint + f'_{index}.txt')).write_text(text, encoding='utf-8')
                        item['status'] = 'extracted'
                        components = []
                        if path.suffix.lower() == '.docx':
                            with zipfile.ZipFile(path) as archive:
                                images = sum(n.startswith('word/media/') for n in archive.namelist())
                            if images:
                                components.append(f'文档内 {images} 个媒体资源未做 OCR / 内容分析')
                        elif path.suffix.lower() == '.pdf':
                            from pypdf import PdfReader
                            pages = PdfReader(path).pages
                            missing = [i + 1 for i, p in enumerate(pages) if not (p.extract_text() or '').strip()]
                            if missing:
                                components.append('无文本页面尚未提取：' + ','.join(map(str, missing)))
                        item['unextracted_components'] = components
                    for reason in item.get('unextracted_components', []):
                        issues.append({'file': rel, 'reason': reason})
                    after = path.stat()
                    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                        raise ValueError('文件在读取期间改变，请重试')
                    read_count += 1
                except Exception as exc:
                    item.update(status='failed', reason=str(exc)[:200])
                    issues.append({'file': rel, 'reason': item['reason']})
                    if rel in prior:
                        item['entries'] = copy.deepcopy(prior[rel].get('entries', []))
            for rel, cache in prior.items():
                if rel not in inventory:
                    inventory[rel] = {**copy.deepcopy(cache), 'status': 'missing', 'reason': '来源文件已不在目录；保留旧条目'}
                    issues.append({'file': rel, 'reason': inventory[rel]['reason']})
            packages[pack_id] = {'folder': str(root), 'files': inventory}
            if old.get('folder') == str(root) and not old.get('packages'):
                retained = [e for e in old['entries'] if any(
                    s['file'] not in inventory or inventory[s['file']]['status'] != 'extracted' for s in e['sources'])]
            elif not old.get('packages'):
                retained = old.get('entries', [])
            for e in retained + [e for p in packages.values() for f in p['files'].values() for e in f['entries']]:
                if e['id'] not in entries:
                    entries[e['id']] = copy.deepcopy(e)
                else:
                    for source in e['sources']:
                        if source not in entries[e['id']]['sources']:
                            entries[e['id']]['sources'].append(source)
            report = {'read_files': read_count, 'entries': len(entries), 'ignored_media': ignored,
                      'issues': issues, 'total_files': len(paths), 'reused_files': reused, 'parsed_files': parsed,
                      'time': time.strftime('%Y-%m-%d %H:%M:%S'),
                      'primary': sum(e['stage'] != '扩展' for e in entries.values())}
            catalog = {'schema_version': 2, 'folder': str(root), 'entries': list(entries.values()),
                       'packages': packages, 'report': report}
            with self.lock:
                self.private_dir.mkdir(parents=True, exist_ok=True)
                atomic_json(self.private_dir / 'import-history' / (uuid.uuid4().hex + '.json'), self.catalog)
                atomic_json(self.private_dir / 'library.json', catalog)
                self.catalog = catalog
            return report
        finally:
            if not already_locked:
                self.import_lock.release()

    def undo_import(self):
        if not self.import_lock.acquire(blocking=False):
            raise ValueError('导入中不能撤销')
        try:
            files = sorted((self.private_dir / 'import-history').glob('*.json'), key=lambda p: p.stat().st_mtime_ns)
            if not files:
                raise ValueError('没有可撤销的导入')
            with self.lock:
                catalog = json.loads(files[-1].read_text(encoding='utf-8'))
                atomic_json(self.private_dir / 'library.json', catalog)
                self.catalog = catalog
                files[-1].rename(files[-1].with_suffix('.restored'))
                return {'ok': True, 'entries': len(catalog['entries'])}
        finally:
            self.import_lock.release()

    def save_personal(self, data):
        from .prompt_library import STAGES
        if not isinstance(data.get('text'), str) or not data['text'].strip():
            raise ValueError('个人提示词不能为空')
        if data.get('stage', '扩展') not in STAGES:
            raise ValueError('创作阶段不合法')
        original = self.detail(data['source_id']) if data.get('source_id') else None
        entry = {'id': 'personal_' + uuid.uuid4().hex, 'title': str(data.get('title', '我的提示词'))[:100],
                 'text': data['text'], 'stage': data.get('stage', '扩展'), 'kind': data.get('kind', '个人模板'),
                 'usage': data.get('usage', '填写后使用'), 'private': True,
                 'sources': original['sources'] if original else [], 'source_id': data.get('source_id'),
                 'models': data.get('models', []), 'verified': False, 'reviewed': True,
                 'evaluation': str(data.get('evaluation', '未验证')),
                 'result': str(data.get('result', '')), 'input': str(data.get('input', ''))}
        with self.lock:
            personal = self.personal + [entry]
            atomic_json(self.private_dir / 'personal.json', personal)
            self.personal = personal
        return entry
