"""Story coverage and inert pasted-model JSON, without executing templates."""
import json
import math
import re
from dataclasses import asdict

from .character_manager import CharacterProfile
from .project_store import validate_storyboard

AUTO_MODEL = object()


def parse_model_result(text):
    text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
    value = json.loads(text)
    if isinstance(value, list):
        value = {'shots': value}
    return validate_storyboard(value)


def build(text, style='通用', min_sec=3, max_sec=6, target_shots=0, subject='',
          character_desc='', location='', time_of_day='', mood='', creative_point='', key_shot='',
          llm_result=AUTO_MODEL):
    from . import browser_workflow as old
    text = text.strip()
    if not text:
        raise ValueError('请先输入故事内容')
    subject = subject or old._detect_subject(text)
    character_desc = character_desc or old.DEFAULT_CHAR_DESC
    location = location.strip() or old._detect(text, old.LOCATION_RULES, '城市角落')
    time_of_day = time_of_day.strip() or ('夜晚' if any(k in text for k in ('夜', '晚')) else '白天')
    mood = mood or old._detect(text, old.MOOD_RULES, '平静')
    min_sec, max_sec = max(1, int(min_sec)), max(1, int(max_sec))
    if min_sec > max_sec or max_sec > 600:
        raise ValueError('分镜时长范围不合法')
    if not 0 <= int(target_shots) <= 300:
        raise ValueError('镜头数量应为 0–300')
    constraints = dict(subject=subject, character_desc=character_desc, location=location,
                       time_of_day=time_of_day, mood=mood, creative_point=creative_point,
                       key_shot=key_shot, target_shots=target_shots, min_sec=min_sec, max_sec=max_sec)
    llm = old._llm_expand(text, style, constraints) if llm_result is AUTO_MODEL else llm_result
    units = [p.strip() for p in re.split(r'(?<=[。！？])(?=[^”」』])|(?<=[”」』])|\n+', text) if p.strip()]
    count = int(target_shots) or max(5, len(units), math.ceil(len(text) / 140))
    if count > 300:
        raise ValueError('故事过长，请按章节分开创作')
    fallback = []
    # Partition all text in order; final event is never trimmed.
    for i in range(count):
        a, b = round(i * len(units) / count), round((i + 1) * len(units) / count)
        beat = ''.join(units[a:b])
        quotes = re.findall(r'[“「『"]([^”」』"]+)[”」』"]', beat)
        fallback.append({'description': beat or f'{subject}在{location}的细节与反应，承接前后剧情。',
                         'dialogue': '\n'.join(quotes), 'narration': '',
                         'shot_type': 'dialogue' if quotes else 'action' if beat else 'empty',
                         'camera_movement': '极慢拉' if i == count - 1 else '静态',
                         'duration': old._durations(count, min_sec, max_sec)[i]})
    raw = llm['shots'] if llm else fallback
    if len(raw) > 300:
        raise ValueError('模型输出超过 300 镜，请分章节')
    anchor = f'{time_of_day}，{location}'
    shots = []
    for i, r in enumerate(raw, 1):
        description = str(r.get('description', ''))
        duration = max(min_sec, min(max_sec, float(r.get('duration') or 4)))
        dialogue = str(r.get('dialogue', ''))
        narration = str(r.get('narration', ''))
        camera = str(r.get('camera_movement', r.get('camera', '静态')))
        # Raw model text and literal braces are inert, never str.format'ed.
        prompt = (f'风格：{style}\n角色：{character_desc}\n场景约束：{anchor}；情绪：{mood}\n'
                  f'动作：{description}\n运镜：{camera}\n时长：{duration} 秒\n'
                  f'对白：{dialogue}\n旁白：{narration}\n创意：{creative_point}\n重点：{key_shot}')
        shots.append({**r, 'shot_id': r.get('shot_id', i), 'description': description,
                      'duration': duration, 'dialogue': dialogue, 'narration': narration,
                      'speaker': r.get('speaker', subject if dialogue else ''),
                      'camera_movement': camera, 'prompt': str(r.get('prompt') or prompt),
                      'shot_type': r.get('shot_type', 'dialogue' if dialogue else 'action'),
                      'scene_id': r.get('scene_id', 'scene_1'), 'characters': r.get('characters', [subject]),
                      'negative_prompt': old.DOUBAO_NEGATIVE_PROMPT, 'subtitle_cues': r.get('subtitle_cues', [])})
    warnings = []
    for shot in shots:
        if time_of_day in ('白天', '黎明') and any(w in shot['description'] for w in ('夜色', '夜晚', '深夜')):
            warnings.append(f"镜头 {shot['shot_id']} 的时间与场景约束冲突，请编辑")
    dialogues = [s['dialogue'] for s in shots if s['dialogue']]
    if len(set(dialogues)) != len(dialogues):
        warnings.append('模型输出含重复对白，请核对归属')
    if llm and text[-min(16, len(text)):] not in ''.join(s['description'] for s in shots):
        warnings.append('模型输出的结尾覆盖未验证，请对照原故事')
    return validate_storyboard({'title': (llm or {}).get('title', text[:16]), 'input_text': text,
        'expanded_text': (llm or {}).get('expanded_text', anchor + '。' + text),
        'character_desc': character_desc, 'environment_anchor': anchor, 'creative_point': creative_point,
        'key_shot': key_shot, 'global_base_prompt': old.DOUBAO_GLOBAL_BASE_PROMPT,
        'negative_prompt': old.DOUBAO_NEGATIVE_PROMPT, 'constraints': constraints,
        'characters': (llm or {}).get('characters', [asdict(CharacterProfile(name=subject, description=character_desc))]),
        'scenes': (llm or {}).get('scenes', [{'scene_id': 'scene_1', 'location': location, 'time_of_day': time_of_day,
                    'weather': '', 'lighting': '', 'fixed_objects': '', 'positions': ''}]),
        'warnings': warnings, 'shots': shots, 'generation': '模型输出' if llm else '本地剧情分段'})
