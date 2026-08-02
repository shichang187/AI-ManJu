"""
scene_splitter.py — 小说场景分镜解析器

功能：
1. 将小说文本解析为场景列表（场次、时间、氛围）
2. 为每个场景生成详细分镜（镜头序号、画面描述、角色、台词、时长估算）
3. 输出结构化的分镜脚本JSON，供下游director_workspace使用

输入：小说文本（str）
输出：SceneScript 列表，每个包含：
  - scene_id: 场景序号
  - setting: 场景设定（地点、时间、氛围）
  - shots: 镜头列表
    - shot_id: 镜头序号
    - description: 画面描述（用于文生图/视频提示词）
    - characters: 出现的角色
    - dialogue: 台词（如果有）
    - narration: 旁白（如果有）
    - estimated_duration: 估算时长（秒）
    - mood: 情绪/氛围
    - shot_type: 镜头类型（dialogue/action/empty）
"""

import json
import re
import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional

# ============================================================
# 数据结构
# ============================================================

@dataclass
class Shot:
    """单个镜头"""
    shot_id: int
    description: str                # 画面描述（文生图/视频提示词）
    characters: List[str]           # 本镜头出现的角色
    dialogue: str = ""              # 角色对话台词
    speaker: str = ""               # 说话的角色名
    narration: str = ""             # 旁白文本
    estimated_duration: float = 4.0 # 估算时长（秒）
    mood: str = "neutral"           # 情绪/氛围
    camera_movement: str = "static" # 运镜方式
    image_prompt: str = ""          # 文生图提示词（如启用SD生成）
    shot_type: str = ""             # 镜头类型：dialogue/action/empty


@dataclass
class Scene:
    """一个场景（含多个镜头）"""
    scene_id: int
    title: str                      # 场景标题
    location: str                   # 地点
    time: str                       # 时间段（日/夜/黄昏）
    atmosphere: str                 # 氛围（浪漫/紧张/悲伤/欢快）
    shots: List[Shot]               # 镜头列表
    summary: str = ""               # 场景摘要

    def total_duration(self) -> float:
        return sum(s.estimated_duration for s in self.shots)


@dataclass
class SceneScript:
    """完整分镜脚本"""
    title: str                      # 故事标题
    scenes: List[Scene]             # 场景列表
    characters: List[dict] = field(default_factory=list)  # 角色列表
    global_narration: str = ""      # 全局旁白

    def total_duration(self) -> float:
        return sum(s.total_duration() for s in self.scenes)


def infer_shot_type(shot: Shot) -> str:
    """
    推断镜头类型。

    优先使用分镜脚本标注的 shot_type；未标注时按对话/空镜/动作启发式兜底。
    """
    if shot.shot_type in ("dialogue", "action", "empty"):
        return shot.shot_type

    if shot.dialogue or shot.speaker:
        return "dialogue"
    if not shot.characters:
        return "empty"

    action_hints = (
        "奔跑", "打斗", "战斗", "追击", "追逐", "冲刺", "跳跃", "飞跃",
        "挥拳", "爆炸", "转身", "爆发", "闪避", "run", "fight", "jump",
        "chase", "attack", "explosion",
    )
    combined = f"{shot.description} {shot.image_prompt} {shot.camera_movement}"
    if any(hint in combined for hint in action_hints):
        return "action"
    if shot.camera_movement in ("推", "拉", "摇", "移", "跟", "升", "降"):
        return "action"
    return "dialogue"


# ============================================================
# LLM Prompt 模板
# ============================================================

SCENE_ANALYSIS_PROMPT = """你是一个专业的影视剧本分析专家。请分析以下小说片段，将其分解为适合制作AI漫剧视频的场次和镜头。

请严格按照以下JSON格式输出，不要包含其他内容：

```json
{{"title": "故事标题",
"characters": [
    {{"name": "角色名", "description": "外貌/性格描述", "voice_type": "青年男声/青年女声/老年男声/老年女声/童声"}}
],
"scenes": [
    {{
      "scene_id": 1,
      "title": "场景标题",
      "location": "地点",
      "time": "时间段（日/夜/黄昏/黎明）",
      "atmosphere": "氛围（浪漫/紧张/悲伤/欢快/平静/神秘）",
      "summary": "场景摘要",
      "shots": [
        {{
          "shot_id": 1,
          "description": "画面描述：详细描绘这个镜头中应该看到的画面，包括构图、颜色、角色动作等",
          "characters": ["角色名"],
          "dialogue": "如果该镜头有角色对话，填写对话内容，否则留空",
          "speaker": "说话的角色名，无对话则留空",
          "narration": "该镜头的旁白解说词",
          "estimated_duration": 4.0,
          "mood": "该镜头的情绪",
          "camera_movement": "运镜方式（推/拉/摇/移/跟/升/降/静态）",
          "shot_type": "dialogue/action/empty（对话/动作/空镜）",
          "image_prompt": "英文图像生成提示词，用于AI生成漫画风格画面，包含角色外貌、场景、构图、色调、风格的详细描述"
        }}
      ]
    }}
]
}}
```

要求：
1. 完整覆盖原文所有内容，不能遗漏剧情
2. 每个场景包含2-6个镜头
3. 对话台词必须保留原文，旁白根据叙事需要补充
4. image_prompt用英文，描述要包含详细场景、角色外貌、色彩、构图、艺术风格（anime style, comic style, cinematic lighting等）
5. 每个镜头时长3-8秒，动作场面可延长到5-10秒
6. 注意镜头间的逻辑连贯性和叙事节奏
7. shot_type必须填写：有人物对白或近景特写填dialogue，奔跑/打斗/情绪爆发/转场填action，无人物的场景远景填empty

小说原文：
{novel_text}
"""


