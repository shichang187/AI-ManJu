"""
director_workspace.py — 导演工作台（核心调度器）

职责：协调整个漫剧视频制作流程
1. 接收分镜脚本 + 角色配置
2. 为每个镜头生成图像/视频片段
3. 调用TTS生成旁白+角色对话
4. 协调时间线编排
5. 调用视频合成器输出最终视频

工作流：
  scene_script → [图像生成引擎] → 镜头画面
              → [TTS引擎] → 配音音频
              → [字幕引擎] → SRT字幕
              → [合成引擎] → 最终视频
"""

import json
import os
import math
import re
import base64
import mimetypes
import shutil
import time
import urllib.error
import urllib.request
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Callable, Any
from pathlib import Path

from .scene_splitter import SceneScript, Scene, Shot, infer_shot_type
from .character_manager import CharacterManager, VOICE_MAPPING

# ============================================================
# 豆包视频 2.0 提示词与参数配置
# ============================================================

# 在方舟控制台开通 Seedance 2.0 后，可用 --doubao-model / DOUBAO_MODEL
# 改成 2.0 模型名或接入点ID（ep-xxx）
DOUBAO_MODEL_DEFAULT = "doubao-seedance-1-0-pro-250528"

DOUBAO_GLOBAL_BASE_PROMPT = (
    "8K超高清，无损原图渲染，专业影视级降噪处理，画面纯净无颗粒杂点，"
    "无胶片噪点、雪花斑点、彩色杂色像素块，线条平滑强抗锯齿，无锯齿毛边，"
    "高HDR动态范围，色彩均匀柔和无断层，低压缩损耗，无马赛克模糊，"
    "画面干净通透，统一漫画上色质感，光影稳定不跳变，细腻高精度采样，"
    "消除帧间运动色斑、静电噪纹、老旧录像脏污纹理，边缘清晰不糊边，全程无随机杂光斑"
)

DOUBAO_NEGATIVE_PROMPT = (
    "胶片颗粒，画面噪点，雪花斑点，彩色杂点像素，静电噪纹，老旧录像噪点，"
    "马赛克，模糊糊边，锯齿线条，压缩失真，色彩断层，随机光斑，运动色块抖动，"
    "画面撕裂拖影，过度锐化噪点，低分辨率，脏污污渍，模糊五官，人物畸形，"
    "画风突变，变形扭曲，文字水印，多余杂物，虚化重影"
)

DOUBAO_SHOT_TEMPLATES = {
    "dialogue": (
        "二次元漫画静态分镜动态漫，严格遵循分镜脚本人物、服饰、场景、配色、五官特征，"
        "{char_desc}，不篡改画风；仅添加极小幅度微动：人物嘴唇随台词轻微开合、"
        "眼皮缓慢眨动、发丝微风轻飘、衣角布料自然垂落晃动，背景轻微空气流动；"
        "使用低强度Ken Burns呼吸运镜，镜头几乎固定，极缓慢小幅推拉平移，"
        "60fps流畅插帧，帧间过渡丝滑无撕裂、无拖影噪点，16:9标准漫剧画幅，"
        "光影全程恒定不变，动态幅度柔和不突兀，画面稳定无偏移抖动，"
        "匹配TTS配音台词时长，{scene_desc}"
    ),
    "action": (
        "二次元漫画动作分镜动态漫，完整保留原图人物姿态、构图、画风，{char_desc}；"
        "人物肢体运动连贯流畅，动作轨迹符合物理逻辑，无人物变形失真；"
        "搭配舒缓跟焦运镜，小幅跟随动作推拉镜头，60fps高帧率补帧，"
        "运动画面无拖影噪块、无色彩错乱杂斑；动态强度适中，不出现剧烈画面晃动，"
        "16:9影视画幅，明暗光影随剧情轻微变化但无噪点色块溢出，"
        "全程画面干净无颗粒，{scene_desc}"
    ),
    "empty": (
        "二次元漫画场景远景静态动态漫，完整还原分镜场景建筑、景物、环境配色，"
        "{scene_desc}；仅轻微环境动态：流云缓慢飘动、树叶轻晃、水面波纹微动；"
        "固定机位镜头，无大幅度运镜，极低强度微动，60fps顺滑帧过渡，"
        "画面无任何颗粒噪点、杂色斑点，整体氛围感统一柔和"
    ),
}

DOUBAO_EXTRA_DENOISE_PROMPT = (
    "全局深度降噪，消除所有细小杂色颗粒，画面纯色区域无杂点干扰"
)

DOUBAO_DEFAULT_VIDEO_PARAMS = {
    "fps": 60,
    "motion_strength": 0.15,
    "style_strength": 0.1,
    "denoise_enable": True,
    "resolution": "1080p",
    "ratio": "auto",
    "watermark": False,
    "use_frames": True,
}

DOUBAO_MOTION_STRENGTH = {
    "dialogue": 0.15,
    "action": 0.3,
    "empty": 0.1,
}

# ============================================================
# 图像生成抽象接口
# ============================================================

