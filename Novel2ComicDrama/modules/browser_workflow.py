"""
browser_workflow.py — 浏览器工作流

流程：
  用户输入短文本 → 扩写 → 拆成 3-6 秒分镜 → 一键复制提示词
  → 用户在浏览器豆包生成视频 → 下载到 output/browser_videos
  → 本地一键合成最终视频
"""

import json
import os
import re
import subprocess
import time
import urllib.request
from typing import Callable, Dict, List, Optional

from .scene_splitter import Shot
from .director_workspace import (
    DoubaoVideoGenerator,
    DOUBAO_GLOBAL_BASE_PROMPT,
    DOUBAO_NEGATIVE_PROMPT,
)
from .video_composer import VideoComposer


DEFAULT_CHAR_DESC = "主角：统一漫画人设，服饰、五官、配色全程一致，不篡改画风"

EXPAND_TEMPLATES = {
    "通用": (
        "夜色与风把故事拉长，{text}。镜头从环境缓缓推向人物，"
        "动作与情绪层层递进，每一个停顿都留下呼吸感。"
    ),
    "热血动作": (
        "{text}。风声、脚步与心跳叠加在一起，{subject}把全部力量压进下一个动作，"
        "镜头跟随节奏推进，画面始终保持稳定与干净。"
    ),
    "情感叙事": (
        "{text}。安静的空间里，光线一点点变化，{subject}的情绪藏在细微的表情与动作里，"
        "沉默比话语更重。"
    ),
    "唯美空镜": (
        "{text}。画面以环境为主，光影、风与色彩缓慢流动，气氛统一柔和，"
        "像一首没有旁白的诗。"
    ),
    "悬念反转": (
        "{text}。画面刻意留白，细节里藏着线索，镜头缓慢移动，"
        "直到最后一秒才揭晓答案。"
    ),
}

