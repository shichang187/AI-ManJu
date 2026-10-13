"""Checked FFmpeg export for the browser; legacy CLI composer is preserved."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess

from .project_store import atomic_json, bounded

VIDEO_TYPES = {'.mp4', '.mov', '.webm', '.m4v'}
OPEN_TYPES = VIDEO_TYPES | {'.wav', '.mp3', '.m4a', '.png', '.jpg', '.jpeg', '.srt'}


def run(args, timeout=300, cwd=None):
    result = subprocess.run(args, capture_output=True, timeout=timeout, cwd=cwd)
    if result.returncode:
        error = result.stderr.decode('utf-8', errors='replace')[-1800:]
        raise RuntimeError('媒体工具执行失败：' + error)
    return result.stdout


def probe(path, decode=False):
    value = json.loads(run(['ffprobe', '-v', 'error', '-show_streams', '-show_format',
                            '-of', 'json', str(path)], timeout=30))
    videos = [s for s in value.get('streams', []) if s.get('codec_type') == 'video']
    if not videos:
        raise ValueError('素材没有视频流')
    duration = float(value.get('format', {}).get('duration', 0))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('素材时长无效')
    v = videos[0]
    if decode:
        run(['ffmpeg', '-v', 'error', '-xerror', '-i', str(path), '-map', '0:v:0', '-map', '0:a?', '-f', 'null', '-'])
    return {'duration': duration, 'width': v['width'], 'height': v['height'],
            'fps': v.get('avg_frame_rate'),
            'audio': any(s.get('codec_type') == 'audio' for s in value['streams'])}


def scan(record, folder, check=False):
    folder = Path(folder)
    files = []
    if folder.is_dir():
        for p in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
            if p.suffix.lower() in VIDEO_TYPES and p.is_file() and not p.is_symlink():
                item = {'filename': p.name, 'path': str(p), 'size_mb': round(p.stat().st_size / 1024 ** 2, 2)}
                if check:
                    try:
                        item['media'] = probe(p, decode=True)
                    except Exception as exc:
                        item['error'] = str(exc)
                files.append(item)
    ids = [str(s['shot_id']) for s in record['storyboard']['shots']]
    available = {f['filename'] for f in files}
    bindings = record.get('bindings') or []
    issues = []
    if not bindings:
        for sid in ids:
            candidates = [f for f in available if Path(f).stem == sid]
            if len(candidates) != 1:
                issues.append({'shot_id': sid, 'reason': '缺少素材' if not candidates else '有多个备选，请明确选择'})
            else:
                bindings.append({'shot_ids': [sid], 'filename': candidates[0]})
    covered = []
    names = []
    for b in bindings:
        group = list(map(str, b.get('shot_ids', [])))
        name = b.get('filename', '')
        if not group or any(s not in ids for s in group):
            issues.append({'shot_id': ','.join(group), 'reason': '对应关系包含不存在的镜头'})
        if name not in available or Path(name).name != name:
            issues.append({'shot_id': ','.join(group), 'reason': '对应素材不存在或路径不合法'})
        covered += group
        names.append(name)
    if covered != ids:
        issues.append({'shot_id': '', 'reason': '素材必须按镜头顺序完整覆盖，每镜恰好一次；请补齐或调整对应关系'})
    if len(names) != len(set(names)):
        issues.append({'shot_id': '', 'reason': '同一素材重复使用；多镜合一请使用一个 shot_ids 数组'})
    return {'videos': files, 'bindings': bindings, 'issues': issues,
            'count': len(files), 'ready': not issues and bool(files)}


def snapshot(record, folder, task_dir, bgm=''):
    inventory = scan(record, folder)
    if not inventory['ready']:
        raise ValueError('素材未就绪：' + '；'.join(i['reason'] for i in inventory['issues']))
    task_dir = Path(task_dir)
    inputs = task_dir / 'inputs'
    inputs.mkdir(parents=True, exist_ok=False)
    record = json.loads(json.dumps(record))
    manifest = []
    for i, binding in enumerate(inventory['bindings']):
        source = bounded(Path(folder) / binding['filename'], folder)
        target = inputs / (str(i) + source.suffix.lower())
        before = source.stat()
        shutil.copyfile(source, target)
        after = source.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError('素材在复制期间改变，请重新导出')
        manifest.append({**binding, 'path': str(target),
                         'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    if bgm:
        bgm = Path(bgm)
        if not bgm.is_file() or bgm.suffix.lower() not in {'.mp3', '.wav', '.m4a', '.aac', '.ogg', '.flac'}:
            raise ValueError('背景音乐必须是可读取的音频文件')
        target = inputs / ('music' + bgm.suffix.lower())
        shutil.copyfile(bgm, target)
        bgm = str(target)
    atomic_json(task_dir / 'snapshot.json', {'project': record, 'media': manifest, 'bgm': str(bgm)})
    return task_dir / 'snapshot.json'


def timestamp(seconds):
    ms = round(seconds * 1000)
    h, rest = divmod(ms, 3600000)
    m, rest = divmod(rest, 60000)
    s, ms = divmod(rest, 1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def export(snapshot_path, task_dir, settings=None, report=lambda *_: None):
    settings = settings or {}
    width, height, fps = [int(settings.get(k, default)) for k, default in
                          [('width', 1280), ('height', 720), ('fps', 30)]]
    if not (16 <= width <= 3840 and 16 <= height <= 3840 and width % 2 == height % 2 == 0 and 1 <= fps <= 60):
        raise ValueError('画幅应为偶数、16–3840 像素，帧率 1–60')
    fit = settings.get('fit', 'pad')
    if fit not in ('pad', 'crop'):
        raise ValueError('画幅策略只能是 pad 或 crop')
    volume = float(settings.get('bgm_volume', .15))
    if not math.isfinite(volume) or not 0 <= volume <= 1:
        raise ValueError('音乐音量必须为 0–1')
    task_dir = Path(task_dir).resolve()
    data = json.loads(Path(snapshot_path).read_text(encoding='utf-8'))
    shots = {str(s['shot_id']): s for s in data['project']['storyboard']['shots']}
    total, captions, timeline, clips = 0., [], [], []
    for i, asset in enumerate(data['media']):
        report('预检并统一素材规格', 10 + int(55 * i / len(data['media'])))
        info = probe(asset['path'], decode=True)
        duration = info['duration']
        name = f'clip_{i:04}.mp4'
        vf = (f'scale={width}:{height}:force_original_aspect_ratio=' +
              ('decrease' if fit == 'pad' else 'increase') + ',' +
              (f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2' if fit == 'pad' else f'crop={width}:{height}') +
              f',setsar=1,fps={fps},format=yuv420p')
        args = ['ffmpeg', '-v', 'error', '-xerror', '-y', '-i', asset['path']]
        if not info['audio']:
            args += ['-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo']
        args += ['-map', '0:v:0', '-map', '0:a:0' if info['audio'] else '1:a:0',
                 '-vf', vf, '-af', 'aresample=48000,apad', '-t', str(duration),
                 '-c:v', 'libx264', '-preset', 'fast', '-crf', '20', '-c:a', 'aac',
                 '-ar', '48000', '-ac', '2', name]
        run(args, cwd=task_dir)
        clips.append(name)
        # Sentence times are explicit. Unaligned planned text only appears when opted in.
        for sid in asset['shot_ids']:
            shot = shots[str(sid)]
            cues = shot.get('subtitle_cues', [])
            if not cues and settings.get('planned_subtitles'):
                if len(asset['shot_ids']) > 1 and (shot.get('dialogue') or shot.get('narration')):
                    raise ValueError('多镜合一素材需要句级字幕时间，不能平均分配台词')
                text = '\n'.join(v for v in (shot.get('dialogue'), shot.get('narration')) if v)
                cues = [{'start': 0, 'end': duration, 'text': text}] if text else []
            for cue in cues:
                start, end = float(cue['start']), float(cue['end'])
                if not 0 <= start < end <= duration or not isinstance(cue.get('text'), str):
                    raise ValueError('字幕时间必须位于对应素材内；文字必须是文本')
                captions.append((total + start, total + end, cue['text']))
        timeline.append({**asset, 'media': info, 'start': total, 'end': total + duration})
        total += duration
    report('拼接与字幕', 72)
    (task_dir / 'concat.txt').write_text(''.join(f"file '{n}'\n" for n in clips), encoding='utf-8')
    run(['ffmpeg', '-v', 'error', '-xerror', '-y', '-f', 'concat', '-safe', '1',
         '-i', 'concat.txt', '-c:v', 'libx264', '-preset', 'fast', '-crf', '20',
         '-af', 'aresample=async=1:first_pts=0', '-c:a', 'aac', 'joined.mp4'], cwd=task_dir)
    captions.sort(key=lambda c: c[0])
    srt = ''.join(f'{i}\n{timestamp(a)} --> {timestamp(b)}\n{text}\n\n'
                  for i, (a, b, text) in enumerate(captions, 1))
    (task_dir / 'subtitles.srt').write_text(srt, encoding='utf-8')
    args = ['ffmpeg', '-v', 'error', '-xerror', '-y', '-i', 'joined.mp4']
    if data.get('bgm'):
        args += ['-stream_loop', '-1', '-i', data['bgm'], '-filter_complex',
                 f'[1:a]volume={volume}[music];[0:a][music]amix=inputs=2:duration=first:normalize=0[a]',
                 '-map', '0:v:0', '-map', '[a]']
    else:
        args += ['-map', '0:v:0', '-map', '0:a:0']
    if captions:
        args += ['-vf', 'subtitles=subtitles.srt', '-c:v', 'libx264', '-preset', 'fast', '-crf', '20']
    else:
        args += ['-c:v', 'copy']
    args += ['-c:a', 'aac', '-t', str(total), '-movflags', '+faststart', 'final.pending.mp4']
    run(args, cwd=task_dir)
    report('验证新成片', 92)
    info = probe(task_dir / 'final.pending.mp4', decode=True)
    if (not info['audio'] or info['width'] != width or info['height'] != height or
            abs(info['duration'] - total) > max(.25, 2 / fps) or
            (task_dir / 'final.pending.mp4').stat().st_size < 100):
        raise RuntimeError('新成片规格或时长验证失败')
    atomic_json(task_dir / 'export.json', {'duration': total, 'media': info, 'timeline': timeline,
                'settings': settings, 'project_revision': data['project']['revision'],
                'subtitle_basis': '人工句级时间或计划文本；声音对齐效果未验证'})
    os.replace(task_dir / 'final.pending.mp4', task_dir / 'final.mp4')
    report('合成完成', 100)
    return str(task_dir / 'final.mp4')
