"""Curated metadata gate and context-aware single-shot adaptation."""
import copy
import re


def approved(entry):
    scores = entry.get('scores', [])
    if not isinstance(scores, list) or len(scores) != 5 or any(type(s) is not int or not 0 <= s <= 2 for s in scores):
        return False
    return (entry.get('review_status') == '内容审核通过' and
            entry.get('completeness') == 'complete' and
            sum(scores) >= 8 and not entry.get('hard_defects') and
            entry.get('model_version') in ('Seedance 2.0', 'text-chat') and
            isinstance(entry.get('required_assets'), list))


def adapt(entry, storyboard, shot_id, inputs=None, aspect='9:16'):
    if not approved(entry) or entry.get('purpose_override') != 'shot':
        raise ValueError('此条目不是已审核镜头模板')
    if entry.get('scope') != '单镜':
        raise ValueError('连续多镜模板需按完整时间轴与多镜绑定使用，不能替换单镜')
    shot = next((s for s in storyboard.get('shots', []) if str(s.get('shot_id')) == str(shot_id)), None)
    if not shot:
        raise ValueError('目标镜头不存在，请重新选择')
    inputs = inputs or {}
    duration = float(shot.get('duration', 0))
    lower, upper = entry.get('duration_range', [3, 10])
    if not lower <= duration <= upper:
        raise ValueError(f'此适配版适合 {lower}–{upper} 秒单镜；请拆分或另选模板，不能压缩原时间轴')
    if aspect not in ('9:16', '16:9', '1:1', '4:3', '3:4'):
        raise ValueError('请选择画幅')
    chars = [c for c in storyboard.get('characters', []) if c.get('name') in shot.get('characters', [])]
    character = '；'.join(c.get('name', '') + '：' + c.get('description', '') for c in chars)
    character = character or '、'.join(shot.get('characters', [])) or inputs.get('character_desc', '') or storyboard.get('character_desc', '')
    if entry.get('category') == '环境空镜':
        if shot.get('characters') or shot.get('dialogue'):
            raise ValueError('空镜模板需要无人物、无对白的环境镜头；请先选择环境镜')
        character = '无人物，仅环境'
    scene = next((s for s in storyboard.get('scenes', []) if s.get('scene_id') == shot.get('scene_id')), {})
    environment = '，'.join(str(scene[k]) for k in ('time_of_day', 'location', 'weather', 'lighting', 'fixed_objects', 'positions') if scene.get(k))
    environment = environment or storyboard.get('environment_anchor', '') or inputs.get('location', '')
    dialogue = shot.get('dialogue', '').strip()
    if len(re.sub(r'[\s，。！？、：；,.!?]', '', dialogue)) > duration * 4:
        raise ValueError('当前对白过长，无法在计划时长内自然说完；请按语义拆镜后使用')
    values = {'角色': character, '场景': environment, '动作': shot.get('description', ''),
              '风格': inputs.get('style') or storyboard.get('style') or '沿用当前项目画风',
              '时长': f'{duration:g}', '画幅': aspect,
              '前段': f'{duration * .25:g}', '后段': f'{duration * .75:g}',
              '对白': dialogue or '无对白', '说话者': shot.get('speaker', '') if dialogue else '无人',
              '旁白': shot.get('narration', '') or '无旁白', '镜头': shot.get('camera_movement', '')}
    text = re.sub(r'\{\{([^{}\n]{1,40})\}\}', lambda m: str(values.get(m[1]) or m[0]), entry['text'])
    missing = sorted(set(re.findall(r'\{\{([^{}]+)\}\}', text)))
    return {'text': text, 'variables': values, 'missing_variables': missing,
            'required_assets': copy.deepcopy(entry['required_assets']), 'shot_id': shot['shot_id'],
            'test_status': entry.get('test_status', '未实测')}
