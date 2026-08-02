"""
video_composer.py — 基于时间线的视频合成引擎

功能：
1. 接收导演工作台的时间线数据
2. 将所有片段按时间线拼合
3. 添加背景音乐
4. 生成对话/旁白字幕（SRT）
5. 添加转场效果
6. 输出最终视频

复用 MoneyPrinterTurbo 的视频合成基础设施：
- MoviePy CompositeVideoClip 多轨道合成
- FFmpeg concat 高效拼接
- 字幕（SRT）引擎
"""

import json
import os
import math
import subprocess
import tempfile
from typing import List, Dict, Optional

from .scene_splitter import SceneScript


class SubtitleBuilder:
    """
    字幕构建器。
    生成SRT字幕文件，支持旁白和对话的区分显示。
    """

    def __init__(self):
        self.entries = []  # [{start, end, text, type}]

    def add_entry(self, start_time: float, end_time: float, text: str,
                  entry_type: str = "narration"):
        """添加一个字幕条目"""
        if text and text.strip():
            self.entries.append({
                "start": start_time,
                "end": end_time,
                "text": text.strip(),
                "type": entry_type
            })

    def add_timeline_entries(self, timeline: List[dict]):
        """从时间线批量添加字幕条目"""
        for entry in timeline:
            # 对话字幕
            if entry.get("dialogue") and entry.get("speaker"):
                dialogue_text = f"{entry['speaker']}: {entry['dialogue']}"
                self.add_entry(
                    start_time=entry["start_time"],
                    end_time=entry["end_time"],
                    text=dialogue_text,
                    entry_type="dialogue"
                )
            # 旁白字幕（如果无对话）
            elif entry.get("narration") and not entry.get("dialogue"):
                self.add_entry(
                    start_time=entry["start_time"],
                    end_time=entry["end_time"],
                    text=entry["narration"],
                    entry_type="narration"
                )

        # 按时间排序
        self.entries.sort(key=lambda x: x["start"])

    def to_srt(self) -> str:
        """生成SRT格式字幕字符串"""
        lines = []
        for i, entry in enumerate(self.entries, 1):
            start_srt = self._format_time(entry["start"])
            end_srt = self._format_time(entry["end"])
            lines.append(str(i))
            lines.append(f"{start_srt} --> {end_srt}")
            lines.append(entry["text"])
            lines.append("")
        return "\n".join(lines)

    def save(self, output_path: str):
        """保存SRT文件"""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(self.to_srt())
        print(f"[字幕] SRT文件已保存: {output_path}")

    def _format_time(self, seconds: float) -> str:
        """将秒数转换为SRT时间格式 HH:MM:SS,mmm"""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        millis = int((secs - int(secs)) * 1000)
        return f"{hours:02d}:{minutes:02d}:{int(secs):02d},{millis:03d}"


