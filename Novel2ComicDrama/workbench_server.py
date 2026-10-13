#!/usr/bin/env python3
"""
workbench_server.py — 双击启动的本地漫剧工作台

启动后会自动打开浏览器，提供：
  短文本扩写分镜、一键复制提示词、视频文件列表、一键合成最终视频。
"""

import json
import os
import socket
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


ROOT = os.path.dirname(os.path.abspath(__file__))
UI_DIR = os.path.join(ROOT, "workbench_ui")
OUTPUT_ROOT = os.environ.get("AIMANJU_OUTPUT_DIR", os.path.join(ROOT, "output"))
OUTPUT_DIR = os.path.join(OUTPUT_ROOT, "browser")
VIDEOS_DIR = os.path.join(OUTPUT_ROOT, "browser_videos")
STORYBOARD_PATH = os.path.join(OUTPUT_DIR, "storyboard.json")
STATE_PATH = os.path.join(OUTPUT_DIR, "session_state.json")

SESSION = {
    "storyboard": None,
    "project_folder": "",
    "project_opened": False,
    "inputs": {},
    "profile": {},
}
COMPOSE_TASKS = {}

sys.path.insert(0, ROOT)

from modules.prompt_library import PromptLibrary

PROMPTS = PromptLibrary(
    os.path.join(ROOT, "data", "prompts", "builtin.json"),
    os.environ.get("AIMANJU_PRIVATE_DATA_DIR", os.path.join(ROOT, "data", "private")),
)

from modules.browser_workflow import (  # noqa: E402
    build_storyboard,
    compose_browser_video,
    create_project_folder,
    list_browser_videos,
    save_storyboard,
)


def load_session_state():
    if os.path.exists(STATE_PATH):
        try:
            with open(STATE_PATH, "r", encoding="utf-8") as f:
                saved = json.load(f)
            if isinstance(saved, dict):
                SESSION.update(saved)
        except Exception:
            pass