STYLE_SHOT_TEMPLATES = {
    "通用": [
        {
            "shot_type": "empty",
            "description": "远景：{time}{location}的完整环境，{mood}的光线铺满画面，空气感清晰。",
            "camera": "慢推",
            "mood": "平静",
            "narration": "",
        },
        {
            "shot_type": "dialogue",
            "description": "中景：{subject}进入画面，{text}。人物动作自然，表情清晰，服饰细节完整。",
            "camera": "静态",
            "mood": "沉浸",
            "narration": "",
        },
        {
            "shot_type": "action",
            "description": "近景：{subject}完成关键动作，动作轨迹连贯，周围环境产生轻微呼应。",
            "camera": "跟随",
            "mood": "推进",
            "narration": "风在这一刻变得清晰。",
        },
        {
            "shot_type": "dialogue",
            "description": "特写：{subject}的面部特写，眼神与情绪变化细腻，光影稳定。",
            "camera": "静态",
            "mood": "聚焦",
            "narration": "",
        },
        {
            "shot_type": "empty",
            "description": "空镜：与情节呼应的环境细节，风、光或水缓慢流动，留下呼吸感。",
            "camera": "固定",
            "mood": "留白",
            "narration": "",
        },
        {
            "shot_type": "dialogue",
            "description": "收尾：{subject}的背影或定格画面，{text}的余韵延续。",
            "camera": "极慢拉",
            "mood": "余韵",
            "narration": "",
        },
    ],
    "热血动作": [
        {
            "shot_type": "empty",
            "description": "远景：{time}{location}，风暴来临前的压迫感，低云与尘土悬浮。",
            "camera": "慢推",
            "mood": "压迫",
            "narration": "",
        },
        {
            "shot_type": "action",
            "description": "中景：{subject}压低重心，蓄力准备，{text}。衣摆与发丝随气流扬起。",
            "camera": "静态",
            "mood": "蓄势",
            "narration": "",
        },
        {
            "shot_type": "action",
            "description": "近景：{subject}猛然爆发，动作凌厉连贯，拳风或脚步带动环境碎片飞散。",
            "camera": "跟随",
            "mood": "爆发",
            "narration": "破空声在耳边炸开。",
        },
        {
            "shot_type": "action",
            "description": "连续动作：{subject}追击或闪避，镜头小幅跟随，速度感强但画面稳定。",
            "camera": "跟焦",
            "mood": "激烈",
            "narration": "",
        },
        {
            "shot_type": "dialogue",
            "description": "特写：{subject}喘息与眼神特写，胜负未分的紧张感。",
            "camera": "静态",
            "mood": "紧张",
            "narration": "",
        },
        {
            "shot_type": "action",
            "description": "收尾：{subject}停住身形，尘埃落定，气氛从爆发转为短暂沉寂。",
            "camera": "极慢拉",
            "mood": "沉寂",
            "narration": "",
        },
    ],
    "情感叙事": [
        {
            "shot_type": "empty",
            "description": "远景：{time}{location}，安静而缓慢的日常感，光线柔和。",
            "camera": "慢推",
            "mood": "安静",
            "narration": "",
        },
        {
            "shot_type": "dialogue",
            "description": "中景：{subject}站在原地，{text}。表情克制，动作幅度很小。",
            "camera": "静态",
            "mood": "克制",
            "narration": "",
        },
        {
            "shot_type": "dialogue",
            "description": "近景：{subject}说出内心话，嘴角或眼角有细微变化，情绪逐渐浮现。",
            "camera": "缓推",
            "mood": "浮现",
            "narration": "",
        },
        {
            "shot_type": "empty",
            "description": "空镜：窗、树或雨等象征物，光影缓慢移动，衬托回忆与思念。",
            "camera": "固定",
            "mood": "怀念",
            "narration": "",
        },
        {
            "shot_type": "dialogue",
            "description": "特写：{subject}低头或抬眼，泪水或笑容在特写中放大。",
            "camera": "静态",
            "mood": "动容",
            "narration": "",
        },
        {
            "shot_type": "dialogue",
            "description": "收尾：{subject}转身或留下一个定格，情绪沉淀。",
            "camera": "极慢拉",
            "mood": "余韵",
            "narration": "",
        },
    ],
    "唯美空镜": [
        {
            "shot_type": "empty",
            "description": "大远景：{time}{location}全景，天空与地面层次分明，色彩统一柔和。",
            "camera": "固定",
            "mood": "开阔",
            "narration": "",
        },
        {
            "shot_type": "empty",
            "description": "空镜：光影在{location}中缓慢移动，尘埃或雾气可见，画面纯净。",
            "camera": "极慢推",
            "mood": "静谧",
            "narration": "",
        },
        {
            "shot_type": "empty",
            "description": "细节空镜：风掠过树梢、水面或纱帘，极轻微动态，质感细腻。",
            "camera": "固定",
            "mood": "轻柔",
            "narration": "",
        },
        {
            "shot_type": "empty",
            "description": "中远景：{subject}或景物剪影融入{location}，留白充足。",
            "camera": "缓移",
            "mood": "诗意",
            "narration": "",
        },
        {
            "shot_type": "empty",
            "description": "空镜：{mood}色调的光斑与倒影，画面无颗粒无杂色。",
            "camera": "固定",
            "mood": "梦幻",
            "narration": "",
        },
        {
            "shot_type": "empty",
            "description": "收尾：{time}的最后一束光，环境归于平静，余韵悠长。",
            "camera": "极慢拉",
            "mood": "余韵",
            "narration": "",
        },
    ],
    "悬念反转": [
        {
            "shot_type": "empty",
            "description": "远景：{time}{location}，光线异常，细节埋着不安的伏笔。",
            "camera": "慢推",
            "mood": "不安",
            "narration": "",
        },
        {
            "shot_type": "dialogue",
            "description": "中景：{subject}发现异常，{text}。动作停顿，眼神警觉。",
            "camera": "静态",
            "mood": "警觉",
            "narration": "",
        },
        {
            "shot_type": "action",
            "description": "近景：{subject}快速行动或转身，镜头跟焦，紧张感增强。",
            "camera": "跟焦",
            "mood": "紧张",
            "narration": "脚步声在空旷处回响。",
        },
        {
            "shot_type": "empty",
            "description": "细节空镜：线索物品、脚印或纸条的特写，画面克制。",
            "camera": "固定",
            "mood": "线索",
            "narration": "",
        },
        {
            "shot_type": "dialogue",
            "description": "特写：{subject}的表情从疑惑转向恍然，暗示真相。",
            "camera": "缓推",
            "mood": "顿悟",
            "narration": "",
        },
        {
            "shot_type": "dialogue",
            "description": "反转收尾：{subject}定格，画面留白，余味指向新线索。",
            "camera": "极慢拉",
            "mood": "反转",
            "narration": "",
        },
    ],
}

