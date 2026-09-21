# -*- coding: utf-8 -*-
"""常客AI Windows 桌面版：批量将视频口播翻译为简体中文。"""
from __future__ import annotations
import asyncio, hashlib, json, os, queue, re, shutil, subprocess, tempfile, threading, time, wave, sys, random
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
import urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import List, Sequence, Tuple
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from translation_highlights import find_highlights, PRICE_HIGHLIGHT_COLOR, PROMOTION_HIGHLIGHT_COLOR
from PIL import Image, ImageTk
from dedup_preview import DedupPreview
from modal_drawer import ModalDrawer
from effect_library import EFFECTS, build_effect_filter
from effect_picker import EffectPicker
from translation_runtime import load_config, setup_cuda
from local_translation import OllamaTranslator
from deepseek_translation import DeepSeekTranslator

# 源语言仍支持自动识别及常见语种；产品输出统一为简体中文。
LANGUAGES: Sequence[Tuple[str, str]] = (("auto", "自动识别"), ("zh-CN", "中文（简体）"), ("vi", "越南语"), ("en", "英语"), ("zh-TW", "中文（繁体）"), ("ja", "日语"), ("ko", "韩语"), ("th", "泰语"), ("fr", "法语"), ("de", "德语"), ("es", "西班牙语"), ("pt", "葡萄牙语"), ("id", "印尼语"), ("ar", "阿拉伯语"), ("ru", "俄语"))
TARGET_LANGUAGES: Sequence[Tuple[str, str]] = (("zh-CN", "中文（简体）"),)
TARGET_LANGUAGE = ("zh-CN", "中文（简体）")
LANG_NAME = dict(LANGUAGES)
VIDEO_TYPES = [("视频文件", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v"), ("所有文件", "*.*")]
APP_DIR = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent

@dataclass
class Segment:
    start: float
    end: float
    text: str
    translated: str = ""

@dataclass
class Job:
    video: str
    source: str
    target: str
    mode: str
    status: str = "等待中"
    progress: int = 0
    output: str = ""
    error: str = ""
    needs_attention: bool = False
    translation_text: str = ""

class TranslationBackend:
    """替换此类即可接入 Whisper/faster-whisper、翻译模型和 TTS 服务。"""
    def __init__(self):
        self.config = load_config(APP_DIR)
        setup_cuda(self.config, APP_DIR)
        self._whisper = None
        self._model_lock = threading.Lock()
        self._cache_lock = threading.Lock()
        self._transcript_cache = {}
        self._model_name = self.config.get("whisper_model", "small")
        self.engine = self.config.get("translation_engine", "deepl").lower()
        self.asr_device = self.config.get("whisper_device", "cpu")
        self.asr_compute_type = self.config.get("whisper_compute_type", "int8_float16" if self.asr_device == "cuda" else "int8")
        self.asr_status = "模型尚未加载"
        self._asr_slots = threading.Semaphore(max(1, int(self.config.get("asr_parallel", 1))))
        self._ollama = OllamaTranslator(
            host=self.config.get("ollama_host", "http://127.0.0.1:11434"),
            model=self.config.get("ollama_model", "qwen2.5:3b"),
            executable=self.config.get("ollama_executable"),
            models_dir=self.config.get("ollama_models_dir"),
            num_gpu=self.config.get("ollama_num_gpu", 0))
        self._deepseek = DeepSeekTranslator(
            api_key=self._key("DEEPSEEK_API_KEY"),
            model=self.config.get("deepseek_model", os.getenv("DEEPSEEK_MODEL", "deepseek-chat")),
            endpoint=self.config.get("deepseek_endpoint", "https://api.deepseek.com/chat/completions"),
            timeout=self.config.get("deepseek_timeout", 90))
        self._detected_language = ""
        self._argos_ready = False
        self._model_cache_dir = APP_DIR / "models_cache"
        self._model_cache_dir.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HF_HOME", str(self._model_cache_dir))
        os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(self._model_cache_dir / "hub"))
        # 识别可以多路并行；云端翻译限制为两路，避免 10 个视频同时请求造成 429 或空译文。
        self._translation_slots = threading.Semaphore(1)

    @property
    def engine_label(self):
        if self.engine == "ollama":
            return "Ollama 本地翻译"
        if self.engine == "deepseek":
            return "DeepSeek 云端翻译"
        return "DeepL 在线翻译"

    @property
    def engine_description(self):
        model = Path(self._model_name).name
        if len(model) > 25:
            model = "Whisper 本地模型"
        device = "NVIDIA GPU" if self.asr_device == "cuda" else "CPU"
        if self.engine == "ollama":
            translator = self._ollama.model
        elif self.engine == "deepseek":
            translator = self._deepseek.model
        else:
            translator = "DeepL"
        return f"识别：{model} / {device} · 翻译：{translator}"
    @staticmethod
    def _key(name: str) -> str:
        value = os.getenv(name, "").strip()
        if not value:
            path = APP_DIR / (name.lower() + ".txt")
            try:
                value = path.read_text(encoding="utf-8").strip()
            except OSError:
                return ""
        return value.replace("\ufeff", "").strip().strip('"').strip("'")
    def _audio_cache_key(self, video: str, source: str) -> str:
        path = Path(video)
        try:
            stat = path.stat()
            stamp = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{source}|{self._model_name}"
        except OSError:
            stamp = f"{video}|{source}|{self._model_name}"
        return hashlib.sha1(stamp.encode("utf-8", errors="ignore")).hexdigest()

    def _extract_audio(self, video: str) -> Tuple[str, str]:
        """只抽取 16 kHz 单声道 PCM 音频，避免 Whisper 再打开/解析视频容器。"""
        ffmpeg = self._ffmpeg()
        audio_root = Path(os.getenv("CHANGKE_TEMP_DIR", str(APP_DIR / "temp_audio")))
        audio_root.mkdir(parents=True, exist_ok=True)
        work = tempfile.mkdtemp(prefix="changke_audio_", dir=str(audio_root))
        audio = str(Path(work) / "speech.wav")
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y", "-threads", "1", "-i", str(video),
               "-map", "0:a:0?", "-vn", "-sn", "-dn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", audio]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0 or not Path(audio).exists() or Path(audio).stat().st_size < 64:
            detail = (result.stderr or result.stdout or "视频没有可用音轨").strip().splitlines()[-1]
            shutil.rmtree(work, ignore_errors=True)
            raise RuntimeError(f"音频提取失败：{detail}")
        return work, audio

    def transcribe_with_language(self, video: str, source: str) -> Tuple[List[Segment], str]:
        key = self._audio_cache_key(video, source)
        with self._cache_lock:
            cached = self._transcript_cache.get(key)
        if cached:
            return [Segment(s.start, s.end, s.text, s.translated) for s in cached[0]], cached[1]
        work, audio = self._extract_audio(video)
        try:
            with self._asr_slots:
                from faster_whisper import WhisperModel
                with self._model_lock:
                    if self._whisper is None:
                        logical = os.cpu_count() or 4
                        cpu_threads = max(1, min(8, logical - 2 if logical > 2 else logical))
                        self._whisper = WhisperModel(self._model_name, device=self.asr_device,
                            compute_type=self.asr_compute_type, cpu_threads=cpu_threads,
                            num_workers=max(1, int(self.config.get("asr_parallel", 1))),
                            download_root=str(self._model_cache_dir))
                        self.asr_status = f"{self.asr_device} / {self.asr_compute_type}"
                language = None if source == "auto" else source.split("-")[0]
                chunks, info = self._whisper.transcribe(audio, language=language,
                    vad_filter=True, vad_parameters={"min_silence_duration_ms": 500, "speech_pad_ms": 300},
                    beam_size=5, condition_on_previous_text=False)
                detected = getattr(info, "language", "") or language or ""
                # Keep inference inside the semaphore: faster-whisper returns a lazy generator.
                result = [Segment(float(c.start), float(c.end), c.text.strip()) for c in chunks if c.text.strip()]
            if result:
                with self._cache_lock:
                    self._transcript_cache[key] = (result, detected)
                self._detected_language = detected
                return result, detected
        except Exception as exc:
            raise RuntimeError(f"本地语音识别失败（{self.asr_device}/{self.asr_compute_type}）：{exc}") from exc
        finally:
            shutil.rmtree(work, ignore_errors=True)
        raise RuntimeError("未识别到有效口播，请检查视频是否有人声，并尝试手动选择识别语言。")

    def transcribe(self, video: str, source: str) -> List[Segment]:
        result, _ = self.transcribe_with_language(video, source)
        return result

    def release_recognition_model(self):
        # Called only after all ASR futures have completed, never during inference.
        import gc
        with self._model_lock:
            self._whisper = None
            gc.collect()

    def translate(self, segments: List[Segment], source: str, target: str, localize: bool, emotion: bool, detected_source: str = "") -> List[Segment]:
        """Local Ollama or DeepL, with detected language belonging to this job only."""
        if not segments:
            raise RuntimeError("没有识别到可翻译的口播。")
        # 软件只保留中文目标，防止旧队列或外部调用误传其它目标语言。
        target_code = "ZH"
        source_code = (detected_source if source == "auto" else source).split("-")[0].upper()
        # 中文源视频无需向 DeepL 提交同语种请求，直接保留识别结果，避免 API 拒绝相同源/目标语言。
        if source_code == target_code:
            for seg in segments:
                seg.translated = seg.text
            return segments
        if self.engine == "ollama":
            translated = self._ollama.translate([s.text for s in segments], source_code)
            for segment, text in zip(segments, translated):
                segment.translated = text
            return segments
        if self.engine == "deepseek":
            translated = self._deepseek.translate([s.text for s in segments], source_code)
            for segment, text in zip(segments, translated):
                segment.translated = text
            return segments
        if self.engine != "deepl":
            raise RuntimeError(f"不支持的翻译引擎：{self.engine}")
        deepl_key = self._key("DEEPL_API_KEY")
        if not deepl_key:
            raise RuntimeError("未配置 DeepL API Key，请切换 Ollama 本地翻译或填写 deepl_api_key.txt。")
        endpoint = "https://api-free.deepl.com/v2/translate" if ":fx" in deepl_key else "https://api.deepl.com/v2/translate"
        # DeepL 接口允许一次提交多段 text；按 50 段分批，减少网络往返并保持返回顺序。
        context = "\n".join((seg.text or "").strip() for seg in segments if (seg.text or "").strip())[:8000]
        for offset in range(0, len(segments), 50):
            batch = segments[offset:offset + 50]
            pairs = [("text", seg.text) for seg in batch] + [("target_lang", target_code)]
            if source_code:
                pairs.append(("source_lang", source_code))
            if context:
                pairs.append(("context", context))
            last_error = None
            for attempt in range(5):
                try:
                    request = urllib.request.Request(endpoint, data=urllib.parse.urlencode(pairs).encode("utf-8"), method="POST", headers={"Authorization": f"DeepL-Auth-Key {deepl_key}", "Content-Type": "application/x-www-form-urlencoded"})
                    with urllib.request.urlopen(request, timeout=30) as response:
                        result = json.loads(response.read().decode("utf-8"))
                    translations = result.get("translations", [])
                    if len(translations) != len(batch):
                        raise RuntimeError(f"DeepL 返回段数异常：{len(translations)}/{len(batch)}")
                    for seg, translated in zip(batch, translations):
                        seg.translated = translated.get("text", "").strip()
                        if not seg.translated:
                            raise RuntimeError("DeepL 返回了空译文")
                    break
                except Exception as exc:
                    last_error = exc
                    code = getattr(exc, "code", None)
                    if code == 456:
                        raise RuntimeError("DeepL 额度已用尽（HTTP 456），请切换 Ollama 本地翻译或补充 DeepL 额度。") from exc
                    if code in (400, 401, 403, 404, 413):
                        raise RuntimeError(f"DeepL 请求被拒绝（HTTP {code}），请检查密钥和请求设置。") from exc
                    if attempt < 4:
                        delay = (2 ** (attempt + 1)) if code == 429 else (1.0 * (attempt + 1))
                        time.sleep(delay)
            else:
                raise RuntimeError(f"DeepL 翻译请求失败：{last_error}") from last_error
        return segments
    def create_voice_task(self, segments: List[Segment], target: str, path: Path, speed: str, voice: str, emotion: bool) -> None:
        path.write_text(json.dumps({"language": target, "speed": speed, "voice": voice, "emotion": emotion, "prosody": "adaptive" if emotion else "neutral", "segments": [s.__dict__ for s in segments], "status": "待接入智能 TTS 引擎"}, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _ffmpeg() -> str:
        configured = os.getenv("FFMPEG_PATH", "").strip().strip('"')
        candidates = [configured] if configured else []
        candidates += [
            shutil.which("ffmpeg") or "",
            str(APP_DIR / "ffmpeg.exe"),
            str(Path(os.getenv("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages/Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe/ffmpeg-9.0.1-full_build/bin/ffmpeg.exe"),
        ]
        for item in candidates:
            if item and Path(item).exists():
                return item
        raise RuntimeError("未找到 FFmpeg，无法生成多语言 MP4。请先安装 FFmpeg 或设置 FFMPEG_PATH。")

    @staticmethod
    def _voice_for(target: str, style: str) -> str:
        code = target.split("-")[0].lower()
        if code == "vi":
            return "vi-VN-HoaiMyNeural" if "女" in style else "vi-VN-NamMinhNeural"
        if code == "en":
            return "en-US-JennyNeural" if "女" in style else "en-US-GuyNeural"
        if code == "zh":
            return "zh-CN-XiaoxiaoNeural" if "女" in style else "zh-CN-YunxiNeural"
        return "en-US-JennyNeural" if "女" in style else "en-US-GuyNeural"

    @staticmethod
    def _atempo_chain(factor: float) -> str:
        # atempo 允许 0.5–2.0，超出范围时串联多个滤镜。
        factor = max(0.05, factor)
        parts = []
        while factor > 2.0:
            parts.append("atempo=2.0"); factor /= 2.0
        while factor < 0.5:
            parts.append("atempo=0.5"); factor /= 0.5
        parts.append(f"atempo={factor:.6f}")
        return ",".join(parts)

    def render_video(self, video: str, segments: List[Segment], target: str, output: Path, voice_style: str) -> None:
        """使用 Edge TTS 生成分段配音，再由 FFmpeg 合成真实 MP4。"""
        try:
            import edge_tts
        except Exception as exc:
            raise RuntimeError(f"未安装 Edge TTS：{exc}") from exc
        ffmpeg = self._ffmpeg()
        if not segments:
            raise RuntimeError("未识别到可翻译的口播，无法生成视频。")
        work = Path(tempfile.mkdtemp(prefix="yiying_tts_", dir=str(output.parent)))
        try:
            audio_files = []
            voice = self._voice_for(target, voice_style)
            for index, seg in enumerate(segments):
                text = (seg.translated or "").strip()
                if not text:
                    continue
                path = work / f"seg_{index:04d}.mp3"
                async def save_audio(text=text, path=path):
                    await edge_tts.Communicate(text, voice=voice).save(str(path))
                try:
                    asyncio.run(save_audio())
                except Exception as exc:
                    raise RuntimeError(f"Edge TTS 生成失败（{voice}）：{exc}") from exc
                audio_files.append((seg, path))
            if not audio_files:
                raise RuntimeError("翻译结果为空，无法生成配音视频。")
            total = max(float(s.end) for s, _ in audio_files)
            source_probe = subprocess.run([ffmpeg, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(video)], capture_output=True, text=True)
            try:
                total = max(total, float((source_probe.stdout or "").strip()))
            except ValueError:
                pass
            inputs = ["-i", str(video)]
            for _, path in audio_files:
                inputs += ["-i", str(path)]
            filters = []
            labels = []
            for i, (seg, _) in enumerate(audio_files, start=1):
                duration = max(0.1, float(seg.end) - float(seg.start))
                # 用 ffprobe 获取实际时长，按原片段时长压缩/拉伸，避免口播与字幕越来越错位。
                probe = subprocess.run([ffmpeg, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(audio_files[i-1][1])], capture_output=True, text=True)
                try: actual = max(0.1, float((probe.stdout or "").strip()))
                except ValueError: actual = duration
                label = f"a{i}"
                filters.append(f"[{i}:a]aresample=48000,{self._atempo_chain(actual / duration)},atrim=duration={duration:.3f},apad,atrim=duration={total:.3f},adelay={int(max(0, seg.start) * 1000)}|{int(max(0, seg.start) * 1000)}[{label}]")
                labels.append(f"[{label}]")
            filters.append("".join(labels) + f"amix=inputs={len(labels)}:duration=longest:dropout_transition=0,atrim=duration={total:.3f}[mix]")
            # 临时 SRT 仅用于烧录到成片，不作为用户导出格式。
            srt = work / "translated.srt"
            def ts(value):
                ms = int(max(0, value) * 1000); h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000); return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
            srt.write_text("\n".join(f"{i}\n{ts(seg.start)} --> {ts(seg.end)}\n{seg.translated.strip()}\n" for i, seg in enumerate(segments, 1) if seg.translated.strip()), encoding="utf-8-sig")
            subtitle_path = str(srt).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
            subtitle_filter = f"subtitles='{subtitle_path}'"
            cmd = [ffmpeg, "-y", "-i", str(video)] + inputs[2:] + ["-filter_complex", ";".join(filters), "-map", "0:v:0", "-map", "[mix]", "-vf", subtitle_filter, "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-b:a", "128k", "-t", f"{total:.3f}", str(output)]
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0:
                detail = (result.stderr or result.stdout or "FFmpeg 未知错误").strip().splitlines()[-1]
                raise RuntimeError(f"FFmpeg 合成失败：{detail}")
        finally:
            shutil.rmtree(work, ignore_errors=True)

class ToggleSwitch(tk.Canvas):
    """Keyboard-accessible option switch matching the workspace prototype."""
    def __init__(self, master, variable):
        super().__init__(master, width=36, height=22, bg="#171a22", highlightthickness=0, takefocus=True, cursor="hand2")
        self.variable = variable
        self._trace = variable.trace_add("write", lambda *_: self._draw())
        self.bind("<Button-1>", self._toggle)
        self.bind("<space>", self._toggle)
        self.bind("<Return>", self._toggle)
        self.bind("<FocusIn>", lambda _: self._draw())
        self.bind("<FocusOut>", lambda _: self._draw())
        self._draw()
    def _toggle(self, _event=None):
        self.variable.set(not self.variable.get())
        return "break"
    def _draw(self):
        self.delete("all")
        active = self.variable.get()
        color = "#6c5ce7" if active else "#444c60"
        self.create_line(11, 11, 25, 11, width=18, fill=color, capstyle="round")
        x = 25 if active else 11
        self.create_oval(x-6, 5, x+6, 17, fill="white", outline="")
        if self.focus_get() == self:
            self.create_rectangle(1, 1, 35, 21, outline="#a99cff")
    def destroy(self):
        self.variable.trace_remove("write", self._trace)
        super().destroy()


class DedupPanel(tk.Frame):
    """嵌入式批量视频去重工作台；与翻译页共用主窗口但使用独立队列。"""
    EFFECTS = EFFECTS
    def __init__(self, master):
        super().__init__(master, bg="#0f1117")
        self.videos = []; self.sticker_dir = APP_DIR / "stickers"; self.sticker_dir.mkdir(parents=True, exist_ok=True)
        self.cancel_event = threading.Event(); self.preview_image = None
        self.worker = None
        self.events = queue.Queue()
        self.batch_name = tk.StringVar(); self.batch_count = tk.IntVar(value=1); self.opacity = tk.IntVar(value=35); self.status = tk.StringVar(value="请选择视频开始去重")
        self.opts = {name: tk.BooleanVar(value=True) for name in ("sticker", "speed", "tail", "template", "background", "effect")}
        self.effect_vars = {name: tk.BooleanVar(value=True) for name in self.EFFECTS}
        self._build()
        self._poll_id = self.after(100, self._poll_events)
    def _build(self):
        style = ttk.Style(self); style.theme_use("clam")
        style.configure("Dedup.TFrame", background="#171a22"); style.configure("Dedup.TButton", background="#282d3b", foreground="#e7ebf5", padding=(12, 8), borderwidth=0); style.map("Dedup.TButton", background=[("active", "#3b4160")])
        style.configure("Dedup.Accent.TButton", background="#6c5ce7", foreground="white", padding=(14, 9), borderwidth=0); style.map("Dedup.Accent.TButton", background=[("active", "#8174ef")])
        top = tk.Frame(self, bg="#0f1117", padx=4, pady=2); top.pack(fill="x")
        ttk.Button(top, text="＋ 添加视频", style="Dedup.TButton", command=self.add_videos).pack(side="left", padx=(0, 6))
        ttk.Button(top, text="清空队列", style="Dedup.TButton", command=self.clear_videos).pack(side="left")
        self.open_options_btn = ttk.Button(top, text="开始去重", style="Dedup.Accent.TButton", command=self.open_options)
        self.open_options_btn.pack(side="right", padx=5)
        ttk.Button(top, text="我的贴纸库", style="Dedup.TButton", command=self.sticker_library).pack(side="right", padx=5)
        body = tk.Frame(self, bg="#0f1117", padx=4, pady=12); body.pack(fill="both", expand=True)
        left = tk.Frame(body, bg="#171a22", padx=12, pady=12); left.pack(side="left", fill="both", expand=True, padx=(0, 10))
        tk.Label(left, text="待处理视频", bg="#171a22", fg="#dfe4f2", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        self.video_list = tk.Listbox(left, bg="#1b1f28", fg="#e7ebf5", selectbackground="#3a326e", relief="flat", borderwidth=0, font=("Microsoft YaHei UI", 10))
        self.video_list.pack(fill="both", expand=True, pady=(8, 0)); self.video_list.bind("<<ListboxSelect>>", self.show_preview)
        right = tk.Frame(body, bg="#171a22", padx=14, pady=12, width=430); right.pack(side="right", fill="both"); right.pack_propagate(False)
        self.preview_panel = tk.Frame(right, bg="#171a22"); self.preview_panel.pack(fill="both", expand=True)
        tk.Label(self.preview_panel, text="视频预览区域", bg="#171a22", fg="#dfe4f2", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        self.preview = DedupPreview(self.preview_panel); self.preview.pack(fill="both", expand=True, pady=8)
        tk.Label(self.preview_panel, textvariable=self.status, bg="#171a22", fg="#9aa7c2", anchor="w").pack(fill="x")
        self.drawer = ModalDrawer(self.winfo_toplevel(), self.close_options)
        self.options_panel = tk.LabelFrame(self.drawer.body, text="去重功能（可多选）", bg="#171a22", fg="#aeb8d0", padx=10, pady=8)
        options = self.options_panel
        labels = [("sticker", "视频四角随机贴纸"), ("speed", "视频随机变速 1.1x–1.2x"), ("tail", "去片尾 0.1–0.2 秒"), ("template", "透明模板"), ("background", "随机视频背景"), ("effect", "随机视频特效")]
        for i, (key, label) in enumerate(labels):
            row = tk.Frame(options, bg="#171a22")
            row.grid(row=i, column=0, columnspan=1 if i == 0 else 2, pady=6, sticky="w")
            ToggleSwitch(row, self.opts[key]).pack(side="left", padx=(0, 8))
            caption = tk.Label(row, text=label, bg="#171a22", fg="#dfe4f2", cursor="hand2")
            caption.pack(side="left")
            caption.bind("<Button-1>", lambda _, var=self.opts[key]: var.set(not var.get()))
        ttk.Button(options, text="我的贴纸库", style="Dedup.TButton", command=self.sticker_library).grid(row=0, column=1, sticky="e")
        effects = tk.Frame(options, bg="#171a22"); effects.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(2, 4))
        self.effects_controls = effects
        tk.Label(effects, text="特效强度", bg="#171a22", fg="#aeb8d0").pack(side="left", padx=(2, 6))
        tk.Scale(effects, from_=0, to=100, orient="horizontal", variable=self.opacity, bg="#171a22", fg="#dfe4f2", highlightthickness=0, troughcolor="#2a3040", length=120).pack(side="left")
        ttk.Button(effects, text="选择特效", style="Dedup.TButton", command=self.effect_picker).pack(side="right")
        self.opts["effect"].trace_add("write", lambda *_: self._update_effect_controls())
        options.grid_columnconfigure(0, weight=1)
        options.grid_rowconfigure(7, weight=1)
        name_heading = tk.Frame(options, bg="#171a22")
        name_heading.grid(row=8, column=0, columnspan=2, sticky="w", pady=(12, 6))
        tk.Label(name_heading, text="批次名称（必填）", bg="#171a22", fg="#ff909d").pack(side="left")
        tk.Label(name_heading, text="建议填写商品名称", bg="#171a22", fg="#7f8aa3", font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(8, 0))
        tk.Entry(options, textvariable=self.batch_name, bg="#242936", fg="white", insertbackground="white", relief="flat", font=("Microsoft YaHei UI", 11)).grid(row=9, column=0, columnspan=2, sticky="ew", ipady=7)
        tk.Label(options, text="生成批次（1–10）", bg="#171a22", fg="#dfe4f2").grid(row=10, column=0, sticky="w", pady=12)
        tk.Spinbox(options, from_=1, to=10, textvariable=self.batch_count, width=5, bg="#242936", fg="white", buttonbackground="#363d50").grid(row=10, column=1, sticky="w")
        bottom = tk.Frame(options, bg="#171a22"); bottom.grid(row=11, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.confirm_btn = ttk.Button(bottom, text="确定去重", style="Dedup.Accent.TButton", command=self.start_dedup)
        self.confirm_btn.pack(side="right")
        ttk.Button(bottom, text="取消", style="Dedup.TButton", command=self.close_options).pack(side="right", padx=8)
        self.batch_name.trace_add("write", lambda *_: self._update_confirm_state())
        self._update_confirm_state()
        tk.Label(self, textvariable=self.status, bg="#0f1117", fg="#9aa7c2", anchor="w").pack(fill="x", padx=4, pady=(0, 8))
        self.close_options()
    def _update_confirm_state(self):
        enabled = bool(self.batch_name.get().strip()) and not (self.worker and self.worker.is_alive())
        self.confirm_btn.configure(state="normal" if enabled else "disabled")
    def _update_effect_controls(self):
        if self.opts["effect"].get():
            self.effects_controls.grid()
        else:
            self.effects_controls.grid_remove()
    def _poll_events(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                self.status.set(value)
                if kind == "finished":
                    self.worker = None
                    self.open_options_btn.configure(state="normal")
                    self._update_confirm_state()
        except queue.Empty:
            pass
        self._poll_id = self.after(100, self._poll_events)
    def destroy(self):
        self.cancel_event.set()
        self.drawer.destroy()
        if getattr(self, "_poll_id", None):
            self.after_cancel(self._poll_id)
        super().destroy()
    def open_options(self):
        """在右侧切换到去重参数页，避免弹出新窗口。"""
        self.preview.pause()
        self.options_panel.pack(fill="both", expand=True)
        self.status.set("请选择去重功能并填写批次信息")
        self._update_effect_controls()
        self.drawer.show()
    def close_options(self):
        self.drawer.hide()
        self.options_panel.pack_forget()
    def add_videos(self):
        if self.worker and self.worker.is_alive():
            return messagebox.showinfo("正在去重", "请等待本批任务完成后添加视频。", parent=self)
        paths = filedialog.askopenfilenames(title="选择视频文件（支持常见视频格式）", filetypes=[("所有视频", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.flv *.wmv *.ts *.mts"), ("所有文件", "*.*")])
        for path in paths:
            if path not in self.videos: self.videos.append(path); self.video_list.insert("end", Path(path).name)
        if paths: self.status.set(f"已添加 {len(self.videos)} 个视频")
        if self.videos and not self.video_list.curselection():
            self.video_list.selection_set(0)
            self.show_preview()
    def clear_videos(self):
        if self.worker and self.worker.is_alive():
            return messagebox.showinfo("正在去重", "请等待本批任务完成后清空队列。", parent=self)
        self.videos.clear(); self.video_list.delete(0, "end")
        self.preview_image = None; self.preview.clear()
        self.status.set("去重队列已清空")
    def show_preview(self, _event=None):
        sel = self.video_list.curselection()
        if not sel: return
        path = self.videos[sel[0]]; self.status.set(f"预览：{Path(path).name}")
        self.preview.load(path)
    def sticker_library(self):
        win = tk.Toplevel(self); win.title("我的贴纸库"); win.geometry("620x460"); win.configure(bg="#171a22")
        scroll_area = tk.Frame(win, bg="#171a22"); scroll_area.pack(fill="both", expand=True, padx=14, pady=14)
        canvas = tk.Canvas(scroll_area, bg="#171a22", highlightthickness=0)
        scrollbar = ttk.Scrollbar(scroll_area, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y"); canvas.pack(side="left", fill="both", expand=True)
        body = tk.Frame(canvas, bg="#171a22")
        window_id = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window_id, width=event.width))
        win._sticker_refs = []
        def reload():
            for child in body.winfo_children(): child.destroy()
            win._sticker_refs.clear()
            paths = [p for p in self.sticker_dir.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")]
            if not paths:
                tk.Label(body, text="贴纸库为空，请上传图片", bg="#171a22", fg="#7f8aa3").grid(row=0, column=0, padx=20, pady=20)
                return
            for index, path in enumerate(paths):
                card = tk.Frame(body, bg="#242936", padx=8, pady=8); card.grid(row=index // 4, column=index % 4, padx=6, pady=6, sticky="nsew")
                try:
                    image = Image.open(path).convert("RGBA"); image.thumbnail((100, 100), Image.Resampling.LANCZOS)
                    photo = ImageTk.PhotoImage(image); win._sticker_refs.append(photo)
                    tk.Label(card, image=photo, bg="#242936", width=100, height=100).pack()
                except Exception:
                    tk.Label(card, text="图片无法预览", bg="#242936", fg="#d96c7c", width=14, height=6).pack()
                tk.Label(card, text=path.name[:18], bg="#242936", fg="#dfe4f2", width=14).pack(pady=(5, 3))
                ttk.Button(card, text="删除", style="Dedup.TButton", command=lambda p=path: self._delete_sticker(p, reload), state="disabled" if self.worker and self.worker.is_alive() else "normal").pack(fill="x")
        for column in range(4): body.grid_columnconfigure(column, weight=1)
        def upload():
            if self.worker and self.worker.is_alive():
                return
            for src in filedialog.askopenfilenames(parent=win, title="选择贴纸图片", filetypes=[("图片", "*.png *.jpg *.jpeg *.webp")]):
                source = Path(src)
                destination = self.sticker_dir / source.name
                if source.resolve() == destination.resolve():
                    continue
                suffix = 1
                while destination.exists():
                    destination = self.sticker_dir / f"{source.stem}_{suffix}{source.suffix}"
                    suffix += 1
                try:
                    with Image.open(source) as image: image.verify()
                    shutil.copy2(source, destination)
                except Exception as exc:
                    messagebox.showwarning("上传失败", f"{source.name}：{exc}", parent=win)
            reload()
        def close(): win.destroy()
        bar = tk.Frame(win, bg="#171a22"); bar.pack(fill="x", padx=14, pady=(0, 14)); ttk.Button(bar, text="上传图片", style="Dedup.Accent.TButton", command=upload, state="disabled" if self.worker and self.worker.is_alive() else "normal").pack(side="left"); ttk.Button(bar, text="关闭", style="Dedup.TButton", command=close).pack(side="right"); reload()
        if self.drawer.active:
            self.drawer.attach_dialog(win)
    def _delete_sticker(self, path, reload):
        if self.worker and self.worker.is_alive():
            return
        try: path.unlink()
        except OSError as exc: messagebox.showwarning("删除失败", str(exc), parent=self)
        reload()
    def effect_picker(self):
        win = EffectPicker(self, self.effect_vars)
        self.drawer.attach_dialog(win)
        return win
    def start_dedup(self):
        if self.worker and self.worker.is_alive():
            return
        name = self.batch_name.get().strip()
        if not name: return messagebox.showwarning("缺少批次名称", "请先填写批次名称后再导出。", parent=self)
        if re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((".", " ")):
            return messagebox.showwarning("批次名称无效", "批次名称不能包含路径或文件名禁用字符。", parent=self)
        try:
            batches = int(self.batch_count.get())
            if not 1 <= batches <= 10: raise ValueError()
        except (ValueError, tk.TclError):
            return messagebox.showwarning("批次数无效", "生成批次请输入 1 到 10 的整数。", parent=self)
        if not self.videos: return messagebox.showwarning("没有视频", "请先添加视频文件。", parent=self)
        if self.opts["sticker"].get() and not any(p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp") for p in self.sticker_dir.iterdir()):
            return messagebox.showwarning("未上传贴纸", "您还未上传贴纸，请去贴纸库中上传。", parent=self)
        folder = filedialog.askdirectory(title="选择去重视频导出文件夹", parent=self)
        if not folder: return
        self.cancel_event.clear(); self.status.set("正在批量去重…")
        settings = {key: var.get() for key, var in self.opts.items()}
        settings.update(opacity=self.opacity.get(), effects=[name for name, var in self.effect_vars.items() if var.get()] or self.EFFECTS)
        self.worker = threading.Thread(target=self._worker, args=(Path(folder), name, batches, tuple(self.videos), settings), daemon=True)
        self.worker.start()
        self.open_options_btn.configure(state="disabled")
        self._update_confirm_state()
        self.close_options()
    def _worker(self, folder, name, batches, videos, settings):
        total = len(videos) * batches
        done = failed = 0
        last_error = ""
        try:
            for b in range(1, batches + 1):
                out_dir = folder / f"{name}{b}"; out_dir.mkdir(parents=True, exist_ok=True)
                for serial, video in enumerate(videos, 1):
                    if self.cancel_event.is_set():
                        self.events.put(("finished", "已取消")); return
                    out = out_dir / f"{name}{b}_{serial:03d}.mp4"
                    self.events.put(("status", f"正在去重 {done + failed + 1}/{total}：{Path(video).name}"))
                    try:
                        self._process_video(video, out, b, serial, settings)
                        done += 1
                    except Exception as exc:
                        out.unlink(missing_ok=True)
                        failed += 1
                        detail = (getattr(exc, "stderr", "") or str(exc)).strip().splitlines()
                        last_error = f"{Path(video).name}：{detail[-1] if detail else '处理失败'}"
            result = f"去重完成：成功 {done} 个，失败 {failed} 个"
            if last_error: result += "；" + last_error
            self.events.put(("finished", result))
        except Exception as exc:
            self.events.put(("finished", f"导出失败：{exc}"))
    def _process_video(self, video, out, batch, serial, settings=None):
        settings = settings or {**{key: var.get() for key, var in self.opts.items()}, "opacity": self.opacity.get(), "effects": [name for name, var in self.effect_vars.items() if var.get()] or self.EFFECTS}
        ffmpeg = TranslationBackend._ffmpeg(); duration = 0.0
        import av
        with av.open(str(video)) as container:
            duration = float(container.duration or 0) / av.time_base
        factor = random.uniform(1.1, 1.2) if settings["speed"] else 1.0; trim = random.uniform(0.1, 0.2) if settings["tail"] else 0.0
        filters = []; inputs = ["-i", str(video)]; base = "[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280"
        if settings["background"]: base = "[0:v]scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,split=2[bg][fg];[bg]gblur=sigma=" + str(random.randint(12, 28)) + "[bgb];[fg]scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2:color=black@0[fg2];[bgb][fg2]overlay=(W-w)/2:(H-h)/2"
        vf = base
        if settings["template"]: vf += f",drawbox=x=0:y=0:w=iw:h=ih:color=white@{settings['opacity']/1000:.3f}:t=fill"
        if settings["effect"]:
            vf += "," + build_effect_filter(random.choice(settings["effects"]), settings["opacity"])
        if factor != 1.0: vf += f",setpts=PTS/{factor:.5f}"
        sticker_files = [p for p in self.sticker_dir.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")] if settings["sticker"] else []
        if sticker_files:
            for p in random.sample(sticker_files, min(4, len(sticker_files))): inputs += ["-i", str(p)]
        filters.append(vf + "[v0]"); label = "v0"
        for i in range(len(inputs[2::2])):
            n = i + 1; filters.append(f"[{n}:v]scale=120:120,format=rgba[s{n}];[{label}][s{n}]overlay=" + [("10:10"), ("W-w-10:10"), ("10:H-h-10"), ("W-w-10:H-h-10")][i] + f"[v{n+1}]"); label = f"v{n+1}"
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"] + inputs + ["-filter_complex", ";".join(filters), "-map", f"[{label}]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-movflags", "+faststart"]
        if factor != 1.0: cmd += ["-af", f"atempo={factor:.5f}"]
        if trim and duration > trim: cmd += ["-t", f"{max(.1, (duration-trim)/factor):.3f}"]
        cmd += [str(out)]; subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=900)


class TranslatorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("常客AI · 批量翻译短视频")
        self.geometry("1180x760"); self.minsize(980, 650)
        self.backend = TranslationBackend(); self.jobs: List[Job] = []; self.events = queue.Queue(); self.stop_event = threading.Event(); self.pause_event = threading.Event(); self.pause_event.set(); self.worker = None
        self.video_thread = None; self.video_stop = threading.Event(); self.video_pause = threading.Event(); self.video_pause.set(); self.video_path = ""; self.video_image = None
        self.video_window = None; self.video_canvas = None; self.video_frames = queue.Queue(maxsize=2); self.video_pump_id = None
        self.source_var = tk.StringVar(value="auto"); self.mode_var = tk.StringVar(value="批量翻译短视频"); self.localize_var = tk.BooleanVar(value=True); self.emotion_var = tk.BooleanVar(value=True); self.status_var = tk.StringVar(value="就绪：请选择顶部功能后添加视频")
        self._ui(); self.after(100, self._poll)
    def _ui(self):
        # 深色工作台布局：左侧功能导航、顶部项目操作区、中央参数卡片与批量队列。
        self.configure(bg="#0f1117")
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("App.TFrame", background="#0f1117")
        style.configure("Card.TLabelframe", background="#171a22", foreground="#dfe4f2", bordercolor="#2a3040", relief="solid")
        style.configure("Card.TLabelframe.Label", background="#171a22", foreground="#aeb8d0", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("App.TLabel", background="#171a22", foreground="#dfe4f2")
        style.configure("Muted.TLabel", background="#171a22", foreground="#7f8aa3")
        style.configure("Top.TButton", background="#282d3b", foreground="#e7ebf5", borderwidth=0, padding=(12, 8))
        style.map("Top.TButton", background=[("active", "#363d50")])
        style.configure("Remove.TButton", background="#282d3b", foreground="#e7ebf5", borderwidth=0, padding=(12, 8))
        style.map("Remove.TButton", background=[("active", "#363d50"), ("disabled", "#1b1f28")], foreground=[("disabled", "#596176")])
        style.configure("Mark.TButton", background="#3d2932", foreground="#ffb8c2", borderwidth=0, padding=(12, 8))
        style.map("Mark.TButton", background=[("active", "#633743"), ("disabled", "#1b1f28")], foreground=[("disabled", "#596176")])
        style.configure("Accent.TButton", background="#6c5ce7", foreground="#ffffff", borderwidth=0, padding=(16, 9), font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#8174ef")])
        style.configure("TCombobox", fieldbackground="#242936", background="#242936", foreground="#e7ebf5", arrowcolor="#aab4cc")
        style.configure("Source.TCombobox", fieldbackground="#283452", background="#283452", foreground="#ffffff", arrowcolor="#dce5ff", borderwidth=1, padding=5, font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Source.TCombobox", fieldbackground=[("readonly", "#283452"), ("focus", "#334570")], foreground=[("readonly", "#ffffff"), ("focus", "#ffffff")], selectbackground=[("readonly", "#283452")], selectforeground=[("readonly", "#ffffff")])
        style.configure("TCheckbutton", background="#171a22", foreground="#cbd3e5")
        style.map("TCheckbutton", background=[("active", "#171a22")])
        style.configure("Treeview", background="#1b1f28", fieldbackground="#1b1f28", foreground="#dfe4ef", rowheight=31, borderwidth=0)
        style.configure("Treeview.Heading", background="#242a38", foreground="#aeb8d0", relief="flat", font=("Microsoft YaHei UI", 9, "bold"))
        style.map("Treeview", background=[("selected", "#3a326e")], foreground=[("selected", "#ffffff")])

        shell = tk.Frame(self, bg="#0f1117"); shell.pack(fill="both", expand=True)
        sidebar = tk.Frame(shell, bg="#151821", width=220, padx=18, pady=22); sidebar.pack(side="left", fill="y"); sidebar.pack_propagate(False)
        tk.Label(sidebar, text="常客AI", bg="#151821", fg="#ffffff", font=("Microsoft YaHei UI", 25, "bold")).pack(anchor="w")
        tk.Label(sidebar, text="CHANGKE AI  /  VIDEO", bg="#151821", fg="#6c5ce7", font=("Microsoft YaHei UI", 8, "bold")).pack(anchor="w", pady=(0, 30))
        tk.Label(sidebar, text="design by YYH", bg="#151821", fg="#a99cff", font=("Microsoft YaHei UI", 9, "italic")).pack(anchor="w", pady=(0, 22))
        tk.Label(sidebar, text="工作台", bg="#151821", fg="#7f8aa3", font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(0, 8))
        self.nav_items = [("▣  批量翻译短视频", "批量翻译短视频"), ("◈  视频批量去重", "视频批量去重"), ("◇  中文视频转外语视频", "中文视频转外语视频"), ("＋  后续功能", "后续功能")]
        self.nav_page = 0; self.nav_page_size = len(self.nav_items); self.nav_buttons = {}
        self.nav_frame = tk.Frame(sidebar, bg="#151821"); self.nav_frame.pack(fill="x")
        self.render_nav_page()
        tk.Frame(sidebar, bg="#252a37", height=1).pack(fill="x", pady=24)
        tk.Label(sidebar, text="当前引擎", bg="#151821", fg="#7f8aa3", font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        self.sidebar_engine = tk.Label(sidebar, text="●  " + self.backend.engine_label, bg="#151821", fg="#52d6b1", font=("Microsoft YaHei UI", 9)); self.sidebar_engine.pack(anchor="w", pady=(7, 0))
        tk.Label(sidebar, text="faster-whisper 本地识别", bg="#151821", fg="#69748d", font=("Microsoft YaHei UI", 8)).pack(anchor="w", pady=(5, 0))
        tk.Label(sidebar, text="v2.0.3  ·  Windows", bg="#151821", fg="#50596e", font=("Microsoft YaHei UI", 8)).pack(side="bottom", anchor="w")

        content = tk.Frame(shell, bg="#0f1117", padx=22, pady=18); content.pack(side="left", fill="both", expand=True)
        top = tk.Frame(content, bg="#0f1117"); top.pack(fill="x", pady=(0, 15))
        self.page_title = tk.Label(top, text="批量翻译短视频", bg="#0f1117", fg="#ffffff", font=("Microsoft YaHei UI", 20, "bold")); self.page_title.pack(side="left")
        self.start = ttk.Button(top, text="▶  开始批量处理", style="Accent.TButton", command=self.start_jobs); self.start.pack(side="right")
        self.workspace = tk.Frame(content, bg="#0f1117"); self.workspace.pack(fill="both", expand=True)

        settings = ttk.LabelFrame(self.workspace, text="翻译设置", style="Card.TLabelframe", padding=12); settings.pack(fill="x")
        line = tk.Frame(settings, bg="#171a22"); line.pack(fill="x")
        tk.Label(line, text="识别语言", bg="#171a22", fg="#d7def3", font=("Microsoft YaHei UI", 10, "bold")).pack(side="left")
        source_values = ["自动识别（推荐）"] + [n for c, n in LANGUAGES if c != "auto"]
        self.source_combo = ttk.Combobox(line, state="readonly", width=18, style="Source.TCombobox", values=source_values); self.source_combo.pack(side="left", padx=10); self.source_combo.set("自动识别（推荐）" if self.source_var.get() == "auto" else LANG_NAME[self.source_var.get()]); self.source_combo.bind("<<ComboboxSelected>>", self.source_changed)
        engine = tk.Frame(settings, bg="#171a22"); engine.pack(fill="x", pady=(10, 0))
        tk.Label(engine, text="翻译引擎", bg="#171a22", fg="#9eabc4", font=("Microsoft YaHei UI", 9)).pack(side="left")
        self.provider_var = tk.StringVar(value=self.backend.engine_label)
        self.provider_combo = ttk.Combobox(engine, textvariable=self.provider_var, state="readonly", width=19,
            values=("Ollama 本地翻译", "DeepSeek 云端翻译", "DeepL 在线翻译"), style="Source.TCombobox")
        self.provider_combo.pack(side="left", padx=8)
        self.provider_combo.bind("<<ComboboxSelected>>", self.provider_changed)
        self.engine_note = tk.Label(settings, text=self.backend.engine_description, bg="#171a22", fg="#d1a46f", font=("Microsoft YaHei UI", 9)); self.engine_note.pack(anchor="w", pady=(7, 0))
        # 语境、情绪、语速和音色选项已移除，统一使用程序默认的中文处理参数。

        files = ttk.LabelFrame(self.workspace, text="批量队列", style="Card.TLabelframe", padding=12); files.pack(fill="both", expand=True, pady=(14, 0))
        bar = tk.Frame(files, bg="#171a22"); bar.pack(fill="x", pady=(0, 9))
        ttk.Button(bar, text="＋ 添加视频", style="Top.TButton", command=self.add).pack(side="left")
        self.remove_btn = ttk.Button(bar, text="移除选中", style="Remove.TButton", command=self.remove, state="disabled")
        self.remove_btn.pack(side="left", padx=6)
        self.mark_btn = ttk.Button(bar, text="标记需要处理的视频", style="Mark.TButton", command=self.mark_needs_attention, state="disabled")
        self.mark_btn.pack(side="left", padx=6)
        ttk.Button(bar, text="清空队列", style="Top.TButton", command=self.clear).pack(side="left")
        tk.Label(bar, text="完成后可点击视频查看中文译文", bg="#171a22", fg="#7f8aa3", font=("Microsoft YaHei UI", 9)).pack(side="left", padx=(22, 6))
        pane = tk.Frame(files, bg="#171a22"); pane.pack(fill="both", expand=True)
        queue_panel = tk.Frame(pane, bg="#171a22"); queue_panel.pack(side="left", fill="both", expand=True, padx=(0, 10))
        cols = ("video", "target", "status", "progress", "review"); self.tree = ttk.Treeview(queue_panel, columns=cols, show="headings", selectmode="extended")
        heads = {"video":"视频", "target":"目标语言", "status":"状态", "progress":"进度", "review":"是否需要处理"}; widths = {"video":300, "target":100, "status":100, "progress":70, "review":110}
        for c in cols: self.tree.heading(c, text=heads[c]); self.tree.column(c, width=widths[c], anchor="w")
        self.tree.tag_configure("attention", foreground="#ff626e")
        self.tree.tag_configure("normal", foreground="#dfe4ef")
        sb = ttk.Scrollbar(queue_panel, orient="vertical", command=self.tree.yview); self.tree.configure(yscrollcommand=sb.set); self.tree.pack(side="left", fill="both", expand=True); sb.pack(side="right", fill="y")
        preview = tk.Frame(pane, bg="#202532", width=420, padx=14, pady=12); preview.pack(side="right", fill="both"); preview.pack_propagate(False)
        tk.Label(preview, text="当前视频翻译", bg="#202532", fg="#ffffff", font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        self.preview_title = tk.Label(preview, text="点击左侧视频查看译文", bg="#202532", fg="#8f9ab2", anchor="w", justify="left", wraplength=370, font=("Microsoft YaHei UI", 9)); self.preview_title.pack(fill="x", pady=(5, 8))
        self.preview_text = tk.Text(preview, bg="#171b24", fg="#e7ebf5", insertbackground="#ffffff", relief="flat", wrap="word", font=("Microsoft YaHei UI", 10), padx=10, pady=10)
        self.preview_text.pack(fill="both", expand=True); self.preview_text.configure(state="disabled")
        self.tree.bind("<<TreeviewSelect>>", self.show_selected_translation)
        self.tree.bind("<Button-1>", self.handle_video_button)
        bottom = tk.Frame(self.workspace, bg="#0f1117"); bottom.pack(fill="x", pady=(12, 0)); self.pause_btn = ttk.Button(bottom, text="暂停", style="Top.TButton", command=self.toggle_pause); self.pause_btn.pack(side="left"); ttk.Button(bottom, text="重新处理", style="Top.TButton", command=self.reprocess).pack(side="left", padx=6); ttk.Button(bottom, text="停止", style="Top.TButton", command=self.stop).pack(side="left", padx=6); tk.Label(bottom, textvariable=self.status_var, bg="#0f1117", fg="#7f8aa3", font=("Microsoft YaHei UI", 9)).pack(side="right")
        self.translation_page = (settings, files, bottom)
        self.dedup_page = None

    def render_nav_page(self):
        for child in self.nav_frame.winfo_children():
            child.destroy()
        self.nav_buttons.clear()
        start = self.nav_page * self.nav_page_size
        for label, mode in self.nav_items[start:start + self.nav_page_size]:
            active = mode == self.mode_var.get()
            b = tk.Button(self.nav_frame, text=label, anchor="w", command=lambda m=mode: self.select_mode(m), bg="#242936" if active else "#151821", activebackground="#302b55", activeforeground="#ffffff", fg="#e5e9f3", relief="flat", bd=0, padx=12, pady=11, font=("Microsoft YaHei UI", 10), cursor="hand2")
            b.pack(fill="x", pady=3); self.nav_buttons[mode] = b
        # 所有功能入口直接显示，避免分页按钮占用侧栏空间。
    def change_nav_page(self, delta):
        pages = max(1, (len(self.nav_items) + self.nav_page_size - 1) // self.nav_page_size)
        self.nav_page = max(0, min(pages - 1, self.nav_page + delta))
        self.render_nav_page()
    def select_mode(self, mode):
        if self.dedup_page is not None and self.dedup_page.drawer.active:
            return
        if mode == "视频批量去重":
            self.mode_var.set(mode)
            self.title(f"常客AI · {mode}")
            self.page_title.configure(text=mode)
            for widget in self.translation_page:
                widget.pack_forget()
            if self.dedup_page is None:
                self.dedup_page = DedupPanel(self.workspace)
            self.dedup_page.pack(fill="both", expand=True)
            self.start.pack_forget()
            self.render_nav_page()
            return
        if mode in ("中文视频转外语视频", "后续功能"):
            messagebox.showinfo("功能预留", f"“{mode}”已预留入口，当前版本暂未启用。")
            return
        self.mode_var.set(mode); self.title(f"常客AI · {mode}")
        self.page_title.configure(text=mode)
        if self.dedup_page is not None:
            self.dedup_page.preview.pause()
            self.dedup_page.pack_forget()
        settings, files, bottom = self.translation_page
        settings.pack(fill="x")
        files.pack(fill="both", expand=True, pady=(14, 0))
        bottom.pack(fill="x", pady=(12, 0))
        self.start.pack(side="right")
        self.render_nav_page()
        if mode == "批量翻译短视频":
            if not (self.worker and self.worker.is_alive()):
                self.status_var.set("批量翻译短视频：已保留队列与翻译设置")
        else:
            self.source_var.set("auto"); self.source_combo.set("自动识别（推荐）"); self.status_var.set("视频模式：自动识别口播 → 中文")
    def source_changed(self, _event=None):
        selected = self.source_combo.get()
        if selected.startswith("自动识别"):
            self.source_var.set("auto")
        else:
            self.source_var.set(next((code for code, name in LANGUAGES if name == selected), "auto"))
    def provider_changed(self, _event=None):
        selected = self.provider_var.get()
        if selected.startswith("Ollama"):
            self.backend.engine = "ollama"
        elif selected.startswith("DeepSeek"):
            self.backend.engine = "deepseek"
        else:
            self.backend.engine = "deepl"
        self.engine_note.configure(text=self.backend.engine_description)
        self.sidebar_engine.configure(text="●  " + self.backend.engine_label)
        if _event is not None:
            path = APP_DIR / "translation_config.json"
            try:
                saved = json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
                saved["translation_engine"] = self.backend.engine
                path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
            except (OSError, ValueError) as exc:
                self.status_var.set(f"本次已切换引擎，但无法保存配置：{exc}")
    def swap(self):
        # 目标语言固定为中文；按钮仅在自动识别与中文源语言之间切换。
        if self.source_var.get() == "zh-CN":
            self.source_var.set("auto"); self.source_combo.set("自动识别（推荐）"); self.status_var.set("已切换：自动识别 → 中文")
        else:
            self.source_var.set("zh-CN"); self.source_combo.set(LANG_NAME["zh-CN"]); self.status_var.set("已切换：中文 → 中文（字幕/配音处理）")
    def targets(self):
        # 产品输出统一为简体中文，忽略旧配置或旧队列中的其它目标语言。
        return [TARGET_LANGUAGE[0]]
    def add(self):
        paths = filedialog.askopenfilenames(title="选择视频文件（可一次选择 50–100 个）", filetypes=VIDEO_TYPES)
        targets = self.targets()
        if paths and not targets: messagebox.showwarning("未选择目标语言", "请至少选择一个目标语言。"); return
        existing = {(j.video,j.target,j.mode) for j in self.jobs}
        for p in paths:
            for t in targets:
                k=(p,t,self.mode_var.get())
                if k not in existing: self.jobs.append(Job(p,self.source_var.get(),t,self.mode_var.get()))
        self.refresh(); self.status_var.set(f"已加入 {len(paths)} 个视频，共 {len(self.jobs)} 个翻译任务")
    def remove(self):
        if self.worker and self.worker.is_alive(): return
        chosen={self.tree.index(i) for i in self.tree.selection()}; self.jobs=[j for i,j in enumerate(self.jobs) if i not in chosen]; self.refresh()

    def update_remove_button(self):
        """只有选中队列视频时才允许移除，避免误操作。"""
        if not hasattr(self, "remove_btn"):
            return
        enabled = bool(self.tree.selection()) and not (self.worker and self.worker.is_alive())
        self.remove_btn.configure(state="normal" if enabled else "disabled")
        self.mark_btn.configure(state="normal" if enabled else "disabled")

    def mark_needs_attention(self):
        """给选中的原视频文件名追加“_需要处理”，便于人工回看。"""
        if self.worker and self.worker.is_alive():
            return messagebox.showinfo("处理中", "请先停止当前任务，再标记原视频文件。")
        selected = self.tree.selection()
        if not selected:
            return messagebox.showinfo("未选择视频", "请先在队列中选择需要标记的视频。")
        renamed = 0
        errors = []
        for item in selected:
            idx = self.tree.index(item)
            if idx >= len(self.jobs):
                continue
            job = self.jobs[idx]
            old_path = Path(job.video)
            if old_path.stem.endswith("_需要处理"):
                job.needs_attention = True
                continue
            new_path = old_path.with_name(old_path.stem + "_需要处理" + old_path.suffix)
            try:
                if old_path.exists():
                    old_path.rename(new_path)
                    job.video = str(new_path)
                job.needs_attention = True
                renamed += 1
            except OSError as exc:
                errors.append(f"{old_path.name}：{exc}")
        self.refresh()
        if errors:
            messagebox.showwarning("部分标记失败", "\n".join(errors[:3]))
        else:
            self.status_var.set(f"已标记 {renamed} 个视频，文件名已追加“_需要处理”")
    def clear(self):
        if self.worker and self.worker.is_alive(): return messagebox.showinfo("处理中", "请先停止当前任务")
        self.jobs.clear(); self.refresh(); self.status_var.set("队列已清空")
    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for j in self.jobs:
            review = "需要处理" if j.needs_attention else "正常"
            tag = "attention" if j.needs_attention else "normal"
            self.tree.insert("", "end", values=(f"▶  {Path(j.video).name}", LANG_NAME.get(j.target,j.target), j.status, f"{j.progress}%", review), tags=(tag,))
        self.update_remove_button()

    def handle_video_button(self, event):
        """点击视频列最左侧的播放符号时，打开独立的视频预览窗口。"""
        row = self.tree.identify_row(event.y); column = self.tree.identify_column(event.x)
        if row and column == "#1" and event.x <= 34:
            self.tree.selection_set(row)
            self.play_job(self.jobs[self.tree.index(row)])
            return "break"

    def play_job(self, job):
        path = str(Path(job.video))
        if not Path(path).exists():
            messagebox.showwarning("无法预览", "找不到视频文件。")
            return
        self.close_video_window()
        self.video_path = path; self.video_stop.clear(); self.video_pause.clear()
        self.video_window = tk.Toplevel(self)
        self.video_window.title(f"视频预览 · {Path(path).name}")
        self.video_window.geometry("760x520"); self.video_window.minsize(520, 360)
        self.video_window.configure(bg="#10131b")
        self.video_window.protocol("WM_DELETE_WINDOW", self.close_video_window)
        self.video_canvas = tk.Canvas(self.video_window, bg="#05070b", highlightthickness=0)
        self.video_canvas.pack(fill="both", expand=True, padx=12, pady=(12, 8))
        controls = tk.Frame(self.video_window, bg="#10131b"); controls.pack(fill="x", padx=12, pady=(0, 12))
        self.video_play_btn = ttk.Button(controls, text="Ⅱ 暂停", style="Top.TButton", command=self.toggle_video_play); self.video_play_btn.pack(side="left")
        tk.Label(controls, text="独立预览窗口 · 翻译预览保持不变", bg="#10131b", fg="#7f8aa3", font=("Microsoft YaHei UI", 9)).pack(side="left", padx=10)
        self.video_window.update_idletasks()
        self.video_pump_id = self.after(15, self._pump_video_frame)
        self.video_thread = threading.Thread(target=self._video_loop, args=(path,), daemon=True); self.video_thread.start()

    def toggle_video_play(self):
        if not self.video_path:
            selected = self.tree.selection()
            if selected:
                self.play_job(self.jobs[self.tree.index(selected[0])])
            return
        if self.video_pause.is_set():
            self.video_pause.clear(); self.video_play_btn.configure(text="Ⅱ 暂停")
        else:
            self.video_pause.set(); self.video_play_btn.configure(text="▶ 播放")

    def close_video_window(self):
        self.video_stop.set(); self.video_pause.set()
        self.video_path = ""
        if self.video_pump_id:
            try: self.after_cancel(self.video_pump_id)
            except tk.TclError: pass
            self.video_pump_id = None
        if self.video_window and self.video_window.winfo_exists():
            self.video_window.destroy()
        self.video_window = None; self.video_canvas = None; self.video_image = None
        while True:
            try: self.video_frames.get_nowait()
            except queue.Empty: break

    def _pump_video_frame(self):
        if not self.video_window or not self.video_window.winfo_exists() or not self.video_canvas:
            return
        image = None
        while True:
            try: image = self.video_frames.get_nowait()
            except queue.Empty: break
        if image is not None:
            width = max(180, self.video_canvas.winfo_width() - 4); height = max(180, self.video_canvas.winfo_height() - 4)
            image.thumbnail((width, height), Image.Resampling.LANCZOS)
            self.video_image = ImageTk.PhotoImage(image)
            self.video_canvas.delete("all")
            self.video_canvas.create_image(width // 2, height // 2, image=self.video_image)
        self.video_pump_id = self.after(15, self._pump_video_frame)

    def _video_loop(self, path):
        try:
            import av
            container = av.open(path)
            stream = next((s for s in container.streams if s.type == "video"), None)
            if stream is None:
                raise RuntimeError("视频没有画面轨道")
            rate = float(stream.average_rate) if stream.average_rate else 25.0
            interval = 1.0 / max(1.0, min(rate, 60.0))
            next_frame_at = time.perf_counter()
            for frame in container.decode(stream):
                if self.video_stop.is_set() or path != self.video_path:
                    break
                # 暂停期间冻结播放时钟，恢复后不会跳帧或突然加速。
                paused_for = 0.0
                while self.video_pause.is_set() and not self.video_stop.is_set():
                    pause_started = time.perf_counter()
                    time.sleep(0.03)
                    paused_for += time.perf_counter() - pause_started
                if self.video_stop.is_set():
                    break
                next_frame_at += paused_for
                # 按原始帧率调度，等待时间扣除解码和图像转换耗时，避免出现 0.5 倍速。
                wait = next_frame_at - time.perf_counter()
                if wait > 0:
                    time.sleep(wait)
                # 按原始宽高比缩放，避免竖屏、方形视频被强行拉成 16:9。
                source_w, source_h = max(1, frame.width), max(1, frame.height)
                scale = min(960 / source_w, 540 / source_h, 1.0)
                render_w = max(1, int(source_w * scale)); render_h = max(1, int(source_h * scale))
                image = Image.fromarray(frame.reformat(width=render_w, height=render_h, format="rgb24").to_ndarray())
                # 只保留最新两帧，避免解码速度超过界面刷新速度时出现延迟堆积。
                try:
                    if self.video_frames.full(): self.video_frames.get_nowait()
                    self.video_frames.put_nowait(image)
                except queue.Full:
                    pass
                next_frame_at += interval
                # 如果某一帧解码超过帧间隔，直接追上当前时间，不让延迟越积越多。
                if next_frame_at < time.perf_counter():
                    next_frame_at = time.perf_counter()
            container.close()
        except Exception:
            # 预览失败不影响翻译主流程，窗口保持可关闭。
            return
    def show_selected_translation(self, _event=None):
        """点击队列中的视频后，在右侧显示对应的中文译文。"""
        selected = self.tree.selection()
        self.update_remove_button()
        if not selected or not hasattr(self, "preview_text"):
            return
        idx = self.tree.index(selected[0])
        if idx >= len(self.jobs):
            return
        job = self.jobs[idx]
        stem = re.sub(r"[^\w\-\u4e00-\u9fff]+", "_", Path(job.video).stem)
        content = job.translation_text
        if not content:
            content = "该视频尚未完成翻译。\n\n处理完成后，点击此视频即可在这里查看中文译文。"
        self.preview_title.configure(text=f"{Path(job.video).name}\n状态：{job.status}")
        self.preview_text.configure(state="normal")
        self.preview_text.delete("1.0", "end")
        self.preview_text.tag_configure("timestamp", foreground="#68738b", font=("Microsoft YaHei UI", 9))
        self.preview_text.tag_configure("price", foreground=PRICE_HIGHLIGHT_COLOR, background="#3b321c", font=("Microsoft YaHei UI", 10, "bold"))
        self.preview_text.tag_configure("promotion", foreground=PROMOTION_HIGHLIGHT_COLOR, background="#3a2029", font=("Microsoft YaHei UI", 10, "bold"))
        cursor = 0
        timestamp_ranges = [(m.start(), m.end(), "timestamp") for m in re.finditer(r"\[\d{2}:\d{2}:\d{2} - \d{2}:\d{2}:\d{2}\]", content)]
        ranges = sorted(timestamp_ranges + [(start, end, tag) for start, end, tag in find_highlights(content)], key=lambda item: item[0])
        for start, end, tag in ranges:
            if start > cursor:
                self.preview_text.insert("end", content[cursor:start])
            self.preview_text.insert("end", content[start:end], tag)
            cursor = end
        if cursor < len(content):
            self.preview_text.insert("end", content[cursor:])
        self.preview_text.configure(state="disabled")
    def start_jobs(self):
        if self.worker and self.worker.is_alive(): return
        if not self.jobs: return messagebox.showinfo("没有任务", "请先添加视频文件。")
        self.provider_changed(); self._run_options = (self.localize_var.get(), self.emotion_var.get())
        self.provider_combo.configure(state="disabled")
        self.stop_event.clear(); self.pause_event.set(); self.pause_btn.configure(text="暂停"); self.start.configure(state="disabled"); self.worker=threading.Thread(target=self.run, daemon=True); self.worker.start(); self.update_remove_button()
    def toggle_pause(self):
        if not (self.worker and self.worker.is_alive()): return
        if self.pause_event.is_set():
            self.pause_event.clear(); self.pause_btn.configure(text="继续"); self.status_var.set("已暂停：当前任务完成后等待继续")
        else:
            self.pause_event.set(); self.pause_btn.configure(text="暂停"); self.status_var.set("已继续处理")
    def reprocess(self):
        if self.worker and self.worker.is_alive(): return messagebox.showinfo("处理中", "请先暂停或停止当前任务。")
        selected = self.tree.selection()
        indices = [self.tree.index(item) for item in selected]
        if not indices:
            indices = [i for i, j in enumerate(self.jobs) if j.status == "失败"]
        if not indices:
            return messagebox.showinfo("没有可重处理任务", "请在队列中选择任务，或先处理失败任务。")
        for i in indices:
            j = self.jobs[i]; j.status, j.progress, j.output, j.error, j.needs_attention, j.translation_text = "等待中", 0, "", "", False, ""
        self.refresh(); self.status_var.set(f"已重置 {len(indices)} 个任务，请点击开始批量处理")
    @staticmethod
    def friendly_error(exc: Exception) -> str:
        """将网络/模型错误转换为用户能直接处理的提示。"""
        text = str(exc).strip() or exc.__class__.__name__
        lowered = text.lower()
        if "ollama" in lowered:
            return text
        if "deepseek" in lowered:
            if "429" in lowered or "too many requests" in lowered:
                return "DeepSeek 请求过于频繁（HTTP 429），请稍后重试。"
            if "401" in lowered or "403" in lowered or "认证" in text or "unauthorized" in lowered:
                return "DeepSeek 接口认证失败，请检查 DEEPSEEK_API_KEY 或 deepseek_api_key.txt。"
            return "DeepSeek 翻译失败：" + text
        if "out of memory" in lowered or "cuda failed with error out of memory" in lowered:
            return "GPU 显存不足，请关闭其他占用显卡的程序后重试。" + text
        if "cudnn" in lowered or "cublas" in lowered:
            return "GPU 运行库缺失或版本不匹配，请检查本地运行环境的 cuda 文件夹。" + text
        if "http error 429" in lowered or "too many requests" in lowered:
            return "DeepL 请求过于频繁（HTTP 429），请稍后重试。"
        if "http error 401" in lowered or "http error 403" in lowered or "forbidden" in lowered:
            return "翻译接口拒绝请求，请检查 API 密钥、配额和接口权限。"
        if "deepl 翻译请求失败" in text.lower():
            return text + "请检查 DeepL API Key 和账户额度。"
        if "timed out" in lowered or "urlopen error" in lowered or "connection" in lowered:
            return "无法连接翻译服务，请检查网络后重试。"
        if "edge tts" in lowered:
            return text + "（Edge TTS 需要联网，请检查网络或稍后重试）"
        if "本地语音识别失败" in text:
            return text + "（请确认 faster-whisper 模型已下载）"
        return text
    def stop(self):
        if self.worker and self.worker.is_alive(): self.stop_event.set(); self.pause_event.set(); self.pause_btn.configure(text="暂停"); self.status_var.set("正在停止…")
    def run(self):
        jobs=list(self.jobs); done=0
        prepared = {}
        # The 6 GB GPU is shared: recognize the batch first, then release Whisper
        # before loading the LLM. Never keep two large models resident together.
        if self.backend.engine == "ollama":
            try:
                self.events.put(("status", "正在准备本地模型；批量识别后自动切换到中文翻译"))
                self.backend._ollama.unload()
            except Exception as exc:
                for idx, j in enumerate(jobs):
                    self.events.put(("e", (idx, self.friendly_error(exc))))
                self.events.put(("done", None))
                return
            def recognize(idx, j):
                self.pause_event.wait()
                if self.stop_event.is_set(): return
                try:
                    self.events.put(("u", (idx, "本地识别口播", 15)))
                    prepared[idx] = self.backend.transcribe_with_language(j.video, j.source)
                    self.events.put(("u", (idx, "已识别，待批量翻译", 50)))
                except Exception as exc:
                    self.events.put(("e", (idx, self.friendly_error(exc))))
            with ThreadPoolExecutor(max_workers=min(10, max(1, len(jobs)))) as pool:
                futures = [pool.submit(recognize, i, job) for i, job in enumerate(jobs)]
                for future in futures:
                    future.result()
            self.backend.release_recognition_model()
        def one(idx,j):
            if self.stop_event.is_set(): return
            self.pause_event.wait()
            if self.stop_event.is_set(): return
            try:
                if self.backend.engine == "ollama":
                    if idx not in prepared: return
                    seg, detected_source = prepared[idx]
                else:
                    self.events.put(("u",(idx,"本地识别（首次加载模型）" if self.backend._whisper is None else "本地识别口播",15)))
                    seg, detected_source = self.backend.transcribe_with_language(j.video,j.source)
                self.pause_event.wait()
                if self.stop_event.is_set(): return
                self.events.put(("u",(idx,"等待翻译",50)))
                with self.backend._translation_slots:
                    self.pause_event.wait()
                    if self.stop_event.is_set(): return
                    self.events.put(("u",(idx,self.backend.engine_label,55)))
                    localize, emotion = getattr(self, "_run_options", (True, True))
                    seg=self.backend.translate(seg,j.source,j.target,localize,emotion,detected_source)
                self.pause_event.wait()
                if self.stop_event.is_set(): return
                if not seg or not any((s.translated or "").strip() for s in seg):
                    raise RuntimeError("翻译服务未返回有效译文，未生成输出文件。")
                attention = bool(find_highlights("\n".join((s.translated or "").strip() for s in seg)))
                files=[]
                j.translation_text = self.format_translation(j, seg)
                self.events.put(("u",(idx,"已完成",100))); self.events.put(("o",(idx,"", attention, j.translation_text)) )
            except Exception as e: self.events.put(("e",(idx,self.friendly_error(e))))
        # 批量队列最多同时处理 10 个视频，充分利用音频分离和本地识别的并行能力。
        # DeepL 每个视频仍按段合并请求，降低单次请求数量。
        workers = min(10, max(1, len(jobs)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            fs=[pool.submit(one,i,j) for i,j in enumerate(jobs)]
            for f in fs: f.result(); done += 1; self.events.put(("a",done))
        self.events.put(("done",None))
    @staticmethod
    def to_srt(segs):
        def ts(v):
            ms=int(v*1000); h,ms=divmod(ms,3600000); m,ms=divmod(ms,60000); s,ms=divmod(ms,1000); return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
        return "\n".join(f"{i}\n{ts(s.start)} --> {ts(s.end)}\n{s.translated or s.text}\n" for i,s in enumerate(segs,1))
    @staticmethod
    def format_translation(job, segs):
        # 右侧预览使用时间段格式，方便核对口播内容。
        def stamp(value):
            total = max(0, int(value)); return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"
        return "\n\n".join(f"[{stamp(seg.start)} - {stamp(seg.end)}]\n{(seg.translated or seg.text).strip()}" for seg in segs if (seg.translated or seg.text).strip()) + "\n"
    @staticmethod
    def to_html(job,segs):
        rows="".join(f"<tr><td>{s.start:.1f}s–{s.end:.1f}s</td><td>{s.text}</td><td>{s.translated}</td></tr>" for s in segs)
        return f"<!doctype html><meta charset='utf-8'><title>{Path(job.video).name}</title><h1>{Path(job.video).name}</h1><p>{LANG_NAME.get(job.source,job.source)} → {LANG_NAME.get(job.target,job.target)}</p><table border='1' cellpadding='8'><tr><th>时间</th><th>原文</th><th>译文</th></tr>{rows}</table>"
    def _poll(self):
        try:
            while True:
                kind,p=self.events.get_nowait()
                if kind=="status": self.status_var.set(p)
                elif kind=="u": self.jobs[p[0]].status,self.jobs[p[0]].progress=p[1],p[2]
                elif kind=="o":
                    self.jobs[p[0]].output=p[1]; self.jobs[p[0]].needs_attention=bool(p[2]) if len(p) > 2 else False
                    if len(p) > 3: self.jobs[p[0]].translation_text = p[3]
                elif kind=="e":
                    self.jobs[p[0]].status,self.jobs[p[0]].error="失败",p[1]
                    self.status_var.set(f"任务失败：{Path(self.jobs[p[0]].video).name} · {p[1]}")
                elif kind=="a": self.status_var.set(f"已完成 {p}/{len(self.jobs)} 个任务")
                elif kind=="done":
                    self.start.configure(state="normal")
                    self.provider_combo.configure(state="readonly")
                    self.pause_btn.configure(text="暂停"); self.pause_event.set()
                    if self.stop_event.is_set():
                        self.status_var.set("已停止：已完成的译文保留在右侧预览")
                    else:
                        failed = sum(1 for j in self.jobs if j.status == "失败")
                        success = len(self.jobs) - failed
                        self.status_var.set(f"处理结束：成功 {success} 个，失败 {failed} 个" if failed else f"处理完成：{success} 个任务全部成功")
                self.refresh()
        except queue.Empty: pass
        self.after(100,self._poll)
if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--translation-check":
        from performance_checks.translation_batch_check import run
        sys.exit(run(sys.modules[__name__], sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == "--self-check":
        from package_self_check import run
        sys.exit(run(sys.modules[__name__], sys.argv[2]))
    TranslatorApp().mainloop()