def save_session_state():
    os.makedirs(os.path.dirname(STATE_PATH) or ".", exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(SESSION, f, ensure_ascii=False, indent=2)


def resolve_project_path(value: str) -> str:
    """把相对项目的路径解析为绝对路径，保证项目迁移后仍可定位。"""
    if not value:
        return ""
    path = value if os.path.isabs(value) else os.path.join(ROOT, value)
    return os.path.realpath(path)


def project_relative(path: str) -> str:
    """把绝对路径转成相对项目根目录的便携路径。"""
    try:
        return os.path.relpath(os.path.realpath(path), ROOT).replace("\\", "/")
    except ValueError:
        # 不同盘符无法计算相对路径时，退回绝对路径
        return os.path.realpath(path).replace("\\", "/")


load_session_state()


def find_free_port(start: int = 8765, tries: int = 10) -> int:
    for port in range(start, start + tries):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    # 固定端口被系统保留时，改用系统随机分配的临时端口
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class WorkbenchHandler(BaseHTTPRequestHandler):
    server_version = "Novel2ComicDrama/0.5"

    def log_message(self, format, *args):
        pass

    def _send_json(self, code: int, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _serve_index(self):
        path = os.path.join(UI_DIR, "index.html")
        if not os.path.exists(path):
            self._send_json(500, {"error": "未找到工作台页面"})
            return
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._serve_index()
        elif path in ("/assets/prompt-library.js", "/assets/prompt-library.css"):
            filename = path.rsplit("/", 1)[1]
            with open(os.path.join(UI_DIR, filename), "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/javascript; charset=utf-8" if filename.endswith(".js") else "text/css; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path in ("/api/prompts", "/api/prompts/detail"):
            try:
                params = parse_qs(urlparse(self.path).query)
                data = PROMPTS.detail(params.get("id", [""])[0]) if path.endswith("/detail") else PROMPTS.query(params)
                self._send_json(200, data)
            except (ValueError, KeyError) as exc:
                self._send_json(400, {"error": str(exc)})
        elif path == "/api/health":
            self._send_json(200, {"ok": True})
        elif path == "/api/videos":
            project_folder = SESSION.get("project_folder")
            if not project_folder:
                self._send_json(200, {
                    "videos": [],
                    "videos_dir": "",
                    "count": 0,
                    "need_project": True,
                })
                return
            videos_dir = resolve_project_path(project_folder)
            videos = list_browser_videos(videos_dir)
            self._send_json(200, {
                "videos": videos,
                "videos_dir": project_relative(videos_dir),
                "count": len(videos),
                "project_opened": bool(SESSION.get("project_opened")),
            })
        elif path == "/api/state":
            self._send_json(200, {"state": SESSION})
        elif path == "/api/compose-progress":
            params = parse_qs(urlparse(self.path).query)
            task_id = (params.get("task_id") or [""])[0]
            task = COMPOSE_TASKS.get(task_id)
            if not task:
                self._send_json(404, {"error": "任务不存在"})
            else:
                self._send_json(200, task)
        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
        else:
            self._send_json(404, {"error": "接口不存在"})

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            if path in ("/api/prompts/import", "/api/prompts/favorites"):
                # Import is only callable from this local workbench, not another website.
                origin = self.headers.get("Origin", "")
                expected = "http://" + self.headers.get("Host", "")
                if (origin and origin != expected) or self.headers.get("Sec-Fetch-Site") == "cross-site":
                    self._send_json(403, {"error": "请在本地工作台操作资料库"})
                    return
                data = self._read_json()
                self._send_json(200, PROMPTS.save_favorites(data.get("favorites", [])) if path.endswith("/favorites") else
                                PROMPTS.import_folder(str(data.get("folder", ""))))
            elif path == "/api/expand":
                data = self._read_json()
                storyboard = build_storyboard(
                    text=data.get("text", ""),
                    style=data.get("style", "通用"),
                    min_sec=int(data.get("min_sec", 3)),
                    max_sec=int(data.get("max_sec", 6)),
                    target_shots=int(data.get("target_shots", 0)),
                    subject=data.get("subject", ""),
                    character_desc=data.get("character_desc", ""),
                    location=data.get("location", ""),
                    time_of_day=data.get("time_of_day", ""),
                    mood=data.get("mood", ""),
                    creative_point=data.get("creative_point", ""),
                    key_shot=data.get("key_shot", ""),
                )
                storyboard_path = save_storyboard(storyboard, OUTPUT_DIR)
                SESSION["storyboard"] = storyboard
                SESSION["inputs"] = {
                    "text": data.get("text", ""),
                    "style": data.get("style", "通用"),
                    "subject": data.get("subject", ""),
                    "character_desc": data.get("character_desc", ""),
                    "location": data.get("location", ""),
                    "time_of_day": data.get("time_of_day", ""),
                    "mood": data.get("mood", ""),
                    "creative_point": data.get("creative_point", ""),
                    "key_shot": data.get("key_shot", ""),
                }
                save_session_state()
                self._send_json(200, {
                    "storyboard": storyboard,
                    "storyboard_path": storyboard_path,
                })
            elif path == "/api/save-storyboard":
                storyboard = self._read_json()
                storyboard_path = save_storyboard(storyboard, OUTPUT_DIR)
                SESSION["storyboard"] = storyboard
                save_session_state()
                self._send_json(200, {"storyboard_path": storyboard_path})
            elif path == "/api/state":
                data = self._read_json()
                state = data.get("state", data)
                if isinstance(state, dict):
                    SESSION.update(state)
                    save_session_state()
                self._send_json(200, {"ok": True})
            elif path == "/api/reset":
                SESSION.clear()
                SESSION.update({
                    "storyboard": None,
                    "project_folder": "",
                    "project_opened": False,
                    "inputs": {},
                    "profile": {},
                    "interview": {"answers": {}, "done": False},
                })
                save_session_state()
                self._send_json(200, {"ok": True})
            elif path == "/api/create-project":
                data = self._read_json()
                storyboard = data.get("storyboard") or SESSION.get("storyboard")
                if not storyboard:
                    self._send_json(400, {"error": "请先生成分镜"})
                    return
                folder = create_project_folder(storyboard, os.path.dirname(OUTPUT_DIR))
                folder_rel = project_relative(folder)
                SESSION["project_folder"] = folder_rel
                SESSION["project_opened"] = False
                SESSION["storyboard"] = storyboard
                save_session_state()
                self._send_json(200, {
                    "project_folder": folder_rel,
                    "message": "项目文件夹已创建",
                })
            elif path == "/api/open-folder":
                data = self._read_json()
                folder = resolve_project_path(
                    data.get("folder") or SESSION.get("project_folder")
                )
                if not folder or not os.path.isdir(folder):
                    self._send_json(400, {"error": "项目文件夹不存在"})
                    return
                browser_root = resolve_project_path(OUTPUT_DIR)
                if os.path.commonpath([folder, browser_root]) != browser_root:
                    self._send_json(400, {"error": "文件夹路径不合法"})
                    return
                os.startfile(folder)
                SESSION["project_opened"] = True
                save_session_state()
                self._send_json(200, {
                    "ok": True,
                    "project_folder": project_relative(folder),
                })
            elif path == "/api/open-file":
                data = self._read_json()
                file_path = resolve_project_path(data.get("path", ""))
                output_root = resolve_project_path(os.path.dirname(OUTPUT_DIR))
                if not file_path or not os.path.isfile(file_path):
                    self._send_json(400, {"error": "视频文件不存在"})
                    return
                if os.path.commonpath([file_path, output_root]) != output_root:
                    self._send_json(400, {"error": "文件路径不合法"})
                    return
                os.startfile(file_path)
                self._send_json(200, {"ok": True})
            elif path == "/api/compose":
                data = self._read_json()
                project_folder = SESSION.get("project_folder")
                if not project_folder or not SESSION.get("project_opened"):
                    self._send_json(400, {
                        "error": "请先确定分镜无误并打开视频文件夹",
                    })
                    return
                project_abs = resolve_project_path(project_folder)
                storyboard_path = os.path.join(project_abs, "storyboard.json")
                videos_dir = project_abs
                if not os.path.exists(storyboard_path):
                    self._send_json(400, {"error": "项目分镜文件不存在"})
                    return
                task_id = f"compose_{int(time.time() * 1000)}"
                COMPOSE_TASKS[task_id] = {
                    "status": "running",
                    "progress": 0,
                    "step": "排队中",
                }

                def worker():
                    def report(step, percent):
                        COMPOSE_TASKS[task_id] = {
                            "status": "running",
                            "progress": percent,
                            "step": step,
                        }
                    try:
                        final_path = compose_browser_video(
                            storyboard_path=storyboard_path,
                            videos_dir=videos_dir,
                            output_dir=os.path.dirname(OUTPUT_DIR),
                            bgm_path=data.get("bgm_path", ""),
                            bgm_volume=float(data.get("bgm_volume", 0.15)),
                            progress_callback=report,
                        )
                        COMPOSE_TASKS[task_id] = {
                            "status": "done",
                            "progress": 100,
                            "step": "合成完成",
                            "final_path": project_relative(final_path),
                        }
                    except Exception as e:
                        COMPOSE_TASKS[task_id] = {
                            "status": "error",
                            "step": "合成失败",
                            "error": str(e),
                        }

                threading.Thread(target=worker, daemon=True).start()
                self._send_json(200, {"task_id": task_id})
            else:
                self._send_json(404, {"error": "接口不存在"})
        except (ValueError, KeyError) as e:
            self._send_json(400, {"error": str(e)})
        except Exception as e:
            self._send_json(500, {"error": str(e)})


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), WorkbenchHandler)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    print("=" * 56)
    print("  AI漫剧工作台已启动")
    print(f"  浏览器地址: {url}")
    print("  关闭此窗口即可停止服务")
    print("=" * 56)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
