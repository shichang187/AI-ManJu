# AI漫剧工作台

把用户的简短故事，通过“对话式创作访谈 + 分镜提示词 + 豆包浏览器生成 + 本地自动合成”变成一部完整的 AI 漫剧视频。

## 使用流程

1. 双击 `start_workbench.bat`，浏览器自动打开 AI漫剧工作台。
2. 在故事输入框输入内容，点击“开始创作”。
3. AI 会依次询问 5 个问题：地点、时间段、核心情绪、创意点、画面重点。
4. 回答完 5 个问题后，系统自动扩写故事，并生成 5 个 3-6 秒的分镜。
5. 所有分镜共用同一个环境锚点，保证地点、时间和光线全程一致。
6. 确认分镜无误后，点击“确定分镜无误”，系统创建该项目专属视频文件夹。
7. 点击“打开视频文件夹”，把豆包生成的视频按 `1.mp4`、`2.mp4`、`3.mp4`、`4.mp4`、`5.mp4` 编号放入。
8. 点击“复制全部分镜”或每个镜头的“复制提示词”，再点击“打开豆包”，把提示词逐镜喂给豆包。
9. 豆包生成完成后，回到工作台点击“刷新视频”，只刷新当前项目的视频。
10. 点击“合成最终视频”，如果视频数量不足 5 个，会弹窗确认是否继续合成。
11. 等待进度条完成，点击“打开视频”预览最终成片。

需要做新项目时，点击“重置”，确认后会清空当前分镜、访谈和项目状态，但不会删除已经生成的文件。

## 目录结构

```
Novel2ComicDrama/
├── start_workbench.bat          # 双击启动工作台
├── workbench_server.py          # 本地服务：状态保存、分镜、合成、打开文件
├── workbench_ui/
│   └── index.html               # 浏览器工作台界面
├── modules/
│   ├── browser_workflow.py      # 访谈扩写、分镜生成、项目文件夹、合成
│   ├── video_composer.py        # FFmpeg 合成，保留豆包视频自带声音
│   ├── director_workspace.py    # 旧管线核心调度器
│   ├── scene_splitter.py        # 分镜数据结构
│   └── character_manager.py     # 角色一致性管理
└── output/
    ├── browser/
    │   ├── 项目名/              # 每个项目的视频文件夹
    │   ├── storyboard.json
    │   ├── session_state.json
    │   └── final_video.mp4      # 最终成片
```

## 环境要求

- Windows
- Python 3
- FFmpeg（已加入 PATH）
- 一个可以打开豆包视频生成页的浏览器

默认扩写完全本地、免费，不需要 API Key。

如果想让扩写更聪明，可以配置 OpenAI 兼容接口：

```powershell
$env:EXPAND_LLM_API_KEY="你的Key"
$env:EXPAND_LLM_BASE_URL="https://你的接口/v1"
$env:EXPAND_LLM_MODEL="模型名"
```

配置失败或未配置时，会自动回退到内置模板。

## 路径说明

页面保存的项目文件夹、最终视频路径都使用相对项目根目录的路径，整个项目文件夹复制到其他电脑后，只要重新双击 `start_workbench.bat`，仍能正常读取和合成。

## 常见情况

- 豆包有时会把多个分镜合并成一个视频，导致视频数量不足 5 个：合成前会弹窗确认，确认后系统会按顺序继续合成，并把分镜信息合并到对应视频的字幕里。
- 豆包生成的视频自带背景音乐：合成时默认保留原视频音轨。
- 页面刷新不会丢内容：访谈进度、分镜、项目文件夹和合成状态会自动恢复。

## 旧 CLI 管线

`main.py` 仍然保留，用于需要自动 TTS、Stable Diffusion 或旧版本地 Ken Burns 的流程：

```powershell
python main.py 小说.txt --output ./output
python main.py 小说.txt --sd-url http://127.0.0.1:7860
python main.py 小说.txt --doubao --doubao-api-key xxxx
```