class VideoComposer:
    """
    视频合成引擎。

    从时间线数据和各个资产文件合成最终视频。
    支持背景音乐、字幕叠加和转场效果。
    """

    def __init__(self, output_dir: str = "output",
                 bgm_dir: str = "",
                 font_path: str = ""):
        self.output_dir = output_dir
        self.bgm_dir = bgm_dir
        self.font_path = font_path

        self.clips_dir = os.path.join(output_dir, "clips")
        self.final_dir = os.path.join(output_dir, "final")
        os.makedirs(self.final_dir, exist_ok=True)

        # 尝试检测系统字体
        if not font_path:
            self.font_path = self._detect_font()

    def compose(self, timeline: List[dict],
                output_path: str = "",
                video_width: int = 1080,
                video_height: int = 1920,
                fps: int = 24,
                bgm_path: str = "",
                bgm_volume: float = 0.15,
                preserve_source_audio: bool = False) -> str:
        """
        合成最终视频。

        Args:
            timeline: 时间线数据 [{start_time, end_time, video_path, ...}]
            output_path: 输出视频路径
            video_width, video_height: 视频分辨率
            fps: 帧率
            bgm_path: 背景音乐路径（可选）
            bgm_volume: 背景音乐音量
            preserve_source_audio: 保留源视频自带音轨（浏览器豆包视频）

        Returns:
            最终视频文件路径
        """
        if not output_path:
            output_path = os.path.join(self.final_dir, "final_video.mp4")

        print(f"\n[合成器] 开始合成最终视频...")
        print(f"[合成器] 轨道数: {len(timeline)}")
        print(f"[合成器] 总时长: {timeline[-1]['end_time']:.1f}s" if timeline else "0s")

        # 1. 生成字幕
        subtitle_path = ""
        subtitle_builder = SubtitleBuilder()
        subtitle_builder.add_timeline_entries(timeline)
        if subtitle_builder.entries:
            subtitle_path = os.path.join(self.output_dir, "final", "subtitles.srt")
            subtitle_builder.save(subtitle_path)

        # 2. 使用FFmpeg进行精确合成（比MoviePy高效且精确）
        # 准备concat文件列表
        concat_file = os.path.join(self.output_dir, "final", "concat_list.txt")

        clip_index = 0
        clip_paths = []
        temp_clips = []

        for entry in timeline:
            clip_index += 1
            video_path = entry.get("video_path", "")
            if not video_path or not os.path.exists(video_path):
                print(f"  [警告] 镜头{clip_index} 视频缺失，跳过")
                continue
            clip_paths.append(video_path)

        # 写入concat文件
        with open(concat_file, "w", encoding="utf-8") as f:
            for path in clip_paths:
                escaped = path.replace("'", "'\\''").replace("\\", "/")
                f.write(f"file '{escaped}'\n")

        # 第一轮：concat所有视频片段
        concat_name = "_concat_with_audio.mp4" if preserve_source_audio else "_concat_no_audio.mp4"
        concat_output = os.path.join(self.output_dir, "final", concat_name)
        self._run_ffmpeg_concat(concat_file, concat_output, keep_audio=preserve_source_audio)

        # 第二轮：合成主音频轨道
        audio_sources = []
        for entry in timeline:
            audio_path = entry.get("audio_path", "")
            if audio_path and os.path.exists(audio_path):
                start = entry["start_time"]
                dur = entry["end_time"] - entry["start_time"]
                audio_sources.append((audio_path, start, dur))

        # 创建混合音频；保留源视频音轨时不再生成静音轨道
        mixed_audio = ""
        if not preserve_source_audio:
            mixed_audio = os.path.join(self.output_dir, "final", "_mixed_audio.aac")
            self._mix_audio_tracks(audio_sources, mixed_audio, timeline[-1]["end_time"] if timeline else 0)

        # 第三轮：合并视频 + 音频 + 字幕 + BGM
        final_output = self._finalize_video(
            concat_output, mixed_audio, subtitle_path,
            output_path, bgm_path, bgm_volume,
            timeline[-1]["end_time"] if timeline else 0,
            preserve_source_audio=preserve_source_audio,
        )

        # 清理临时文件
        cleanup_files = [concat_output, concat_file]
        if mixed_audio:
            cleanup_files.append(mixed_audio)
        for f in cleanup_files:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass

        print(f"[合成器] 最终视频: {final_output}")
        return final_output

    def _run_ffmpeg_concat(self, concat_file: str, output_path: str,
                           keep_audio: bool = False):
        """使用FFmpeg concat demuxer拼接视频"""
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", concat_file,
            "-c:v", "libx264",
            "-crf", "16",
            "-pix_fmt", "yuv420p",
        ]
        if keep_audio:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])
        else:
            cmd.append("-an")
        cmd.extend(["-preset", "fast", output_path])
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=600)
        except subprocess.CalledProcessError as e:
            print(f"  [FFmpeg concat] {e.stderr[:200] if e.stderr else ''}")
            raise

    def _mix_audio_tracks(self, audio_sources: List[tuple],
                          output_path: str, total_duration: float):
        """混合多个音频轨道"""
        if not audio_sources or total_duration <= 0:
            # 生成静音音频
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                "-t", str(total_duration),
                "-c:a", "aac", "-b:a", "192k",
                output_path,
            ]
            subprocess.run(cmd, capture_output=True, check=True, timeout=60)
            return

        # 使用ffmpeg的amix滤镜混合所有音频
        filter_parts = []
        input_parts = []

        for i, (audio_path, start, dur) in enumerate(audio_sources):
            # 先延迟，再截取
            delay_samples = int(start * 44100)
            dur_samples = int(dur * 44100)
            # adelay=delays={delay}:all=1 实现延迟
            filter_parts.append(f"[{i}:a]adelay={delay_samples}|{delay_samples},atrim=duration={dur}[a{i}]")
            input_parts.append(f"-i {audio_path}")

        # amix 混合所有轨道
        inputs_str = " ".join(input_parts)
        filter_labels = "".join(f"[a{i}]" for i in range(len(audio_sources)))
        filter_str = "; ".join(filter_parts)
        amix_str = f"{filter_labels}amix=inputs={len(audio_sources)}:duration=first:dropout_transition=2"

        cmd = (
            f"ffmpeg -y {inputs_str} "
            f"-filter_complex \"{filter_str}; {amix_str}\" "
            f"-c:a aac -b:a 192k "
            f"{output_path}"
        )

        try:
            subprocess.run(cmd, shell=True, capture_output=True, text=True, check=True, timeout=300)
        except subprocess.CalledProcessError as e:
            print(f"  [FFmpeg混合音频] {e.stderr[:300] if e.stderr else ''}")
            # 降级：使用简单串联
            self._simple_audio_concat(audio_sources, output_path, total_duration)

    def _simple_audio_concat(self, audio_sources: List[tuple],
                             output_path: str, total_duration: float):
        """降级方案：使用concat demuxer简单串联音频"""
        if not audio_sources:
            return

        # 只取第一个音频
        first_audio = audio_sources[0][0]
        cmd = [
            "ffmpeg", "-y",
            "-i", first_audio,
            "-c:a", "aac", "-b:a", "192k",
            output_path,
        ]
        subprocess.run(cmd, capture_output=True, check=True, timeout=60)

    def _finalize_video(self, video_path: str, audio_path: str,
                        subtitle_path: str, output_path: str,
                        bgm_path: str, bgm_volume: float,
                        total_duration: float,
                        preserve_source_audio: bool = False) -> str:
        """最终合成：视频+音频+字幕+BGM"""
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"未找到拼接的视频: {video_path}")

        # 构建FFmpeg命令 - 分步处理避免Windows路径转义问题
        cmd = ["ffmpeg", "-y", "-i", video_path]
        if audio_path and os.path.exists(audio_path):
            cmd.extend(["-i", audio_path])

        # 如果有BGM，添加BGM轨道
        bgm_ready = False
        if bgm_path and os.path.exists(bgm_path) and total_duration > 0:
            cmd.extend(["-i", bgm_path])
            bgm_ready = True

        # 音频混合
        if preserve_source_audio:
            bgm_vol = max(0, min(1, bgm_volume))
            if bgm_ready:
                filter_complex = (
                    "[0:a]adelay=0|0[a_main];"
                    f"[1:a]adelay=0|0,volume={bgm_vol}[a_bgm];"
                    "[a_main][a_bgm]amix=inputs=2:duration=first[a_out]"
                )
                cmd.extend(["-filter_complex", filter_complex, "-map", "0:v", "-map", "[a_out]"])
            else:
                cmd.extend(["-map", "0:v", "-map", "0:a"])
        elif bgm_ready:
            bgm_vol = max(0, min(1, bgm_volume))
            filter_complex = (
                f"[1:a]adelay=0|0[a_main];"
                f"[2:a]adelay=0|0,volume={bgm_vol}[a_bgm];"
                f"[a_main][a_bgm]amix=inputs=2:duration=first[a_out]"
            )
            cmd.extend(["-filter_complex", filter_complex, "-map", "0:v", "-map", "[a_out]"])
        else:
            cmd.extend(["-map", "0:v", "-map", "1:a"])

        # 字幕 - 使用独立步骤处理，避免Windows路径中的冒号导致 subtitles filter 解析失败
        has_subtitles = subtitle_path and os.path.exists(subtitle_path)

        # 编码参数
        cmd.extend([
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "16",
            "-c:a", "aac",
            "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-t", str(total_duration),
            output_path,
        ])

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if result.returncode != 0:
                print(f"  [FFmpeg] {result.stderr[-300:] if result.stderr else ''}")
        except subprocess.TimeoutExpired:
            raise RuntimeError("视频合成超时（>600s）")

        # 如果有字幕文件，使用subtitles filter单独叠加（避免filter_complex路径转义问题）
        if has_subtitles:
            temp_path = output_path + ".temp.mp4"
            os.rename(output_path, temp_path)

            # Windows路径处理：去掉盘符冒号，使用相对路径或正斜杠
            srt_path = subtitle_path.replace("\\", "/").replace(":", "\\\\:")
            # 或者使用更简单的方式：通过file:协议
            import urllib.parse
            srt_uri = urllib.parse.quote(subtitle_path.replace("\\", "/"), safe="/:")
            vf_cmd = [
                "ffmpeg", "-y",
                "-i", temp_path,
                "-vf", f"subtitles={srt_path}",
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", "16",
                "-c:a", "copy",
                "-pix_fmt", "yuv420p",
                output_path,
            ]
            try:
                subprocess.run(vf_cmd, capture_output=True, text=True, check=True, timeout=300)
            except subprocess.CalledProcessError as e:
                # 第二次尝试：用filter_script方式
                try:
                    script_path = output_path + ".sub.script"
                    with open(script_path, "w", encoding="utf-8") as sf:
                        sf.write(
                            f"movie='{subtitle_path.replace(chr(92), '/')}' [sub]; "
                            f"[in][sub] overlay [out]"
                        )
                    vf_cmd2 = [
                        "ffmpeg", "-y",
                        "-i", temp_path,
                        "-vf", f"subtitles={subtitle_path.replace(chr(92), '/').replace(':', '\\:')}",
                        "-c:v", "libx264",
                        "-preset", "medium",
                        "-crf", "16",
                        "-c:a", "copy",
                        "-pix_fmt", "yuv420p",
                        output_path,
                    ]
                    subprocess.run(vf_cmd2, capture_output=True, text=True, check=True, timeout=300)
                    os.remove(script_path)
                except Exception:
                    print(f"  [字幕] 叠加失败，使用无字幕版本")
                    os.rename(temp_path, output_path)
            finally:
                if os.path.exists(temp_path):
                    try: os.remove(temp_path)
                    except: pass

        if not os.path.exists(output_path):
            raise RuntimeError(f"最终视频未生成: {output_path}")

        file_size = os.path.getsize(output_path) / (1024 * 1024)
        print(f"[合成器] 输出文件大小: {file_size:.1f} MB")
        return output_path

    def _detect_font(self) -> str:
        """检测系统中文字体"""
        candidates = [
            "C:/Windows/Fonts/msyh.ttc",       # 微软雅黑
            "C:/Windows/Fonts/simhei.ttf",      # 黑体
            "C:/Windows/Fonts/STHeiti Medium.ttc",  # 华文黑体
            "/System/Library/Fonts/PingFang.ttc",   # macOS
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",  # Linux
        ]
        for font in candidates:
            if os.path.exists(font):
                return font
        return ""


def compose_from_script(script: SceneScript,
                        timeline: List[dict],
                        output_dir: str = "output",
                        bgm_path: str = "",
                        bgm_volume: float = 0.15,
                        subtitle_font: str = "") -> str:
    """
    从分镜脚本和时间线一键合成视频。

    Args:
        script: 分镜脚本
        timeline: 时间线
        output_dir: 输出目录
        bgm_path: 背景音乐路径
        bgm_volume: 背景音乐音量
        subtitle_font: 字幕字体路径

    Returns:
        最终视频路径
    """
    composer = VideoComposer(
        output_dir=output_dir,
        bgm_dir=os.path.dirname(bgm_path) if bgm_path else "",
        font_path=subtitle_font,
    )

    return composer.compose(
        timeline=timeline,
        output_path=os.path.join(output_dir, "final", f"{script.title}_漫剧.mp4"),
        bgm_path=bgm_path,
        bgm_volume=bgm_volume,
    )
