# 常客 AI Windows 2.0.3

当前 Windows 软件完整源码：视频转录与翻译、独立的批量去重工作区、视频预览、贴纸库、15 个 GIF 特效预览及批次导出。当前入口为 `translator_desktop.py`，无需账号登录。

## 开发启动

使用 Windows 和 Python 3.10（需带 Tkinter）。在项目目录运行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\启动源码.ps1
```

还需安装 FFmpeg，将 `ffmpeg.exe` 放在项目根目录、加入 PATH，或设置 `FFMPEG_PATH`。源码包不包含 FFmpeg、CUDA、模型权重和 Ollama 运行库。

启动脚本会从 `translation_config.example.json` 创建本机 `translation_config.json`。所有路径可用相对于项目目录（打包后相对于 exe）的相对路径，不再依赖开发者电脑的 D 盘目录。配置和密钥都被 Git 忽略。

## 识别与翻译配置

- 识别使用本地 faster-whisper，默认模型名 `large-v3-turbo`，第一次使用时需从模型平台下载。也可将已下载的完整模型目录填写到 `whisper_model`，例如 `runtime/models/large-v3-turbo`。本仓库没有捆绑模型权重。
- 配置样例使用兼容性优先的 `cpu` / `int8`。支持 NVIDIA GPU 的电脑安装匹配的 CUDA/cuDNN 运行库后，可改为 `whisper_device: cuda`、`whisper_compute_type: int8_float16`，并将 DLL 目录写入 `cuda_dir`。GPU 识别默认单路，任务队列与翻译并行度由应用控制。
- 默认翻译通道为本机 Ollama `qwen2.5:3b`。安装 Ollama，执行 `ollama pull qwen2.5:3b`，保持其本机服务可用。如果希望软件自动启动便携版 Ollama，将程序放到 `runtime/ollama/ollama.exe`、模型存储放到 `runtime/ollama-models`；也可以把两项配置改为自己电脑的实际目录。已运行的标准 Ollama 服务可直接连接，模型须安装在该服务使用的目录下。
- 在软件内选择 DeepSeek 云端翻译时，在软件目录自行创建 `deepseek_api_key.txt`，只写自己的 Key，或设置 `DEEPSEEK_API_KEY` 环境变量。
- DeepL 为另一可选翻译通道，使用本机 `deepl_api_key.txt` 或 `DEEPL_API_KEY`。云端翻译仍以本地 Whisper 转录结果为输入。
- 识别失败、模型缺失、空译文与服务错误会报错，不会把原文当成中文翻译结果。

## 构建 Windows 程序

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\构建.ps1
```

输出为 `dist/常客AI`。分享时必须复制整个文件夹，包括 `_internal`；不能只发 exe。FFmpeg 与所需本地模型/运行库应另行配置。构建脚本不会复制私人 Key、模型或本机配置。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

部分测试会短暂创建 Tk 窗口，需在可用的 Windows 桌面会话中运行。`performance_checks/translation_batch_check.py` 是实音频批次测试，须先配置模型和翻译服务。

## 文件说明

- `translator_desktop.py`：当前桌面入口及两个互相独立的工作区。
- `local_translation.py`、`deepseek_translation.py`、`translation_runtime.py`：本地/云端翻译与运行环境。
- `dedup_preview.py`、`effect_library.py`、`effect_picker.py`、`modal_drawer.py`、`assets/effects/`：去重和预览。
- `desktop_account.py`、`desktop_account_ui.py`、`translator_bootstrap.py`、`translator_desktop_payload.bin`、`常客AI修复版.spec`：历史账号/启动兼容代码，留存便于团队参考。当前入口与构建脚本不使用它们；历史 spec 内路径仅适用于旧环境。

源码包不含真实 API Key、用户会话、个人视频/贴纸、缓存及 Git 历史。请勿将这些私人文件手动加入仓库。