LOCATION_RULES = [
    ("废弃", "废弃楼宇"),
    ("教室", "教室"),
    ("天台", "天台"),
    ("海边", "海边"),
    ("森林", "森林"),
    ("雨", "雨夜街道"),
    ("夜", "夜晚城市"),
    ("医院", "医院走廊"),
    ("车站", "车站"),
    ("山顶", "山顶"),
    ("办公室", "办公室"),
    ("公园", "公园"),
    ("战场", "战场"),
    ("街道", "街道"),
    ("古堡", "古堡"),
    ("实验室", "实验室"),
    ("天空", "天空"),
]

MOOD_RULES = [
    ("哭", "悲伤"),
    ("泪", "悲伤"),
    ("孤独", "孤独"),
    ("思念", "怀念"),
    ("开心", "明亮"),
    ("笑", "轻松"),
    ("紧张", "紧张"),
    ("危险", "压迫"),
    ("愤怒", "激烈"),
    ("浪漫", "温柔"),
    ("平静", "安静"),
    ("神秘", "神秘"),
    ("战斗", "激烈"),
    ("追", "紧张"),
]


def _detect(text: str, rules: List[tuple], default: str) -> str:
    for keyword, value in rules:
        if keyword in text:
            return value
    return default


def _detect_subject(text: str) -> str:
    match = re.search(r"([\u4e00-\u9fa5]{2,4})(?:在|正在|突然|缓缓|朝着|向|对|和)", text)
    if match:
        return match.group(1)
    return "主角"


def _detect_dialogue(text: str) -> str:
    match = re.search(r"[\"\"「」]([^\"\"「」]{1,60})[\"\"「」]", text)
    if match:
        return match.group(1)
    return ""


def _durations(count: int, min_sec: int, max_sec: int) -> List[int]:
    base = [5, 4, 6, 3, 5, 4, 5, 6, 4, 3, 5, 4]
    result = []
    for i in range(count):
        duration = max(min_sec, min(max_sec, base[i % len(base)]))
        result.append(duration)
    return result


def expand_narrative(text: str, style: str, subject: str) -> str:
    template = EXPAND_TEMPLATES.get(style, EXPAND_TEMPLATES["通用"])
    return template.format(text=text.strip(), subject=subject)


def _build_shot(shot_id: int, template: dict, context: dict,
                duration: int, character_desc: str,
                dialogue: str = "", creative_point: str = "",
                key_shot: str = "") -> dict:
    description = template["description"].format(**context)
    environment_anchor = context.get("environment_anchor", "")
    if environment_anchor and not description.startswith(environment_anchor):
        description = f"{environment_anchor}，{description}"
    if creative_point and shot_id == 3:
        description = f"{description} 关键创意画面：{creative_point}"
    if key_shot and shot_id == 4:
        description = f"{description} 重点镜头：{key_shot}"
    shot_type = template["shot_type"]
    subject = context["subject"]
    characters = [subject] if shot_type != "empty" else []
    if shot_type == "dialogue":
        dialogue = dialogue or context["dialogue"]
    else:
        dialogue = ""

    shot = Shot(
        shot_id=shot_id,
        description=description,
        characters=characters,
        dialogue=dialogue,
        speaker=subject if dialogue else "",
        narration=template.get("narration", ""),
        estimated_duration=float(duration),
        mood=template.get("mood", "平静"),
        camera_movement=template.get("camera", "静态"),
        image_prompt="",
        shot_type=shot_type,
    )

    generator = DoubaoVideoGenerator()
    prompt = generator.build_shot_prompt(
        shot,
        scene=None,
        character_manager=None,
        char_desc=character_desc,
        scene_desc=description,
    )
    return {
        "shot_id": shot_id,
        "shot_type": shot_type,
        "duration": duration,
        "description": description,
        "dialogue": dialogue,
        "speaker": shot.speaker,
        "narration": shot.narration,
        "mood": shot.mood,
        "camera_movement": shot.camera_movement,
        "characters": characters,
        "prompt": prompt,
        "negative_prompt": DOUBAO_NEGATIVE_PROMPT,
    }


