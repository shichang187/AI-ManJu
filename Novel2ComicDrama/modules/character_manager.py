"""
character_manager.py — 角色与形象管理

核心功能：
1. 角色注册与描述管理（维护一致的视觉形象设定）
2. 角色-声音映射（TTS声音分配）
3. 角色参考图管理（用于文生图保持一致性）
4. 角色出场时间线追踪

这是保持漫剧角色视觉一致性的关键模块。
"""

import json
import os
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict

from .scene_splitter import SceneScript


@dataclass
class CharacterProfile:
    """角色完整档案"""
    name: str
    description: str                # 外貌特征（用于文生图）
    personality: str = ""           # 性格描述
    voice_type: str = "青年男声"     # 声音类型
    voice_name: str = ""            # 分配的TTS声音名称
    age_group: str = "成年"         # 年龄段
    gender: str = "男"              # 性别
    appearance_tags: str = ""       # 外貌标签（逗号分隔，用于提示词）
    clothing_style: str = ""        # 着装风格
    reference_image: str = ""       # 参考图路径（如果有）
    color_palette: str = ""         # 角色主色调

    # 内置一致的提示词片段，用于文生图时保持长相一致
    # 例如："young man, short black hair, sharp eyes, wearing a black trench coat"
    def get_appearance_prompt(self) -> str:
        """返回用于文生图的角色外貌描述"""
        parts = []
        if self.appearance_tags:
            parts.append(self.appearance_tags)
        if self.clothing_style:
            parts.append(self.clothing_style)
        if self.description:
            parts.append(self.description)

        return ", ".join(parts) if parts else self.description


# TTS声音类型 → 具体声音名称的映射
VOICE_MAPPING = {
    "青年男声": "zh-CN-YunxiNeural",
    "青年女声": "zh-CN-XiaoxiaoNeural",
    "老年男声": "zh-CN-YunyangNeural",
    "老年女声": "zh-CN-XiaoyiNeural",
    "童声": "zh-CN-XiaomengNeural",
    "旁白男声": "zh-CN-YunjianNeural",
    "旁白女声": "zh-CN-XiaochenNeural",
    # Azure V2 版本
    "青年男声_v2": "zh-CN-YunxiMultilingualNeural-V2",
    "青年女声_v2": "zh-CN-XiaoxiaoMultilingualNeural-V2",
    "旁白_v2": "zh-CN-XiaochenMultilingualNeural-V2",
}