class ImageGenerator:
    """
    图像生成器抽象基类。

    实际使用时可以接入：
    - Stable Diffusion WebUI API (AUTOMATIC1111)
    - ComfyUI API
    - Midjourney / DALL-E API
    - 简单的PIL生成器（降级使用）
    """

    def generate(self, prompt: str, character_prompts: Dict[str, str] = None,
                 negative_prompt: str = "", width: int = 1080, height: int = 1920,
                 output_path: str = "") -> str:
        """
        根据提示词生成图像。

        Args:
            prompt: 图像描述提示词
            character_prompts: 角色外观描述映射 {角色名: 描述}
            negative_prompt: 负面提示词
            width, height: 图像尺寸
            output_path: 输出路径（为空则自动生成）

        Returns:
            生成的图像文件路径
        """
        raise NotImplementedError


class SDWebUIGenerator(ImageGenerator):
    """
    Stable Diffusion WebUI API 生成器。
    连接到本地或远程的 AUTOMATIC1111 WebUI。
    """

    def __init__(self, api_url: str = "http://127.0.0.1:7860",
                 default_negative: str = "nsfw, lowres, noise, grain, film grain, compression artifacts, bad anatomy, bad hands, text, error, extra fingers, missing fingers, blurry, ugly"):
        self.api_url = api_url.rstrip("/")
        self.default_negative = default_negative

    def generate(self, prompt: str, character_prompts: Dict[str, str] = None,
                 negative_prompt: str = "", width: int = 1080, height: int = 1920,
                 output_path: str = "") -> str:
        import requests
        import base64

        # 组合完整的提示词
        full_prompt = prompt
        if character_prompts:
            char_desc = ", ".join(character_prompts.values())
            if char_desc:
                full_prompt = f"{char_desc}, {prompt}"

        # 添加通用的漫画/动漫风格
        style_prompt = ("(masterpiece, best quality:1.2), anime style, comic style, cinematic lighting, "
                        "detailed background, clean lines, no noise, anti-aliasing, high detail")
        full_prompt = f"{full_prompt}, {style_prompt}"

        neg = negative_prompt or self.default_negative

        payload = {
            "prompt": full_prompt,
            "negative_prompt": neg,
            "width": width,
            "height": height,
            "steps": 20,
            "cfg_scale": 7,
            "sampler_name": "Euler a",
            "batch_size": 1,
        }

        try:
            response = requests.post(
                f"{self.api_url}/sdapi/v1/txt2img",
                json=payload,
                timeout=120,
            )
            result = response.json()

            if "images" in result and result["images"]:
                img_data = base64.b64decode(result["images"][0])
                if not output_path:
                    output_path = tempfile.mktemp(suffix=".png")
                with open(output_path, "wb") as f:
                    f.write(img_data)
                print(f"[SD] 图像已生成: {output_path}")
                return output_path
            else:
                raise RuntimeError(f"SD返回异常: {result}")
        except requests.exceptions.ConnectionError:
            print(f"[SD] 无法连接到 {self.api_url}，请确保SD WebUI已启动并添加 --api 参数")
            raise
        except Exception as e:
            print(f"[SD] 生成失败: {e}")
            raise