def _llm_expand(text: str, style: str) -> Optional[dict]:
    """可选：用 OpenAI 兼容接口做更高质量的扩写，失败时回退内置模板。"""
    api_key = os.getenv("EXPAND_LLM_API_KEY", "")
    base_url = os.getenv("EXPAND_LLM_BASE_URL", "")
    model = os.getenv("EXPAND_LLM_MODEL", "")
    if not api_key or not base_url:
        return None

    prompt = (
        "你是漫剧分镜师。请把下面简短内容扩写成适合AI漫剧的叙事，"
        "并拆成3-6秒的分镜。只返回JSON："
        '{"title":"标题","expanded_text":"扩写后的叙事","shots":'
        '[{"shot_type":"dialogue/action/empty","description":"画面描述",'
        '"duration":4,"dialogue":"台词或空","narration":"旁白或空"}]}\n'
        f"风格：{style}\n内容：{text}"
    )
    payload = {
        "model": model or "doubao-seed-1-6-251015",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 3000,
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        content = data["choices"][0]["message"]["content"]
        content = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", content.strip())
        parsed = json.loads(content)
        if not isinstance(parsed.get("shots"), list) or not parsed["shots"]:
            return None
        return parsed
    except Exception:
        return None


def build_storyboard(text: str, style: str = "通用",
                     min_sec: int = 3, max_sec: int = 6,
                     target_shots: int = 0,
                     subject: str = "", character_desc: str = "",
                     location: str = "", time_of_day: str = "",
                     mood: str = "", creative_point: str = "",
                     key_shot: str = "") -> dict:
    """
    短文本 → 扩写 → 分镜，输出可直接复制的豆包视频提示词包。
    """
    text = text.strip()
    if not text:
        raise ValueError("请先输入故事内容")

    min_sec = max(1, int(min_sec))
    max_sec = max(min_sec, int(max_sec))
    if not subject:
        subject = _detect_subject(text)
    if not character_desc:
        character_desc = DEFAULT_CHAR_DESC

    llm_result = _llm_expand(text, style)
    if llm_result is not None:
        expanded_text = llm_result.get("expanded_text") or expand_narrative(text, style, subject)
        title = llm_result.get("title") or text[:16]
        raw_shots = (llm_result.get("shots", []) or [])[:5]
    else:
        expanded_text = expand_narrative(text, style, subject)
        title = text[:16]
        raw_shots = None

    if raw_shots is None:
        templates = STYLE_SHOT_TEMPLATES.get(style, STYLE_SHOT_TEMPLATES["通用"])
        count = min(5, max(1, int(target_shots or 5)))
        if count > len(templates):
            templates = templates + templates[: count - len(templates)]
        templates = templates[:count]
        raw_shots = []
        for template in templates:
            raw_shots.append({
                "shot_type": template["shot_type"],
                "description": template["description"],
                "duration": None,
                "dialogue": template.get("dialogue", ""),
                "narration": template.get("narration", ""),
                "camera": template.get("camera", "静态"),
                "mood": template.get("mood", "平静"),
            })

    location = location.strip() or _detect(text, LOCATION_RULES, "城市角落")
    mood = mood.strip() or _detect(text, MOOD_RULES, "平静")
    time_of_day = time_of_day.strip() or (
        "夜晚" if any(k in text for k in ("夜", "晚")) else (
            "黄昏" if "黄昏" in text else "白天"
        )
    )
    dialogue = _detect_dialogue(text)
    environment_anchor = f"{time_of_day}，{location}"
    context = {
        "text": text,
        "subject": subject,
        "location": location,
        "mood": mood,
        "time": time_of_day,
        "dialogue": dialogue,
        "environment_anchor": environment_anchor,
        "creative_point": creative_point,
        "key_shot": key_shot,
    }
    expanded_text = f"{environment_anchor}。{expanded_text}"
    if creative_point:
        expanded_text = f"{expanded_text} 创意点：{creative_point}"
    if key_shot:
        expanded_text = f"{expanded_text} 画面重点：{key_shot}"

    durations = _durations(len(raw_shots), min_sec, max_sec)
    shots = []
    for index, raw in enumerate(raw_shots, 1):
        duration = int(raw.get("duration") or durations[index - 1])
        duration = max(min_sec, min(max_sec, duration))

        if "{" in str(raw.get("description", "")):
            raw_desc = raw["description"].format(**context)
        else:
            raw_desc = raw.get("description", expanded_text)
        shot_desc = raw_desc
        if len(shot_desc) > 220:
            shot_desc = shot_desc[:220]

        shot_type = raw.get("shot_type", "dialogue")
        if shot_type not in ("dialogue", "action", "empty"):
            shot_type = "dialogue"

        template = {
            "shot_type": shot_type,
            "description": shot_desc,
            "camera": raw.get("camera", "静态"),
            "mood": raw.get("mood", mood),
            "narration": raw.get("narration", ""),
        }
        shots.append(
            _build_shot(
                index, template, context, duration, character_desc,
                dialogue=raw.get("dialogue", ""),
                creative_point=creative_point,
                key_shot=key_shot,
            )
        )

    return {
        "title": title,
        "input_text": text,
        "expanded_text": expanded_text,
        "character_desc": character_desc,
        "environment_anchor": environment_anchor,
        "creative_point": creative_point,
        "key_shot": key_shot,
        "global_base_prompt": DOUBAO_GLOBAL_BASE_PROMPT,
        "negative_prompt": DOUBAO_NEGATIVE_PROMPT,
        "shots": shots,
    }


def save_storyboard(storyboard: dict, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "storyboard.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(storyboard, f, ensure_ascii=False, indent=2)
    return path


def create_project_folder(storyboard: dict, output_dir: str) -> str:
    """确认分镜后，在 output/browser 下创建该项目专属视频文件夹"""
    slug = re.sub(r'[\\/:*?"<>|\s]+', "_", storyboard.get("title", "project"))
    slug = slug.strip("_")[:30] or "project"
    folder = os.path.join(output_dir, "browser", slug)
    os.makedirs(folder, exist_ok=True)
    save_storyboard(storyboard, folder)
    return folder


def list_browser_videos(videos_dir: str) -> List[dict]:
    if not os.path.isdir(videos_dir):
        return []
    extensions = (".mp4", ".mov", ".webm", ".m4v")
    def _natural_key(name: str):
        parts = re.split(r"(\d+)", name)
        return [int(part) if part.isdigit() else part.lower() for part in parts]

    files = sorted(
        (name for name in os.listdir(videos_dir)
         if name.lower().endswith(extensions)),
        key=_natural_key,
    )
    result = []
    for name in files:
        path = os.path.join(videos_dir, name)
        result.append({
            "filename": name,
            "path": path,
            "size_mb": round(os.path.getsize(path) / 1024 / 1024, 2),
        })
    return result


def get_video_duration(path: str) -> float:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration", "-of",
             "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10,
        )
        return max(0.1, float(result.stdout.strip()))
    except Exception:
        return 5.0


def compose_browser_video(storyboard_path: str, videos_dir: str,
                          output_dir: str, bgm_path: str = "",
                          bgm_volume: float = 0.15,
                          progress_callback: Callable[[str, int], None] = None) -> str:
    """
    把浏览器豆包生成的视频按分镜顺序合成最终视频。
    """
    def report(step: str, percent: int):
        if progress_callback is not None:
            progress_callback(step, percent)

    report("读取分镜脚本", 5)
    with open(storyboard_path, "r", encoding="utf-8") as f:
        storyboard = json.load(f)
    report("扫描项目视频", 15)
    videos = list_browser_videos(videos_dir)
    shots = storyboard.get("shots", [])

    if not videos:
        raise RuntimeError("项目视频文件夹中没有找到视频文件，请先放入 1.mp4、2.mp4 等视频")

    timeline = []
    current = 0.0
    report("读取视频时长", 30)
    total_shots = max(len(shots), 1)
    total_videos = len(videos)
    for index, video in enumerate(videos, 1):
        start_idx = round((index - 1) * total_shots / total_videos)
        end_idx = round(index * total_shots / total_videos)
        group = shots[start_idx:end_idx]
        duration = get_video_duration(video["path"])
        timeline.append({
            "start_time": current,
            "end_time": current + duration,
            "duration": duration,
            "video_path": video["path"],
            "audio_path": "",
            "scene_id": 1,
            "shot_id": index,
            "narration": " ".join(
                shot.get("narration", "") for shot in group if shot.get("narration")
            ).strip(),
            "dialogue": " ".join(
                shot.get("dialogue", "") for shot in group if shot.get("dialogue")
            ).strip(),
            "speaker": group[0].get("speaker", "") if group else "",
            "mood": group[0].get("mood", "") if group else "",
            "description": group[0].get("description", "") if group else "",
        })
        current += duration

    report("拼接视频与保留声音", 55)
    composer = VideoComposer(
        output_dir=output_dir,
        bgm_dir=os.path.dirname(bgm_path) if bgm_path else "",
        font_path="",
    )
    final_path = os.path.join(output_dir, "browser", "final_video.mp4")
    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    final = composer.compose(
        timeline=timeline,
        output_path=final_path,
        bgm_path=bgm_path,
        bgm_volume=bgm_volume,
        preserve_source_audio=True,
    )
    report("合成完成", 100)
    return final