def _strip_markdown_fence(text: str) -> str:
    """去除LLM输出中的markdown代码块标记"""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


class SceneSplitter:
    """
    场景分镜解析器：使用LLM将小说文本解析为结构化分镜脚本。

    复用 MoneyPrinterTurbo 的 LLM 调用基础设施，但使用更精细的 prompt
    来获取场景/镜头级别的结构化输出。
    """

    def __init__(self, llm_provider=None, model_name: str = None, api_key: str = None):
        """
        Args:
            llm_provider: LLM调用器，兼容MoneyPrinterTurbo的 llm.generate_script 风格
                         或自定义callable(text) -> str
            model_name: 模型名称（用于兼容非默认模型）
            api_key: API密钥
        """
        self.llm_provider = llm_provider
        self.model_name = model_name
        self.api_key = api_key

    def parse(self, novel_text: str) -> SceneScript:
        """
        解析小说文本，返回结构化分镜脚本。

        Args:
            novel_text: 小说文本

        Returns:
            SceneScript 对象
        """
        if not novel_text.strip():
            raise ValueError("小说文本不能为空")

        prompt = SCENE_ANALYSIS_PROMPT.format(novel_text=novel_text[:8000])

        raw_response = self._call_llm(prompt)

        # _call_llm 可能直接返回 SceneScript（当使用内置解析器时）
        if isinstance(raw_response, SceneScript):
            return raw_response

        parsed = self._parse_response(raw_response)
        return parsed

    def _call_llm(self, prompt: str) -> str:
        """调用LLM获取响应"""
        if self.llm_provider is None:
            # 使用内置的简单解析器作为降级（不依赖外部LLM API）
            return self._fallback_parse(prompt)

        if callable(self.llm_provider):
            return self.llm_provider(prompt)

        # 兼容MoneyPrinterTurbo的LLM服务
        try:
            from app.services import llm
            original_generate = llm.generate_script

            # 临时替换prompt，获取长文本响应
            response = llm._generate_response(prompt=prompt)
            return response
        except Exception as e:
            # 如果外部LLM不可用，使用内置解析器
            print(f"[SceneSplitter] LLM调用失败: {e}，使用内置解析器")
            return self._fallback_parse(prompt)

    def _parse_response(self, raw: str) -> SceneScript:
        """解析LLM的JSON响应为SceneScript对象"""
        cleaned = _strip_markdown_fence(raw)

        # 尝试提取JSON
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            # 尝试从文本中提取JSON
            match = re.search(r'\{.*\}', cleaned, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    raise ValueError("无法解析LLM返回的JSON格式")
            else:
                raise ValueError("LLM返回内容不包含有效JSON")

        scenes = []
        for scene_data in data.get("scenes", []):
            shots = []
            for shot_data in scene_data.get("shots", []):
                shot = Shot(
                    shot_id=shot_data.get("shot_id", 1),
                    description=shot_data.get("description", ""),
                    characters=shot_data.get("characters", []),
                    dialogue=shot_data.get("dialogue", ""),
                    speaker=shot_data.get("speaker", ""),
                    narration=shot_data.get("narration", ""),
                    estimated_duration=float(shot_data.get("estimated_duration", 4.0)),
                    mood=shot_data.get("mood", "neutral"),
                    camera_movement=shot_data.get("camera_movement", "static"),
                    image_prompt=shot_data.get("image_prompt", shot_data.get("description", "")),
                    shot_type=shot_data.get("shot_type", ""),
                )
                shots.append(shot)

            scene = Scene(
                scene_id=scene_data.get("scene_id", 1),
                title=scene_data.get("title", ""),
                location=scene_data.get("location", ""),
                time=scene_data.get("time", ""),
                atmosphere=scene_data.get("atmosphere", "neutral"),
                shots=shots,
                summary=scene_data.get("summary", ""),
            )
            scenes.append(scene)

        return SceneScript(
            title=data.get("title", "未命名故事"),
            scenes=scenes,
            characters=data.get("characters", []),
        )

    def _fallback_parse(self, prompt: str) -> SceneScript:
        """
        当LLM不可用时的降级解析逻辑。
        使用正则和启发式规则从文本中提取基本结构。
        """
        # 提取提示词中的原文
        novel_match = re.search(r'小说原文：\n(.+)', prompt, re.DOTALL)
        novel_text = novel_match.group(1).strip() if novel_match else ""

        if not novel_text:
            novel_text = prompt

        # 基本分段
        paragraphs = [p.strip() for p in novel_text.split('\n') if p.strip()]

        # 构建最低限度的分镜
        scenes = []
        chars = set()
        dialogue_pattern = re.compile(r'[""](.+?)[""]|[:：]\s*[""](.+?)[""]')

        for i, para in enumerate(paragraphs[:10]):
            if not para:
                continue

            # 检测可能的对话
            dialogues = dialogue_pattern.findall(para)
            shot_dialogues = []
            for d in dialogues:
                text = d[0] or d[1]
                if text.strip():
                    shot_dialogues.append(text.strip())

            # 检测角色名（简单启发式）
            para_chars = re.findall(r'[《》]?\w{2,4}(?:说|道|喊|叫|问|答|哼|叹)', para)
            for c in para_chars:
                name = c[:-1]
                chars.add(name)

            # 创建1-2个镜头
            shots = []
            shot1 = Shot(
                shot_id=1,
                description=para[:100] + ("..." if len(para) > 100 else ""),
                characters=list(chars)[-3:] if chars else ["人物"],
                dialogue=shot_dialogues[0] if shot_dialogues else "",
                speaker="",
                narration=para[:150],
                estimated_duration=5.0,
                mood="neutral",
                image_prompt=f"Anime style scene: {para[:80]}",
                shot_type="dialogue" if shot_dialogues else "",
            )
            shots.append(shot1)

            if len(para) > 200:
                shot2 = Shot(
                    shot_id=2,
                    description=para[100:200] + "...",
                    characters=list(chars)[-3:] if chars else ["人物"],
                    dialogue=shot_dialogues[1] if len(shot_dialogues) > 1 else "",
                    speaker="",
                    narration=para[100:200],
                    estimated_duration=4.0,
                    mood="neutral",
                    image_prompt=f"Anime style: {para[80:160]}",
                    shot_type="dialogue" if len(shot_dialogues) > 1 else "",
                )
                shots.append(shot2)

            scene = Scene(
                scene_id=i + 1,
                title=f"场景{i + 1}",
                location="未知",
                time="日",
                atmosphere="neutral",
                shots=shots,
                summary=para[:50],
            )
            scenes.append(scene)

        character_list = []
        for c in list(chars)[:6]:
            character_list.append({
                "name": c,
                "description": f"角色{c}",
                "voice_type": "青年男声"
            })

        return SceneScript(
            title="自动解析故事",
            scenes=scenes,
            characters=character_list,
        )

    def to_json(self, script: SceneScript, indent: int = 2) -> str:
        """将分镜脚本导出为JSON字符串"""
        def _shot_to_dict(s: Shot) -> dict:
            return {
                "shot_id": s.shot_id,
                "description": s.description,
                "characters": s.characters,
                "dialogue": s.dialogue,
                "speaker": s.speaker,
                "narration": s.narration,
                "estimated_duration": s.estimated_duration,
                "mood": s.mood,
                "camera_movement": s.camera_movement,
                "image_prompt": s.image_prompt,
                "shot_type": s.shot_type,
            }

        def _scene_to_dict(s: Scene) -> dict:
            return {
                "scene_id": s.scene_id,
                "title": s.title,
                "location": s.location,
                "time": s.time,
                "atmosphere": s.atmosphere,
                "summary": s.summary,
                "shots": [_shot_to_dict(sh) for sh in s.shots],
                "total_duration": s.total_duration(),
            }

        output = {
            "title": script.title,
            "total_duration": script.total_duration(),
            "characters": script.characters,
            "scenes": [_scene_to_dict(s) for s in script.scenes],
        }

        return json.dumps(output, ensure_ascii=False, indent=indent)

    def save_script(self, script: SceneScript, output_path: str):
        """将分镜脚本保存为JSON文件"""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(self.to_json(script))
        print(f"[SceneSplitter] 分镜脚本已保存: {output_path}")


# ============================================================
# CLI 测试入口
# ============================================================
if __name__ == "__main__":
    test_novel = (
        "在一个风雨交加的夜晚，年轻的侦探林墨独自站在废弃的钟楼顶端。"
        "雨水顺着他的黑色风衣滑落，他的目光紧锁着对面大楼里一闪而过的身影。\n\n"
        '"终于等到你了。"林墨低声说道，嘴角浮现出一丝笑意。\n\n'
        "他纵身一跃，在夜空中划过一道黑色的弧线。"
        "雨幕中，他准确地抓住了对面楼顶的边缘，一个翻身稳稳落地。\n\n"
        '"林墨，你还真是阴魂不散。"阴暗处传来一个沙哑的声音。\n\n'
        '"老K，你逃不掉的。三年前那起珠宝案，今天该有个了结了。"\n\n'
        "老K从阴影中走出，他穿着一件灰色的风衣，脸上带着一道狰狞的疤痕。"
        '"你以为就凭你一个人，能抓住我？"'
    )

    splitter = SceneSplitter()
    script = splitter.parse(test_novel)
    print(splitter.to_json(script))
