"""
main.py — Novel2ComicDrama 主入口

使用方法：
    python main.py novel.txt
    python main.py novel.txt --output ./my_video --bgm background.mp3
    python main.py --list-fonts

不依赖任何外部API（LLM或SD），内置降级方案：
- 文本解析：内置启发式解析器（无LLM也能工作）
- 图像生成：内置FallbackPainter（纯PIL生成场景图）
- TTS：使用本地edge-tts
- 视频合成：本地FFmpeg + MoviePy

若想获得更好的效果，可配置：
- LLM:   通过 --use-llm 使用MoneyPrinterTurbo的LLM解析文本
- SD:    通过 --sd-url http://127.0.0.1:7860 连接SD WebUI
- 豆包:  通过 --doubao 使用豆包视频2.0图生视频替代本地Ken Burns
"""

import argparse
import json
import os
import sys
import time
import io

# Windows GBK encoding fix for console output
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 添加模块路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from modules.scene_splitter import SceneSplitter
from modules.character_manager import CharacterManager
from modules.director_workspace import (
    DirectorWorkspace, TTSScheduler,
    SDWebUIGenerator, FallbackPainter, DoubaoVideoGenerator,
)
from modules.video_composer import VideoComposer, compose_from_script


def ensure_dependencies():
    """检查关键依赖是否安装"""
    missing = []
    try:
        import PIL
    except ImportError:
        missing.append("Pillow (pip install Pillow)")
    try:
        import moviepy
    except ImportError:
        missing.append("moviepy (pip install moviepy)")
    try:
        import edge_tts
    except ImportError:
        missing.append("edge-tts (pip install edge-tts)")

    if missing:
        print("=" * 60)
        print("缺少以下依赖，请安装:")
        for dep in missing:
            print(f"  {dep}")
        print("=" * 60)
        return False

    # 检查FFmpeg
    import subprocess
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("=" * 60)
        print("警告: 未找到FFmpeg，视频合成需要FFmpeg")
        print("请从 https://ffmpeg.org/download.html 下载")
        print("并确保ffmpeg在PATH中")
        print("=" * 60)
        return False

    return True


def read_novel(file_path: str) -> str:
    """读取小说文件"""
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"文件不存在: {file_path}")

    with open(file_path, "r", encoding="utf-8-sig") as f:
        text = f.read()

    if not text.strip():
        raise ValueError(f"文件为空: {file_path}")

    print(f"[输入] 已读取文件: {file_path} ({len(text)} 字符)")
    return text


def setup_llm_provider(use_llm: bool = False, llm_provider: str = "moonshot"):
    """
    设置LLM提供器。

    如果 use_llm=True，尝试加载MoneyPrinterTurbo的LLM服务。
    否则返回None（使用内置降级解析器）。
    """
    if not use_llm:
        print("[LLM] 使用内置文本解析器（无需API Key）")
        return None

    try:
        # 尝试加载MoneyPrinterTurbo的LLM模块
        mpt_path = os.path.join(os.path.dirname(__file__), "../MoneyPrinterTurbo")
        sys.path.insert(0, mpt_path)

        from app.services import llm as mpt_llm
        print(f"[LLM] 已连接 MoneyPrinterTurbo LLM 服务 (provider: {llm_provider})")
        return mpt_llm._generate_response
    except Exception as e:
        print(f"[LLM] 无法加载MoneyPrinterTurbo LLM: {e}")
        print("[LLM] 将使用内置文本解析器")
        return None


def setup_image_generator(sd_url: str = "", output_dir: str = "output"):
    """设置图像生成器"""
    if sd_url:
        try:
            gen = SDWebUIGenerator(api_url=sd_url)
            print(f"[图像] 使用 Stable Diffusion WebUI: {sd_url}")
            return gen
        except Exception as e:
            print(f"[图像] 无法连接SD: {e}")

    print("[图像] 使用内置场景绘制器（无需GPU）")
    return FallbackPainter()


def find_bgm(output_dir: str, bgm_path: str = "") -> str:
    """查找背景音乐文件"""
    if bgm_path and os.path.exists(bgm_path):
        print(f"[BGM] 使用指定背景音乐: {bgm_path}")
        return bgm_path

    # 尝试从 MoneyPrinterTurbo 资源目录加载
    bgm_candidates = [
        os.path.join(os.path.dirname(__file__), "../MoneyPrinterTurbo/resource/songs"),
    ]
    for bgm_dir in bgm_candidates:
        if os.path.isdir(bgm_dir):
            files = [f for f in os.listdir(bgm_dir) if f.endswith((".mp3", ".wav", ".m4a"))]
            if files:
                selected = os.path.join(bgm_dir, files[0])
                print(f"[BGM] 使用默认背景音乐: {selected}")
                return selected

    print("[BGM] 未找到背景音乐，将不使用BGM")
    return ""


