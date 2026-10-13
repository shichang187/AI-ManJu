"""Atomic project records; legacy storyboards remain readable and exportable."""
import copy
import json
import math
import os
from pathlib import Path
import re
import threading
import uuid


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def bounded(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_relative_to(root):
        raise ValueError('路径超出项目范围')
    return path


def validate_storyboard(value):
    if not isinstance(value, dict) or not isinstance(value.get('shots'), list) or not value['shots']:
        raise ValueError('需要非空 shots 分镜数组')
    if len(value['shots']) > 300:
        raise ValueError('每个项目最多 300 镜；请拆分章节')
    value = copy.deepcopy(value)
    ids = set()
    for index, shot in enumerate(value['shots'], 1):
        if not isinstance(shot, dict):
            raise ValueError('分镜必须是对象')
        shot.setdefault('shot_id', index)
        sid = str(shot['shot_id'])
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', sid) or sid in ids:
            raise ValueError('镜头 ID 必须唯一且只含字母、数字、下划线或短横线')
        ids.add(sid)
        duration = float(shot.get('duration', 4))
        if not math.isfinite(duration) or not 0 < duration <= 600:
            raise ValueError('镜头时长必须在 0–600 秒之间')
        shot['duration'] = duration
        for field in ('description', 'dialogue', 'narration', 'speaker', 'prompt', 'camera_movement'):
            if not isinstance(shot.get(field, ''), str):
                raise ValueError(field + ' 必须是文本')
        if not isinstance(shot.get('characters', []), list) or not all(isinstance(c, str) for c in shot.get('characters', [])):
            raise ValueError('出场角色必须是文本数组')
        cues = shot.get('subtitle_cues', [])
        if not isinstance(cues, list):
            raise ValueError('句级字幕必须是数组')
        for cue in cues:
            if not isinstance(cue, dict) or not isinstance(cue.get('text'), str):
                raise ValueError('字幕需要 start、end 和 text')
            start, end = float(cue.get('start', -1)), float(cue.get('end', -1))
            if not math.isfinite(start) or not math.isfinite(end) or not 0 <= start < end:
                raise ValueError('字幕起止时间无效')
    for field in ('characters', 'scenes'):
        if not isinstance(value.get(field, []), list) or not all(isinstance(v, dict) for v in value.get(field, [])):
            raise ValueError(field + ' 档案必须是对象数组')
    return value


class ProjectStore:
    def __init__(self, browser_root):
        self.root = Path(browser_root).resolve()
        self.lock = threading.RLock()

    def folder(self, project_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', str(project_id)):
            raise ValueError('项目 ID 不合法')
        folder = bounded(self.root / project_id, self.root)
        if not (folder / 'project.json').is_file():
            raise ValueError('项目不存在')
        return folder

    def create(self, storyboard, inputs=None, profile=None, interview=None):
        with self.lock:
            pid = 'project_' + uuid.uuid4().hex
            record = {'schema_version': 2, 'project_id': pid, 'revision': 1,
                      'storyboard': validate_storyboard(storyboard), 'bindings': [],
                      'inputs': inputs or {}, 'profile': profile or {}, 'interview': interview or {}}
            self._write(self.root / pid, record)
            return record

    def _write(self, folder, record):
        # project.json is authoritative; storyboard.json only supports old tools.
        atomic_json(folder / 'project.json', record)
        atomic_json(folder / 'storyboard.json', record['storyboard'])

    def load(self, pid):
        with self.lock:
            return json.loads((self.folder(pid) / 'project.json').read_text(encoding='utf-8'))

    def save(self, pid, storyboard=None, bindings=None, expected=None, **fields):
        with self.lock:
            record = self.load(pid)
            if expected is not None and int(expected) != record['revision']:
                raise ValueError('项目已被其他页面更新，请重新打开后再保存')
            before = copy.deepcopy(record)
            if storyboard is not None:
                record['storyboard'] = validate_storyboard(storyboard)
            if bindings is not None:
                if not isinstance(bindings, list):
                    raise ValueError('素材对应关系必须是数组')
                record['bindings'] = copy.deepcopy(bindings)
            for key in ('inputs', 'profile', 'interview'):
                if key in fields:
                    if not isinstance(fields[key], dict):
                        raise ValueError(key + ' 必须是对象')
                    record[key] = copy.deepcopy(fields[key])
            if record == before:
                return record
            folder = self.folder(pid)
            atomic_json(folder / 'revisions' / (str(before['revision']) + '.json'), before)
            record['revision'] += 1
            self._write(folder, record)
            return record

    def restore(self, pid, revision):
        with self.lock:
            folder = self.folder(pid)
            if not str(revision).isdigit():
                raise ValueError('版本不合法')
            past = json.loads((folder / 'revisions' / (str(revision) + '.json')).read_text(encoding='utf-8'))
            return self.save(pid, storyboard=past['storyboard'], bindings=past.get('bindings', []),
                             inputs=past.get('inputs', {}), profile=past.get('profile', {}),
                             interview=past.get('interview', {}))

    def migrate(self, folder):
        # Explicit opt-in, stable ID, no renaming/deleting of legacy assets or outputs.
        with self.lock:
            folder = bounded(folder, self.root)
            if folder == self.root or folder.parent != self.root:
                raise ValueError('请选择旧项目目录')
            if (folder / 'project.json').exists():
                return json.loads((folder / 'project.json').read_text(encoding='utf-8'))
            storyboard = json.loads((folder / 'storyboard.json').read_text(encoding='utf-8'))
            pid = 'legacy_' + uuid.uuid5(uuid.NAMESPACE_URL, str(folder)).hex
            new_folder = self.root / pid
            record = {'schema_version': 2, 'project_id': pid, 'revision': 1,
                      'storyboard': validate_storyboard(storyboard), 'bindings': [],
                      'inputs': {}, 'profile': {}, 'interview': {}, 'legacy_folder': str(folder)}
            if (new_folder / 'project.json').exists():
                return self.load(pid)
            self._write(new_folder, record)
            return record

    def media_folder(self, record):
        return bounded(record.get('legacy_folder') or self.folder(record['project_id']), self.root)

    def listing(self):
        result = []
        if not self.root.exists():
            return result
        for folder in sorted(self.root.iterdir()):
            if not folder.is_dir() or folder.is_symlink():
                continue
            try:
                if (folder / 'project.json').exists():
                    r = json.loads((folder / 'project.json').read_text(encoding='utf-8'))
                    result.append({'project_id': r['project_id'], 'title': r['storyboard'].get('title', ''),
                                   'revision': r['revision']})
                elif (folder / 'storyboard.json').exists():
                    r = json.loads((folder / 'storyboard.json').read_text(encoding='utf-8'))
                    result.append({'legacy_folder': folder.name, 'title': r.get('title', ''), 'revision': 0})
            except (ValueError, OSError, KeyError):
                result.append({'legacy_folder': folder.name, 'title': '项目读取失败，请检查备份', 'invalid': True})
        return result