class FallbackPainter(ImageGenerator):
    """
    降级图像生成器：使用PIL生成简单的场景图像。
    当SD不可用时，至少能生成有视觉内容的场景。
    """

    def generate(self, prompt: str, character_prompts: Dict[str, str] = None,
                 negative_prompt: str = "", width: int = 1080, height: int = 1920,
                 output_path: str = "") -> str:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
        import random
        import hashlib

        # 根据prompt生成确定性seed，同一场景每次颜色一致
        seed = int(hashlib.md5(prompt.encode()).hexdigest()[:8], 16)
        random.seed(seed)

        img = Image.new("RGB", (width, height), (20, 20, 30))
        draw = ImageDraw.Draw(img)

        # --- 1. 绘制渐变背景 ---
        mood_colors = {
            "浪漫": [(255, 182, 193), (255, 105, 180)],
            "紧张": [(139, 0, 0), (0, 0, 0)],
            "悲伤": [(70, 70, 100), (30, 30, 50)],
            "欢快": [(255, 223, 128), (255, 182, 85)],
            "平静": [(135, 206, 235), (70, 130, 180)],
            "神秘": [(25, 25, 60), (5, 5, 30)],
            "恐怖": [(10, 10, 10), (60, 0, 0)],
            "温暖": [(255, 200, 150), (200, 120, 50)],
            "neutral": [(50, 50, 80), (20, 20, 40)],
        }

        # 从prompt中检测氛围关键词
        detected_mood = "neutral"
        for mood in mood_colors:
            if mood in prompt:
                detected_mood = mood
                break

        color_pair = mood_colors.get(detected_mood, mood_colors["neutral"])
        for y in range(height):
            ratio = y / height
            r = int(color_pair[0][0] * (1 - ratio) + color_pair[1][0] * ratio)
            g = int(color_pair[0][1] * (1 - ratio) + color_pair[1][1] * ratio)
            b = int(color_pair[0][2] * (1 - ratio) + color_pair[1][2] * ratio)
            draw.line([(0, y), (width, y)], fill=(r, g, b))

        # --- 2. 绘制场景元素（根据prompt关键词）---
        # 夜景
        if any(w in prompt for w in ["夜", "night", "dark", "月光", "moon"]):
            moon_radius = 40
            moon_x, moon_y = width // 2, height // 4
            draw.ellipse([moon_x - moon_radius, moon_y - moon_radius,
                         moon_x + moon_radius, moon_y + moon_radius],
                         fill=(220, 220, 200))
            # 星星
            for _ in range(30):
                sx = random.randint(0, width)
                sy = random.randint(0, height // 2)
                draw.point((sx, sy), fill=(255, 255, 200))

        # 建筑/城市
        if any(w in prompt for w in ["建筑", "城市", "楼", "钟楼", "building", "city", "tower"]):
            for bx in range(0, width, 80):
                bh = random.randint(100, 400)
                draw.rectangle([bx, height - bh, bx + 60, height],
                              fill=(random.randint(30, 60), random.randint(30, 60), random.randint(40, 80)))
                # 窗户
                for wy in range(height - bh + 20, height - 20, 30):
                    if random.random() > 0.3:
                        draw.rectangle([bx + 10, wy, bx + 25, wy + 15],
                                      fill=(random.choice([(255, 200, 100), (100, 150, 255)])))

        # 户外/自然
        if any(w in prompt for w in ["树", "forest", "tree", "自然", "森林"]):
            for _ in range(3):
                tx = random.randint(100, width - 100)
                # 树干
                draw.rectangle([tx - 5, height - 200, tx + 5, height],
                             fill=(80, 60, 40))
                # 树冠
                draw.ellipse([tx - 60, height - 280, tx + 60, height - 160],
                            fill=(random.randint(30, 80), random.randint(80, 160), random.randint(30, 60)))

        # --- 3. 绘制角色轮廓（简笔画风格，有角色提示时）---
        if character_prompts:
            char_index = 0
            for char_name, char_desc in character_prompts.items():
                cx = width // (len(character_prompts) + 1) * (char_index + 1)
                cy = height // 2 + 100

                # 简单的人形轮廓
                # 头部
                head_r = 30
                draw.ellipse([cx - head_r, cy - 100 - head_r,
                            cx + head_r, cy - 100 + head_r],
                           fill=(220, 180, 150))

                # 身体（取决于prompt中的颜色描述）
                body_color = (60, 60, 80)
                if "黑色" in prompt or "black" in prompt.lower():
                    body_color = (30, 30, 35)
                elif "白色" in prompt or "white" in prompt.lower():
                    body_color = (180, 180, 190)
                elif "灰色" in prompt or "gray" in prompt.lower() or "grey" in prompt.lower():
                    body_color = (100, 100, 110)

                draw.rectangle([cx - 25, cy - 70, cx + 25, cy + 50],
                             fill=body_color)

                char_index += 1

        # --- 4. 添加柔光效果 ---
        # 径向渐变（聚焦效果）
        center_x, center_y = width // 2, height // 3
        for r in range(max(width, height) // 2, 0, -5):
            alpha = int(20 * (1 - r / (max(width, height) // 2)))
            draw.ellipse([center_x - r, center_y - r, center_x + r, center_y + r],
                        outline=None, fill=None)

        # 轻微的高斯模糊效果模拟（通过颜色抖动）
        img = img.filter(ImageFilter.SMOOTH_MORE)
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.2)

        # 覆盖一层半透明的暗角
        vignette = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        v_draw = ImageDraw.Draw(vignette)
        for i in range(100):
            alpha = int(2 * (1 - i / 100))
            v_draw.ellipse([i * width // 200, i * height // 200,
                          width - i * width // 200, height - i * height // 200],
                         outline=(0, 0, 0, alpha))
        img = Image.alpha_composite(img.convert("RGBA"), vignette).convert("RGB")

        if not output_path:
            output_path = tempfile.mktemp(suffix=".png")
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        img.save(output_path, quality=95)
        print(f"[Painter] 场景图像已生成: {output_path}")
        return output_path


# ============================================================
# 豆包视频 2.0 图生视频
# ============================================================

class DoubaoVideoGenerator:
    """
    豆包 Seedance / 视频 2.0 图生视频生成器。

    调用火山方舟内容生成任务接口：
      POST /api/v3/contents/generations/tasks
    本地漫画静态图作为 image_url 参考图传入，替代 MoviePy Ken Burns 微动。

    方舟接口没有 negative_prompt / motion_strength / style_strength /
    denoise_enable 同名参数，代码按以下规则映射：
      - denoise_enable=True  -> 提示词头部追加深度降噪句
      - motion_strength      -> 对话/空镜 camera_fixed=True，动作镜头 False，
                                并按镜头类型选择默认动态强度
      - style_strength       -> 提示词强调保留参考图画风、不重绘
      - fps                  -> 换算为 frames = duration * fps
      - negative_prompt      -> 拼接到提示词尾部作为画面禁止项
    """

    def __init__(self, api_key: str = "", base_url: str = "",
                 model: str = "", video_params: Dict[str, Any] = None,
                 reference_image_url: str = "",
                 image_uploader: Callable[[str], str] = None,
                 poll_interval: float = 5.0, max_poll_time: float = 1800.0,
                 request_timeout: float = 120.0, max_retries: int = 2):
        self.api_key = api_key or os.getenv("DOUBAO_API_KEY") or os.getenv("ARK_API_KEY") or ""
        self.base_url = (
            base_url or os.getenv("DOUBAO_BASE_URL")
            or "https://ark.cn-beijing.volces.com/api/v3"
        ).rstrip("/")
        self.model = model or os.getenv("DOUBAO_MODEL") or DOUBAO_MODEL_DEFAULT
        self.video_params = {**DOUBAO_DEFAULT_VIDEO_PARAMS, **(video_params or {})}
        self.reference_image_url = (
            reference_image_url or os.getenv("DOUBAO_REFERENCE_IMAGE_URL", "")
        )
        self.image_uploader = image_uploader
        self.poll_interval = max(1.0, float(poll_interval))
        self.max_poll_time = max(30.0, float(max_poll_time))
        self.request_timeout = max(5.0, float(request_timeout))
        self.max_retries = max(0, int(max_retries))

    def is_available(self) -> bool:
        """豆包接口是否可用（必须配置 API Key）"""
        return bool(self.api_key)

    def build_shot_prompt(self, shot: Shot, scene: Scene = None,
                          character_manager: CharacterManager = None,
                          char_desc: str = "", scene_desc: str = "") -> str:
        """
        按镜头类型拼接全局基础提示词 + 分镜模板 + 角色 + 画面描述。
        """
        shot_type = infer_shot_type(shot)
        if not char_desc:
            if character_manager is not None:
                char_desc = character_manager.get_scene_char_desc(shot.characters)
            else:
                char_desc = "、".join(shot.characters)

        if not scene_desc:
            scene_desc = shot.description or shot.image_prompt
            if not scene_desc and scene is not None:
                scene_desc = scene.summary

        body = DOUBAO_SHOT_TEMPLATES[shot_type].format(
            char_desc=char_desc,
            scene_desc=scene_desc,
        )
        body = body.replace("，，", "，").replace("；，", "；").replace("，；", "；")
        prompt = f"{DOUBAO_GLOBAL_BASE_PROMPT}\n{body}"
        if self.video_params.get("denoise_enable", True):
            prompt = f"{DOUBAO_EXTRA_DENOISE_PROMPT}\n{prompt}"
        return prompt

    def generate_clip(self, image_path: str, prompt: str,
                      negative_prompt: str = "", output_path: str = "",
                      duration: float = 5.0, shot_type: str = "dialogue",
                      video_params: Dict[str, Any] = None,
                      seed: int = None, callback_url: str = "") -> str:
        """
        提交单镜头图生视频任务并下载成片。

        Returns:
            生成的视频文件路径
        """
        params = {**self.video_params, **(video_params or {})}
        fps = int(params.get("fps", 60))
        motion_strength = float(
            params.get("motion_strength", DOUBAO_MOTION_STRENGTH.get(shot_type, 0.15))
        )
        style_strength = float(params.get("style_strength", 0.1))
        denoise_enable = bool(params.get("denoise_enable", True))

        if denoise_enable and DOUBAO_EXTRA_DENOISE_PROMPT not in prompt:
            prompt = f"{DOUBAO_EXTRA_DENOISE_PROMPT}\n{prompt}"
        if style_strength > 0:
            prompt = (
                f"{prompt}\n画风严格保持参考图风格，风格偏移强度控制在"
                f"{style_strength}以内，不重绘不换画风"
            )
        if negative_prompt:
            prompt = f"{prompt}\n画面中严禁出现：{negative_prompt}"

        if not output_path:
            output_path = tempfile.mktemp(suffix=".mp4")
        duration = min(max(int(round(float(duration))), 1), 15)

        payload = self._build_payload(
            image_path=image_path,
            prompt=prompt,
            duration=duration,
            shot_type=shot_type,
            params=params,
            seed=seed,
            callback_url=callback_url,
            fps=fps,
            motion_strength=motion_strength,
        )
        print(
            f"    [豆包] 提交镜头任务: model={self.model}, "
            f"duration={duration}s, shot_type={shot_type}"
        )
        task_id = self._create_task(payload)
        print(f"    [豆包] 任务ID: {task_id}，等待云端生成...")

        result = self._wait_task(task_id)
        video_url = (
            (result.get("content") or {}).get("video_url")
            or result.get("video_url")
            or ""
        )
        if not video_url:
            raise RuntimeError(f"豆包任务成功但未返回视频地址: {result}")

        self._download_video(video_url, output_path)
        print(f"    [豆包] 视频片段已生成: {output_path}")
        return output_path

    def _build_payload(self, image_path: str, prompt: str, duration: int,
                       shot_type: str, params: Dict[str, Any],
                       seed: int = None, callback_url: str = "",
                       fps: int = 60, motion_strength: float = 0.15) -> dict:
        """把高层的豆包视频参数转换为方舟内容生成任务请求体"""
        content = [{"type": "text", "text": prompt}]
        image_url = self._resolve_image_url(image_path)
        if image_url:
            content.append({
                "type": "image_url",
                "image_url": {"url": image_url},
                "role": "user",
            })

        payload = {
            "model": self.model,
            "content": content,
            "duration": duration,
            "watermark": bool(params.get("watermark", False)),
            "camera_fixed": self._camera_fixed(shot_type, motion_strength),
            "resolution": params.get("resolution", "1080p"),
            "seed": seed,
        }
        if callback_url:
            payload["callback_url"] = callback_url

        ratio = params.get("ratio", "auto")
        if ratio == "auto":
            ratio = self._detect_ratio(image_path)
        if ratio:
            payload["ratio"] = ratio

        frames = self._compute_frames(duration, fps, params.get("use_frames", True))
        if frames:
            payload["frames"] = frames
        return payload

    def _camera_fixed(self, shot_type: str, motion_strength: float) -> bool:
        """对话/空镜锁机位，动作镜头允许跟随运镜"""
        return shot_type in ("dialogue", "empty") or motion_strength <= 0.2

    def _compute_frames(self, duration: int, fps: int, use_frames: bool) -> int:
        if not use_frames or fps <= 0:
            return None
        return int(round(duration * fps))

    def _detect_ratio(self, image_path: str) -> str:
        """按参考图宽高自动选择画幅，缺省按16:9"""
        if image_path and os.path.exists(image_path):
            try:
                from PIL import Image
                with Image.open(image_path) as im:
                    width, height = im.size
                return "16:9" if width >= height else "9:16"
            except Exception:
                pass
        return "16:9"

    def _resolve_image_url(self, image_path: str) -> str:
        """
        本地参考图转 data URL；也可通过 image_uploader 或
        DOUBAO_REFERENCE_IMAGE_URL（需含 {path} 或 {filename}）提供公网地址。
        """
        if not image_path:
            return ""
        image_path = str(image_path)
        if image_path.startswith(("http://", "https://", "data:image")):
            return image_path
        if not os.path.exists(image_path):
            raise FileNotFoundError(f"参考图不存在: {image_path}")

        if self.image_uploader is not None:
            return self.image_uploader(image_path)

        if self.reference_image_url:
            if "{path}" in self.reference_image_url or "{filename}" in self.reference_image_url:
                return self.reference_image_url.format(
                    path=image_path.replace("\\", "/"),
                    filename=os.path.basename(image_path),
                )
            raise RuntimeError(
                "DOUBAO_REFERENCE_IMAGE_URL 需要包含 {path} 或 {filename}，"
                "或通过 image_uploader 提供公网图床"
            )

        with open(image_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("ascii")
        mime = mimetypes.guess_type(image_path)[0] or "image/png"
        return f"data:{mime};base64,{encoded}"

    def _create_task(self, payload: dict) -> str:
        url = f"{self.base_url}/contents/generations/tasks"
        try:
            data = self._request_json("POST", url, payload)
        except RuntimeError as e:
            message = str(e).lower()
            if payload.get("frames") and any(
                key in message for key in ("frames", "fps", "param")
            ):
                print("    [豆包] 模型不支持60fps帧数参数，自动回退到模型默认帧率")
                payload = dict(payload)
                payload.pop("frames", None)
                data = self._request_json("POST", url, payload)
            else:
                raise

        task_id = data.get("id") or data.get("task_id")
        if not task_id:
            raise RuntimeError(f"豆包接口未返回任务ID: {data}")
        return task_id

    def _wait_task(self, task_id: str) -> dict:
        url = f"{self.base_url}/contents/generations/tasks/{task_id}"
        start = time.time()
        while time.time() - start < self.max_poll_time:
            data = self._request_json("GET", url)
            status = (data.get("status") or "").lower()
            if status in ("succeeded", "completed", "success"):
                return data
            if status in ("failed", "cancelled", "canceled"):
                error = data.get("error") or {}
                message = error.get("message") if isinstance(error, dict) else error
                raise RuntimeError(f"豆包视频任务失败: {message or data}")
            time.sleep(self.poll_interval)
        raise TimeoutError(
            f"豆包视频任务超时（>{self.max_poll_time:.0f}s）: {task_id}"
        )

    def _request_json(self, method: str, url: str, payload: dict = None) -> dict:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Novel2ComicDrama/0.2",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url, data=body, method=method, headers=headers
        )

        last_error = ""
        for attempt in range(self.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.request_timeout) as resp:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8", errors="replace")
                last_error = f"HTTP {e.code}: {raw[:500]}"
                if e.code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise RuntimeError(f"豆包接口请求失败: {last_error}")
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_error = str(e)
                if attempt < self.max_retries:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise RuntimeError(f"豆包接口网络错误: {last_error}")
        raise RuntimeError(f"豆包接口请求失败: {last_error}")

    def _download_video(self, video_url: str, output_path: str,
                        timeout: float = 600.0) -> None:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        request = urllib.request.Request(
            video_url, headers={"User-Agent": "Novel2ComicDrama/0.2"}
        )
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            with open(output_path, "wb") as f:
                shutil.copyfileobj(resp, f)
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError(f"视频下载失败或文件为空: {output_path}")


# ============================================================
# TTS 调度器
# ============================================================

class TTSScheduler:
    """
    TTS调度器，复用MoneyPrinterTurbo的TTS能力。

    为旁白和每个角色分配各自的TTS声音，生成音频片段。
    """

    def __init__(self, output_dir: str = "output/audio", voice_map: Dict[str, str] = None):
        self.output_dir = output_dir
        self.voice_map = voice_map or {}

    def synthesize_narration(self, text: str, voice_name: str = "",
                             output_path: str = "") -> str:
        """
        合成旁白音频。

        Args:
            text: 旁白文本
            voice_name: TTS声音名称
            output_path: 输出文件路径

        Returns:
            音频文件路径
        """
        if not voice_name:
            voice_name = "zh-CN-YunjianNeural"

        if not output_path:
            os.makedirs(self.output_dir, exist_ok=True)
            output_path = os.path.join(self.output_dir, f"narration_{hash(text) % 10000}.mp3")

        return self._call_tts(text, voice_name, output_path)

    def synthesize_dialogue(self, text: str, voice_name: str,
                            output_path: str = "") -> str:
        """
        合成角色对话音频。

        Args:
            text: 对话文本
            voice_name: 角色TTS声音名称
            output_path: 输出文件路径

        Returns:
            音频文件路径
        """
        if not output_path:
            os.makedirs(self.output_dir, exist_ok=True)
            output_path = os.path.join(self.output_dir, f"dialogue_{hash(text) % 10000}.mp3")

        return self._call_tts(text, voice_name, output_path)

    def _call_tts(self, text: str, voice_name: str, output_path: str) -> str:
        """
        调用TTS引擎。优先使用MoneyPrinterTurbo的voice模块。
        如果不可用，使用edge-tts直接调用。
        """
        try:
            # 尝试1：使用MoneyPrinterTurbo的TTS服务
            import sys
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../MoneyPrinterTurbo"))
            from app.services import voice as mpt_voice

            sub_maker = mpt_voice.tts(
                text=text,
                voice_name=voice_name,
                voice_rate=1.0,
                voice_file=output_path,
            )
            if sub_maker and os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                return output_path

            # 如果上面没成功，降级到edge-tts
            raise ImportError("MPT TTS returned empty")
        except (ImportError, Exception) as e:
            # 尝试2：直接使用edge-tts
            return self._edge_tts_fallback(text, voice_name, output_path)

    def _edge_tts_fallback(self, text: str, voice_name: str, output_path: str) -> str:
        """使用edge-tts作为降级方案"""
        import asyncio
        import edge_tts

        # 映射到edge-tts的声音名称
        edge_voice_map = {
            "zh-CN-XiaoxiaoNeural": "zh-CN-XiaoxiaoNeural",
            "zh-CN-YunxiNeural": "zh-CN-YunxiNeural",
            "zh-CN-YunjianNeural": "zh-CN-YunjianNeural",
            "zh-CN-XiaoyiNeural": "zh-CN-XiaoyiNeural",
            "zh-CN-YunyangNeural": "zh-CN-YunyangNeural",
            "zh-CN-XiaomengNeural": "zh-CN-XiaomengNeural",
            "zh-CN-XiaochenNeural": "zh-CN-XiaochenNeural",
        }

        edge_name = edge_voice_map.get(voice_name, "zh-CN-YunxiNeural")

        async def _do():
            communicate = edge_tts.Communicate(text, edge_name)
            await communicate.save(output_path)

        asyncio.run(_do())

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return output_path
        else:
            raise RuntimeError(f"edge-tts 合成失败: {output_path}")

    def get_audio_duration(self, audio_path: str) -> float:
        """获取音频文件时长（秒）"""
        try:
            from moviepy import AudioFileClip
            clip = AudioFileClip(audio_path)
            dur = clip.duration
            clip.close()
            return dur
        except Exception:
            # 使用ffprobe获取时长
            try:
                result = subprocess.run(
                    ["ffprobe", "-v", "error", "-show_entries",
                     "format=duration", "-of", "default=noprint_wrappers=1:nokey=1",
                     audio_path],
                    capture_output=True, text=True, timeout=10
                )
                return float(result.stdout.strip())
            except Exception:
                return 3.0


# ============================================================
# 导演工作台
# ============================================================

@dataclass
class ShotAsset:
    """一个镜头的所有资产"""
    shot: Shot
    scene_id: int
    image_path: str = ""
    narration_audio: str = ""
    dialogue_audio: str = ""
    combined_audio: str = ""
    video_path: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    subtitles: List[dict] = field(default_factory=list)


class DirectorWorkspace:
    """
    导演工作台 - 核心调度器。

    协调整个视频制作流程：
    1. 接收场景脚本
    2. 角色声音分配
    3. 逐镜头生成画面 + 配音
    4. 时间线编排
    5. 输出给视频合成器
    """

    def __init__(self, output_dir: str = "output",
                 image_generator: ImageGenerator = None,
                 tts_scheduler: TTSScheduler = None,
                 char_manager: CharacterManager = None,
                 video_generator: DoubaoVideoGenerator = None):
        """
        Args:
            output_dir: 输出目录
            image_generator: 图像生成器（不提供则使用FallbackPainter）
            tts_scheduler: TTS调度器
            char_manager: 角色管理器
            video_generator: 豆包视频生成器（提供后替代本地Ken Burns微动）
        """
        self.output_dir = output_dir
        self.image_gen = image_generator or FallbackPainter()
        self.tts = tts_scheduler or TTSScheduler(output_dir=os.path.join(output_dir, "audio"))
        self.char_mgr = char_manager or CharacterManager()
        self.video_gen = video_generator

        # 创建子目录
        self.images_dir = os.path.join(output_dir, "images")
        self.audio_dir = os.path.join(output_dir, "audio")
        self.video_dir = os.path.join(output_dir, "clips")
        for d in [self.images_dir, self.audio_dir, self.video_dir]:
            os.makedirs(d, exist_ok=True)

        self.shot_assets: List[ShotAsset] = []

    def produce(self, script: SceneScript, parallel: bool = False) -> List[ShotAsset]:
        """
        执行完整的视频制作流程。

        Args:
            script: 分镜脚本
            parallel: 是否并行处理镜头（默认串行）

        Returns:
            镜头资产列表
        """
        print("\n" + "=" * 60)
        print(f"开始制作漫剧: {script.title}")
        print(f"场景数: {len(script.scenes)}")
        print(f"总镜头数: {sum(len(s.shots) for s in script.scenes)}")
        print(f"预估时长: {script.total_duration():.1f}秒")
        print("=" * 60)

        # 注册角色
        self.char_mgr.register_from_script(script)
        print(self.char_mgr.summary())

        # 逐场景逐镜头生成
        asset_id = 0
        for scene in script.scenes:
            print(f"\n--- 场景 {scene.scene_id}: {scene.title} ({scene.atmosphere}) ---")
            for shot in scene.shots:
                asset_id += 1
                print(f"  镜头 {asset_id}: {shot.description[:50]}...")

                asset = self._produce_shot(asset_id, scene, shot)
                self.shot_assets.append(asset)

        print(f"\n[导演] 制作完成, 共产生 {len(self.shot_assets)} 个镜头资产")
        return self.shot_assets

    def _produce_shot(self, asset_id: int, scene: Scene, shot: Shot) -> ShotAsset:
        """处理单个镜头的所有资产生成"""
        asset = ShotAsset(shot=shot, scene_id=scene.scene_id)

        # 1. 生成画面
        image_path = os.path.join(self.images_dir, f"shot_{asset_id:04d}.png")
        try:
            # 获取角色外观描述
            char_prompts = {}
            for char_name in shot.characters:
                desc = self.char_mgr.get_appearance_prompt(char_name)
                if desc:
                    char_prompts[char_name] = desc

            image_path = self.image_gen.generate(
                prompt=shot.image_prompt or shot.description,
                character_prompts=char_prompts if char_prompts else None,
                width=1080,
                height=1920,
                output_path=image_path,
            )
        except Exception as e:
            print(f"    [警告] 图像生成失败: {e}，使用纯色背景")
            image_path = ""

        asset.image_path = image_path

        # 2. 合成配音
        # 2a. 旁白
        if shot.narration:
            nar_path = os.path.join(self.audio_dir, f"nar_{asset_id:04d}.mp3")
            try:
                self.tts.synthesize_narration(
                    shot.narration,
                    voice_name=self.char_mgr.get_narration_voice(),
                    output_path=nar_path,
                )
                asset.narration_audio = nar_path
            except Exception as e:
                print(f"    [警告] 旁白TTS失败: {e}")

        # 2b. 对话
        if shot.dialogue and shot.speaker:
            dia_path = os.path.join(self.audio_dir, f"dia_{asset_id:04d}.mp3")
            try:
                voice = self.char_mgr.get_voice_name(shot.speaker)
                self.tts.synthesize_dialogue(
                    shot.dialogue,
                    voice_name=voice,
                    output_path=dia_path,
                )
                asset.dialogue_audio = dia_path
            except Exception as e:
                print(f"    [警告] 对话TTS失败: {e}")

        # 3. 合并音频（旁白+对话）
        if asset.narration_audio or asset.dialogue_audio:
            combined_path = os.path.join(self.audio_dir, f"comb_{asset_id:04d}.mp3")
            self._merge_audio(asset, combined_path)
            asset.combined_audio = combined_path

        # 4. 生成视频片段
        video_path = os.path.join(self.video_dir, f"clip_{asset_id:04d}.mp4")
        asset.video_path = self._create_clip(asset, video_path, shot.estimated_duration, scene=scene)

        print(f"    [OK] 镜头{asset_id} 完成 (时长: {shot.estimated_duration}s)")
        return asset

    def _merge_audio(self, asset: ShotAsset, output_path: str):
        """合并旁白和对话音频"""
        from moviepy import AudioFileClip, CompositeAudioClip, afx

        clips = []
        if asset.narration_audio and os.path.exists(asset.narration_audio):
            try:
                clips.append(AudioFileClip(asset.narration_audio))
            except Exception:
                pass

        if asset.dialogue_audio and os.path.exists(asset.dialogue_audio):
            try:
                dia = AudioFileClip(asset.dialogue_audio)
                # 对话音量稍高于旁白
                dia = dia.with_effects([afx.MultiplyVolume(1.3)])
                clips.append(dia)
            except Exception:
                pass

        if not clips:
            return

        if len(clips) == 1:
            clips[0].write_audiofile(output_path, logger=None)
            clips[0].close()
        else:
            composite = CompositeAudioClip(clips)
            composite.write_audiofile(output_path, logger=None)
            composite.close()
            for c in clips:
                c.close()

    def _create_clip(self, asset: ShotAsset, output_path: str, duration: float,
                     scene: Scene = None) -> str:
        """
        生成单镜头视频片段。

        配置了豆包视频生成器时，以静态漫画图为参考图走云端图生视频；
        失败或未配置时回退到本地 MoviePy Ken Burns 微动。
        """
        if self.video_gen is not None:
            try:
                if not self.video_gen.is_available():
                    raise RuntimeError("未配置 DOUBAO_API_KEY / ARK_API_KEY")
                if not asset.image_path or not os.path.exists(asset.image_path):
                    raise RuntimeError("缺少静态漫画参考图，无法走豆包图生视频")

                shot = asset.shot
                shot_type = infer_shot_type(shot)
                prompt = self.video_gen.build_shot_prompt(
                    shot, scene=scene, character_manager=self.char_mgr
                )
                return self.video_gen.generate_clip(
                    image_path=asset.image_path,
                    prompt=prompt,
                    negative_prompt=DOUBAO_NEGATIVE_PROMPT,
                    output_path=output_path,
                    duration=duration,
                    shot_type=shot_type,
                    video_params={
                        "motion_strength": DOUBAO_MOTION_STRENGTH.get(shot_type, 0.15)
                    },
                )
            except Exception as e:
                print(f"    [警告] 豆包视频生成失败: {e}，回退本地 Ken Burns")
                return self._create_ken_burns_clip(asset, output_path, duration)
        return self._create_ken_burns_clip(asset, output_path, duration)

    def _create_ken_burns_clip(self, asset: ShotAsset, output_path: str,
                               duration: float) -> str:
        """从图像和音频创建本地 Ken Burns 视频片段（豆包不可用时的回退）"""
        from moviepy import (
            ImageClip, AudioFileClip, CompositeVideoClip,
            ColorClip, VideoFileClip
        )
        from moviepy.video.fx import Resize

        # 创建画面
        if asset.image_path and os.path.exists(asset.image_path):
            img_clip = ImageClip(asset.image_path)
        else:
            # 降级：使用场景氛围色的纯色背景
            import random
            r, g, b = random.randint(20, 60), random.randint(20, 60), random.randint(40, 80)
            img_clip = ColorClip(size=(1080, 1920), color=(r, g, b))

        # 确定实际时长
        if asset.combined_audio and os.path.exists(asset.combined_audio):
            try:
                audio = AudioFileClip(asset.combined_audio)
                actual_duration = max(duration, audio.duration)
                img_clip = img_clip.with_duration(actual_duration).with_audio(audio)
            except Exception:
                img_clip = img_clip.with_duration(duration)
        else:
            img_clip = img_clip.with_duration(duration)

        # 添加微动效果（Ken Burns 效果）
        try:
            img_clip = img_clip.with_effects([Resize(lambda t: 1 + 0.03 * (t / img_clip.duration))])
        except Exception:
            pass

        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # 写入临时文件
        if img_clip.audio is not None:
            img_clip.write_videofile(
                output_path,
                fps=24,
                codec="libx264",
                audio_codec="aac",
                logger=None,
            )
        else:
            img_clip.write_videofile(
                output_path,
                fps=24,
                codec="libx264",
                logger=None,
            )

        img_clip.close()
        return output_path

    def get_timeline(self) -> List[dict]:
        """
        生成时间线数据，供video_composer使用。

        Returns:
            timeline: [{start, end, video_path, audio_path, subtitle_text, ...}, ...]
        """
        timeline = []
        current_time = 0.0

        for asset in self.shot_assets:
            try:
                from moviepy import VideoFileClip
                clip = VideoFileClip(asset.video_path)
                dur = clip.duration
                clip.close()
            except Exception:
                dur = asset.shot.estimated_duration

            entry = {
                "start_time": current_time,
                "end_time": current_time + dur,
                "duration": dur,
                "video_path": asset.video_path,
                "audio_path": asset.combined_audio if (asset.combined_audio and os.path.exists(asset.combined_audio)) else "",
                "scene_id": asset.scene_id,
                "shot_id": asset.shot.shot_id,
                "narration": asset.shot.narration,
                "dialogue": asset.shot.dialogue,
                "speaker": asset.shot.speaker,
                "mood": asset.shot.mood,
                "description": asset.shot.description,
            }
            timeline.append(entry)
            current_time += dur

        return timeline

    def save_timeline(self, timeline: List[dict], output_path: str = ""):
        """保存时间线数据"""
        if not output_path:
            output_path = os.path.join(self.output_dir, "timeline.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(timeline, f, ensure_ascii=False, indent=2)
        print(f"[导演] 时间线已保存: {output_path}")