def main():
    parser = argparse.ArgumentParser(
        description="Novel2ComicDrama — 小说自动转AI漫剧视频",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py 小说.txt                                              # 最简单方式
  python main.py 小说.txt --output ./output                            # 指定输出目录
  python main.py 小说.txt --bgm music.mp3                              # 添加背景音乐
  python main.py 小说.txt --use-llm                                    # 使用LLM解析剧本
  python main.py 小说.txt --sd-url http://127.0.0.1:7860               # 使用SD生成画面
  python main.py 小说.txt --doubao --doubao-api-key xxxx               # 用豆包视频2.0图生视频

无LLM无SD也能运行：使用内置解析器 + PIL绘制场景 + edge-tts配音
        """
    )

    parser.add_argument("novel", nargs="?", default="", help="小说文本文件路径 (.txt)")
    parser.add_argument("--output", "-o", default="./output", help="输出目录 (默认: ./output)")
    parser.add_argument("--bgm", default="", help="背景音乐文件路径")
    parser.add_argument("--bgm-volume", type=float, default=0.15, help="背景音乐音量 0-1 (默认: 0.15)")
    parser.add_argument("--sd-url", default="", help="Stable Diffusion WebUI API地址 (默认: 不使用SD)")
    parser.add_argument("--doubao", action="store_true", help="使用豆包视频2.0（图生视频）生成镜头动态片段")
    parser.add_argument("--doubao-api-key", default="", help="豆包/方舟API Key（也可设置 DOUBAO_API_KEY 或 ARK_API_KEY）")
    parser.add_argument("--doubao-model", default="", help="豆包视频模型或方舟接入点ID（默认: doubao-seedance-1-0-pro-250528）")
    parser.add_argument("--doubao-base-url", default="", help="豆包视频接口地址（默认: 方舟API）")
    parser.add_argument("--doubao-fps", type=int, default=60, help="目标帧率（默认60）")
    parser.add_argument("--doubao-motion-strength", type=float, default=0.0, help="动态强度覆盖，0=按镜头类型自动")
    parser.add_argument("--doubao-style-strength", type=float, default=0.0, help="画风强度覆盖，0=默认0.1")
    parser.add_argument("--doubao-ratio", default="auto", help="画幅比例（auto/16:9/9:16，默认按参考图自动）")
    parser.add_argument("--use-llm", action="store_true", help="使用MoneyPrinterTurbo的LLM解析文本")
    parser.add_argument("--llm-provider", default="moonshot", help="LLM提供商 (默认: moonshot)")
    parser.add_argument("--script-only", action="store_true", help="只生成分镜脚本，不生成视频")
    parser.add_argument("--timeline-only", action="store_true", help="只生成时间线，不合成视频")
    parser.add_argument("--list-fonts", action="store_true", help="列出可用中文字体")
    parser.add_argument("--dry-run", action="store_true", help="演练模式：只分析并输出分镜，不生成任何媒体文件")

    args = parser.parse_args()

    # 特殊命令
    if args.list_fonts:
        list_available_fonts()
        return

    if not args.novel:
        parser.print_help()
        print("\n请提供小说文件路径，例如: python main.py 小说.txt")
        return

    # 确保输出目录
    os.makedirs(args.output, exist_ok=True)

    # 检查依赖
    if not args.script_only and not args.dry_run:
        if not ensure_dependencies():
            print("\n请安装缺失依赖后重试。")
            return

    # 1. 读取小说
    print("\n" + "=" * 60)
    print("  Novel2ComicDrama v0.1.0 — 小说自动转AI漫剧")
    print("=" * 60)
    novel_text = read_novel(args.novel)

    # 2. 文本解析
    print("\n" + "=" * 40)
    print("阶段1/5: 剧本与分镜解析")
    print("=" * 40)

    llm_func = setup_llm_provider(args.use_llm, args.llm_provider)
    splitter = SceneSplitter(llm_provider=llm_func)
    script = splitter.parse(novel_text)

    # 保存分镜脚本
    script_path = os.path.join(args.output, "storyboard.json")
    splitter.save_script(script, script_path)

    print(f"  故事标题: {script.title}")
    print(f"  场景数量: {len(script.scenes)}")
    print(f"  角色数量: {len(script.characters)}")
    print(f"  总镜头数: {sum(len(s.shots) for s in script.scenes)}")
    print(f"  预估时长: {script.total_duration():.1f}秒")

    if args.script_only:
        print("\n[完成] 分镜脚本已生成，跳过视频生成 (--script-only)")
        return

    # 3. 角色管理
    print("\n" + "=" * 40)
    print("阶段2/5: 角色配置与声音分配")
    print("=" * 40)

    char_mgr = CharacterManager(output_dir=args.output)
    char_mgr.register_from_script(script)
    print(char_mgr.summary())

    # 4. 导演工作台 - 协调资产生成
    print("\n" + "=" * 40)
    print("阶段3/5: 导演工作台 - 画面与配音生成")
    print("=" * 40)

    # 设置图像生成器
    image_gen = setup_image_generator(args.sd_url, args.output)

    # 设置豆包视频生成器（可选，替代本地Ken Burns）
    doubao_gen = None
    if args.doubao:
        doubao_params = {
            "fps": args.doubao_fps,
            "ratio": args.doubao_ratio,
        }
        if args.doubao_motion_strength > 0:
            doubao_params["motion_strength"] = args.doubao_motion_strength
        if args.doubao_style_strength > 0:
            doubao_params["style_strength"] = args.doubao_style_strength
        doubao_gen = DoubaoVideoGenerator(
            api_key=args.doubao_api_key,
            model=args.doubao_model,
            base_url=args.doubao_base_url,
            video_params=doubao_params,
        )
        if doubao_gen.is_available():
            print(f"[豆包] 启用视频2.0图生视频: model={doubao_gen.model}")
        else:
            print("[豆包] 未设置 API Key，跳过豆包生成，回退本地 Ken Burns")
            doubao_gen = None

    # 设置TTS
    tts = TTSScheduler(output_dir=os.path.join(args.output, "audio"))

    # 创建导演工作台
    director = DirectorWorkspace(
        output_dir=args.output,
        image_generator=image_gen,
        tts_scheduler=tts,
        char_manager=char_mgr,
        video_generator=doubao_gen,
    )

    if args.dry_run:
        # 演练模式：只输出计划不执行
        print("\n[演练模式] 跳过实际生成")
        print(f"  预计生成 {sum(len(s.shots) for s in script.scenes)} 个镜头")
        print(f"  预计耗时 ~{script.total_duration():.0f}秒")
        print(f"  输出目录: {os.path.abspath(args.output)}")
        print("\n[完成] 演练完成")
        return

    # 执行制作
    t0 = time.time()
    shot_assets = director.produce(script, parallel=False)
    t1 = time.time()
    print(f"\n[耗时] 资产生成: {t1 - t0:.1f}秒")

    # 5. 获取时间线
    print("\n" + "=" * 40)
    print("阶段4/5: 时间线编排")
    print("=" * 40)

    timeline = director.get_timeline()
    director.save_timeline(timeline)

    if args.timeline_only:
        print("\n[完成] 时间线已生成，跳过视频合成 (--timeline-only)")
        return

    # 6. 视频合成
    print("\n" + "=" * 40)
    print("阶段5/5: 视频合成")
    print("=" * 40)

    bgm_path = find_bgm(args.output, args.bgm)

    t2 = time.time()
    final_video = compose_from_script(
        script=script,
        timeline=timeline,
        output_dir=args.output,
        bgm_path=bgm_path,
        bgm_volume=args.bgm_volume,
    )
    t3 = time.time()

    if final_video and os.path.exists(final_video):
        file_size = os.path.getsize(final_video) / (1024 * 1024)
        total_time = t3 - t0
        print("\n" + "=" * 60)
        print("  ✓ 制作完成!")
        print("=" * 60)
        print(f"  输出视频: {final_video}")
        print(f"  文件大小: {file_size:.1f} MB")
        print(f"  总耗时:   {total_time:.1f}秒")
        print(f"  输出目录: {os.path.abspath(args.output)}")
        print("=" * 60)
    else:
        print("\n[错误] 视频合成失败")
        print("检查输出目录中的日志文件了解详情")


def list_available_fonts():
    """列出系统中可用的中文字体"""
    import subprocess

    print("系统中可用的中文字体:")
    print("-" * 40)

    # Windows字体目录
    font_dirs = []
    if os.name == "nt":
        font_dirs = ["C:/Windows/Fonts"]
    elif sys.platform == "darwin":
        font_dirs = ["/System/Library/Fonts", "/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    else:
        font_dirs = ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts")]

    found = []
    for fd in font_dirs:
        if os.path.isdir(fd):
            for f in os.listdir(fd):
                if f.lower().endswith((".ttf", ".ttc")) and any(
                    kw in f.lower() for kw in ["chinese", "cjk", "hei", "song", "ming",
                                               "kai", "fang", "yuan", "yahei", "pingfang",
                                               "noto", "wqy", "微软", "中", "黑", "宋", "楷"]
                ):
                    found.append(os.path.join(fd, f))

    if found:
        for f in found:
            font_name = os.path.basename(f)
            size = os.path.getsize(f) // 1024
            print(f"  {font_name} ({size} KB)  ->  {f}")
    else:
        print("  未找到中文字体，将使用默认字体")

    print(f"\n共找到 {len(found)} 个中文字体")


if __name__ == "__main__":
    main()
