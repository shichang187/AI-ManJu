"""
Novel2ComicDrama - 小说自动转AI漫剧视频系统

核心工作流：
1. 小说文本 → scene_splitter → 结构化分镜脚本（含场次、镜头、台词、画面描述）
2. 角色管理 → character_manager → 角色一致性描述 + TTS声音映射
3. 导演工作台 → director_workspace → 协调资产生成、语音合成、画面生成
4. 视频合成 → timeline-based 多轨道视频组装 → 最终MP4

复用 MoneyPrinterTurbo 成熟组件：
- TTS引擎（edge-tts, Azure, Gemini等）
- 字幕生成（Whisper/Edge）
- BGM管理（随机/自定义/第三方API）
- MoviePy合成基础 + FFmpeg编解码
"""

__version__ = "0.6.0"
