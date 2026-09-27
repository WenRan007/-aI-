# 常客 AI Windows 团队版

这是常客 AI 2.0 的 Windows 源码：本地 faster-whisper 语音识别、独立 DeepSeek 云端中文翻译、批量队列、视频预览与独立视频去重工作区。

## 团队登录与权限

启动软件后使用手机号和短信验证码登录。主账号可以在“团队管理”中启用、停用、移除成员，并授予或撤销“视频翻译”权限；成员只有获得授权后才能开始任务。权限会定期刷新，主账号撤销权限后，成员端会停止后续处理。用户名可以在“个人资料”中修改。

## AI 配置

- 识别：本地 `faster-whisper large-v3-turbo`，支持 NVIDIA CUDA，也会在没有兼容显卡时自动切换 CPU。
- 翻译：独立 DeepSeek 云端大模型，默认 `deepseek-chat`，批量任务最多 10 路并行。
- Key：仅从软件目录的 `deepseek_api_key.txt` 或环境变量 `DEEPSEEK_API_KEY` 读取。
- 真实 API Key、短信会话、模型权重、用户视频和本机配置不会提交到仓库，请使用 `deepseek_api_key.example.txt` 配置。

## 开发启动

使用 Windows 和 Python 3.10（需带 Tkinter）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
python translator_desktop.py
```

需要 FFmpeg，将 `ffmpeg.exe` 放在项目根目录、加入 PATH，或设置 `FFMPEG_PATH`。发布版运行环境与模型放在 `Windows程序/runtime`，分享发布版时复制整个程序目录。

## 构建 Windows 程序

```powershell
python -m PyInstaller --noconfirm --distpath dist --workpath build build_desktop_201.spec
```

输出为 `dist/常客AI`。不能只复制 exe，需要保留 `_internal`、运行库和模型目录。

当前入口为 `translator_desktop.py`；`desktop_account.py` 和 `desktop_account_ui.py` 提供登录、权限刷新、个人资料和团队管理。视频去重入口由同一程序独立打开，不会占用翻译队列。