class CharacterManager:
    """
    角色管理器。

    负责：
    - 从SceneScript自动提取和注册角色
    - 为角色分配TTS声音
    - 维护角色外观一致性的提示词
    - 导出角色配置供文生图模块使用
    """

    def __init__(self, output_dir: str = ""):
        self.characters: Dict[str, CharacterProfile] = {}
        self.output_dir = output_dir or "characters"

    def register_from_script(self, script: SceneScript):
        """
        从分镜脚本中自动注册角色。

        Args:
            script: SceneScript 分镜脚本对象
        """
        for char_data in script.characters:
            name = char_data.get("name", "")
            if not name:
                continue
            profile = CharacterProfile(
                name=name,
                description=char_data.get("description", ""),
                voice_type=char_data.get("voice_type", "青年男声"),
            )

            # 自动分配TTS声音
            profile.voice_name = self._assign_voice(profile.voice_type, name)

            # 为角色生成外观标签
            if not profile.appearance_tags:
                profile.appearance_tags = self._generate_appearance_tags(
                    name, profile.description, profile.gender
                )

            self.characters[name] = profile

        print(f"[CharacterManager] 已注册 {len(self.characters)} 个角色")

    def register_character(self, profile: CharacterProfile):
        """手动注册一个角色"""
        if not profile.voice_name:
            profile.voice_name = self._assign_voice(profile.voice_type, profile.name)
        self.characters[profile.name] = profile

    def get_voice_name(self, character_name: str) -> str:
        """获取某个角色的TTS声音名称"""
        char = self.characters.get(character_name)
        if char:
            return char.voice_name or self._assign_voice(char.voice_type, character_name)
        # 返回默认旁白声音
        return VOICE_MAPPING.get("旁白男声", "zh-CN-YunjianNeural")

    def get_appearance_prompt(self, character_name: str) -> str:
        """获取角色的外貌描述提示词"""
        char = self.characters.get(character_name)
        if char:
            return char.get_appearance_prompt()
        return character_name

    def get_scene_char_desc(self, character_names) -> str:
        """
        拼接某个镜头/场景内全部角色的固定外貌描述。

        Args:
            character_names: 角色名列表（也可传单个角色名）

        Returns:
            用于豆包视频提示词的统一角色描述，多个角色用中文分号分隔。
        """
        if isinstance(character_names, str):
            character_names = [character_names]

        parts = []
        for name in character_names or []:
            desc = self.get_appearance_prompt(name)
            if desc:
                parts.append(f"{name}：{desc}" if desc != name else name)
        return "；".join(parts)

    def get_all_voice_assignments(self) -> Dict[str, str]:
        """获取所有角色的声音分配映射"""
        return {
            name: self.get_voice_name(name)
            for name in self.characters
        }

    def get_narration_voice(self) -> str:
        """获取旁白声音"""
        return VOICE_MAPPING.get("旁白男声", "zh-CN-YunjianNeural")

    def _assign_voice(self, voice_type: str, character_name: str = "") -> str:
        """根据声音类型分配具体TTS声音名称"""
        voice_name = VOICE_MAPPING.get(voice_type)
        if voice_name:
            return voice_name
        # 默认为男性声音
        return "zh-CN-YunxiNeural"

    def _generate_appearance_tags(self, name: str, description: str, gender: str = "男") -> str:
        """
        根据角色描述生成外观标签。
        在实际应用中，这部分可以由LLM生成更精细的描述。
        """
        # 如果已经有详细描述，直接使用
        if description and len(description) > 5:
            return description

        # 基本的外观标签
        gender_tag = "male" if gender == "男" else "female"
        return f"a {gender_tag} character named {name}, anime style, detailed face, expressive eyes"

    def save_profiles(self, output_path: str = ""):
        """保存角色档案到JSON文件"""
        if not output_path:
            output_path = os.path.join(self.output_dir, "character_profiles.json")

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        profiles = {}
        for name, profile in self.characters.items():
            profiles[name] = asdict(profile)

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(profiles, f, ensure_ascii=False, indent=2)

        print(f"[CharacterManager] 角色档案已保存: {output_path}")

    def load_profiles(self, input_path: str):
        """从JSON文件加载角色档案"""
        with open(input_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        for name, profile_data in data.items():
            self.characters[name] = CharacterProfile(**profile_data)

    def summary(self) -> str:
        """输出角色概要"""
        lines = ["\n===== 角色配置摘要 ====="]
        for name, char in self.characters.items():
            lines.append(f"  {name}: {char.voice_name} [{char.voice_type}]")
        lines.append("=" * 24)
        return "\n".join(lines)


if __name__ == "__main__":
    # 测试
    from scene_splitter import SceneScript, Scene, Shot

    script = SceneScript(
        title="测试故事",
        scenes=[],
        characters=[
            {"name": "林墨", "description": "25岁青年侦探，黑色短发，锐利的眼神", "voice_type": "青年男声"},
            {"name": "老K", "description": "45岁罪犯，沙哑嗓音，脸上有疤痕", "voice_type": "老年男声"},
        ]
    )

    mgr = CharacterManager()
    mgr.register_from_script(script)
    print(mgr.summary())
    print(f"林墨的声音: {mgr.get_voice_name('林墨')}")
    print(f"旁白声音: {mgr.get_narration_voice()}")
