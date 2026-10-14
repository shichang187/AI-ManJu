# Seedance 2.0 第一批来源与内容审核报告

2026-10-14，Codex。正文与适配版本地私有，本文仅记录来源链接和审核摘要。未调用真实视频模型，也未下载第三方示例媒体。

## 收集结果

对27个详情入口进行了读取尝试：YouMind 25个，PromptMart 2个；其中11条YouMind取得完整可见正文，14个详情读取失败，PromptMart两条登录锁定/截断。不能把失败记录算成完成全文内容评审；本轮未达到20条完整候选的目标。11条完整候选逐条检查后，以其中8个不同来源各制作一条单镜改写版，另外3条留作候选。没有将同一来源分页、重复正文、翻译或不同卡片标题计为多条精选。

采集通过正常公开页及网页读取工具，Python直连发生TLS连接错误；没有关闭证书检查、反复猜入口、绕过锁定或提取隐藏账号数据。`tools/collect_sources.py` 导入这种编号文本快照，按独立提示词区提取和规范化正文指纹去重，不将首页摘要、菜单或相关推荐视为提示词。采集不自动评审；语义相近结构另外人工对照。

[即梦公开入口](https://jimeng.jianying.com/ai-tool/home/)可读取，但本会话没有已连接的作者登录浏览器，正常浏览器加载超时，完整作品提示词与实际试片未验证。[AI艺术之家](https://aihome.art/)和[Prompt工坊指定地址](https://kk-6788.github.io/vibecoding-prompt-studio/)本轮复查仍未取得内容，不能推断站点永久关闭或仓库不存在。

## 八条单镜适配版

下表评分针对改写版，不是原来源质量或实际模型效果。五项各0–2分依次为设定、动作时间、运镜、连续性、变量依赖；详细理由和原文哈希在私有记录。

| 用途 / 适配版 | 完整来源 / 页面作者 | 评分 | 改写理由 |
| --- | --- | --- | --- |
| 情绪停顿 | [教室情感](https://youmind.com/zh-CN/video-prompts/japanese-classroom-romance-1402) · AIGC｜阳家豪 | 10 | 15秒多镜与多轮对白改为单个反应，移除原示例角色与服装。 |
| 行动跟拍 | [街头赛车](https://youmind.com/zh-CN/video-prompts/cinematic-street-racing-night-2530) · Pierrick Chevallier | 10 | 12秒六段只选取一类侧跟机位，不把原赛车和氮气加进当前故事。 |
| 物件细节 | [田园生活](https://youmind.com/zh-CN/video-prompts/modern-rural-healing-cinematic-288) · John | 10 | 三镜采摘、切菜、用餐改为既有物件的一次操作与结果。 |
| 风格一致 | [高定奇幻](https://youmind.com/zh-CN/video-prompts/haute-couture-porcelain-ink-vortex-594) · John | 9 | 删除复杂衣服碎裂与世界溶解，保留光影、材质与身份连续。 |
| 动作起势 | [水雷对决](https://youmind.com/zh-CN/video-prompts/water-thunder-breathing-duel-189) · John | 9 | 删除特定IP角色、呼吸法和强制对手，适配准备、执行和落点。 |
| 短句对白 | [说唱MV](https://youmind.com/zh-CN/video-prompts/80-year-old-rapper-music-video-1403) · 松果先森 | 9 | 删除原歌词、舞蹈和多段切镜，限定单一说话者的短句与停顿。 |
| 环境空镜 | [车站故事](https://youmind.com/zh-CN/video-prompts/cinematic-railway-platform-scene-12054) · ORHAN | 10 | 仅保留环境建立与声音留白，删除人物、信封与火车剧情。 |
| 路径衔接 | [森林骑行](https://youmind.com/zh-CN/video-prompts/medieval-archer-forest-rider-12304) · Ayat | 10 | 不沿用马匹、弓箭和营地，用当前故事路径保持起点和落点连续。 |

这些是结构参考后的重新编辑，不是原作者原文或未经修改的示例复刻。每条适配版仅作用于一个镜头，3–10秒范围为编辑约束；时间段按实际时长计算。覆盖人物情感、对白、动作、环境、运镜与风格。三个新增分镜指令为项目原创，用于文本模型，和视频模板分开。

## 保留候选与拒绝入库

[都市追逐](https://youmind.com/zh-CN/video-prompts/seedance-urban-chase-prompt-12119)原例30秒，要求角色参考图；[2D格斗](https://youmind.com/zh-CN/video-prompts/2d-fighting-game-sequence-prompt-for-seedance-2-0-2684)要求两张参考图且四镜合计22秒；[烈焰战士](https://youmind.com/zh-CN/video-prompts/fiery-warrior-action-video-12303)动作链较复杂、缺少清楚的运镜和时间分配。本轮三条都没有进入默认精选。

[PromptMart仙侠](https://www.promptmart.cn/prompt/33354620)公开提示词明确截断并显示登录免费解锁；[香水广告](https://www.promptmart.cn/prompt/12933431)此前已确认锁定及参考素材依赖，本轮页面读取输出未覆盖完整详情，仍未取得完整正文。两条保留待获取记录，不用摘要凑精选，不绕过解锁。

原时长、多镜要求和参考素材依赖留在候选与改写理由，长多镜示例没有自动缩成4秒单镜或平摊对白。平台账户的免费能力、实际模式/时长、2.0 mini/2.5与2.0差异没有混用。

## 权限与核验限制

未取得以上集合整体可再发布许可。作者、详情入口、采集日期、可见原文哈希、完整性、版本、审核者、理由、评分和未实测状态保存在忽略的私有目录。非中文来源本轮取得的是中文页面可见译文，原语言全文与翻译准确性未验证，不能将译文标成作者原语言原文。

来源示例视频的观感、原发布平台的结果、真实即梦/豆包试片均未验证。本轮内容评分由Codex对完整文本与改写版逐条评审，未冒称作者人工验收或模型实测。

模型列表使用硅基流动官方的[认证与chat筛选接口](https://docs.siliconflow.cn/docs/api/models-get)，仅说明列表认证成功，不能代表免费、余额充足或分镜质量合格。
