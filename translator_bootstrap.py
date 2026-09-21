# -*- coding: utf-8 -*-
"""Bootstrap the verified desktop translator payload and repair UI event polling."""
from __future__ import annotations
import marshal, queue, sys, types, traceback, os, shutil, tempfile, hashlib, threading, time
import json, urllib.parse, urllib.request
from pathlib import Path

try:
    _payload = Path(__file__).with_name("translator_desktop_payload.bin").read_bytes()
except Exception:
    (Path(sys.executable).parent / "bootstrap_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    raise
_module = types.ModuleType("translator_desktop_payload")
_module.__file__ = str(Path(__file__).with_name("translator_desktop_payload.py"))
sys.modules[_module.__name__] = _module
exec(marshal.loads(_payload), _module.__dict__)
globals().update(_module.__dict__)

# Standalone Windows mode: no login page or team-management gate.  The
# desktop client keeps using the local Whisper model and translates through
# the DeepL key stored beside the executable.
class _NoAuthClient:
    def get_access(self):
        return {"user": {"phone": "本地模式", "role": "owner", "displayName": "本地用户"}, "canTranslate": True}
    def translate_segments(self, segments, source_language=""):
        return [{"start": float(s.get("start", 0)), "end": float(s.get("end", 0)), "source": str(s.get("text", "")), "translated": str(s.get("text", ""))} for s in segments]
    def logout(self):
        return None
    def __getattr__(self, name):
        def no_op(*args, **kwargs):
            return [] if name == "list_members" else {}
        return no_op

class _NoAuthController:
    def __init__(self, root, client, on_unlocked, on_locked):
        self.root, self.client = root, client
        self.on_unlocked, self.on_locked = on_unlocked, on_locked
        self.user = {"phone": "本地模式", "role": "owner", "displayName": "本地用户", "canTranslate": True}
    def start(self):
        self.on_unlocked(dict(self.user))
    def close(self):
        return None
    def lock(self, reason=""):
        return None
    def logout(self):
        return None
    def show_team(self):
        return None
    def show_profile(self):
        return None

_module.DesktopAccountClient = _NoAuthClient
_module.AccountController = _NoAuthController

def _local_translate(self, segments, source, target, localize, emotion, detected_source=""):
    key = os.getenv("DEEPL_API_KEY", "").strip()
    if not key:
        try:
            key = (Path(APP_DIR) / "deepl_api_key.txt").read_text(encoding="utf-8-sig").strip()
        except OSError:
            key = ""
    if not key:
        raise RuntimeError("未配置 DeepL API Key：请将密钥放入软件目录中的 deepl_api_key.txt")
    source_code = (detected_source or getattr(self, "_detected_language", "") or "") if source == "auto" else (detected_source or source or "")
    source_code = source_code.split("-")[0].upper()
    if source_code == "ZH":
        for seg in segments: seg.translated = seg.text
        return segments
    endpoint = "https://api-free.deepl.com/v2/translate" if ":fx" in key else "https://api.deepl.com/v2/translate"
    for offset in range(0, len(segments), 50):
        batch = segments[offset:offset + 50]
        pairs = [("text", seg.text) for seg in batch] + [("target_lang", "ZH")]
        if source_code: pairs.append(("source_lang", source_code))
        last = None
        for attempt in range(3):
            try:
                req = urllib.request.Request(endpoint, data=urllib.parse.urlencode(pairs).encode("utf-8"), method="POST", headers={"Authorization": f"DeepL-Auth-Key {key}", "Content-Type": "application/x-www-form-urlencoded"})
                with urllib.request.urlopen(req, timeout=45) as response:
                    data = json.loads(response.read().decode("utf-8"))
                rows = data.get("translations", [])
                if len(rows) != len(batch): raise RuntimeError(f"DeepL 返回段数异常：{len(rows)}/{len(batch)}")
                for seg, row in zip(batch, rows): seg.translated = str(row.get("text", "")).strip()
                break
            except Exception as exc:
                last = exc
                if attempt < 2: time.sleep(1.5 * (attempt + 1))
        else:
            raise RuntimeError(f"DeepL 翻译失败：{last}")
    return segments

_module.TranslationBackend.translate = _local_translate

# The primary account is the only account allowed to use the local desktop
# engine without waiting for a team permission probe.  Members still use the
# server-side permission returned by the team API.
try:
    _account_get_access = DesktopAccountClient.get_access
    def _owner_get_access(self):
        result = _account_get_access(self)
        user = result.get("user", result) if isinstance(result, dict) else {}
        phone = "".join(ch for ch in str(user.get("phone", "")) if ch.isdigit())
        if phone == "<owner-phone>" and str(user.get("role", "")).lower() == "owner":
            result = dict(result)
            result["canTranslate"] = True
        return result
    DesktopAccountClient.get_access = _owner_get_access
except Exception:
    pass

def _fixed_poll(self):
    dirty = False
    try:
        while True:
            kind, payload = self.events.get_nowait()
            if kind == "batch":
                generation, kind, payload = payload
                if generation != self.account_generation:
                    continue
            if kind == "lock":
                self.account_controller.lock(payload)
                continue
            if kind in ("u", "o", "e") and payload[0] >= len(self.jobs):
                continue
            if kind == "u":
                self.jobs[payload[0]].status, self.jobs[payload[0]].progress = payload[1], payload[2]
                dirty = True
            elif kind == "o":
                self.jobs[payload[0]].output = payload[1]
                self.jobs[payload[0]].needs_attention = bool(payload[2]) if len(payload) > 2 else False
                if len(payload) > 3:
                    self.jobs[payload[0]].translation_text = payload[3]
                dirty = True
            elif kind == "e":
                self.jobs[payload[0]].status, self.jobs[payload[0]].error = "失败", payload[1]
                self.status_var.set(f"任务失败：{Path(self.jobs[payload[0]].video).name} · {payload[1]}")
                dirty = True
            elif kind == "a":
                self.status_var.set(f"已完成 {payload}/{len(self.jobs)} 个任务")
            elif kind == "done":
                self.start.configure(state="normal" if self.account_unlocked else "disabled")
                self.pause_btn.configure(text="暂停")
                self.pause_event.set()
                failed = sum(1 for j in self.jobs if j.status == "失败")
                success = sum(1 for j in self.jobs if j.status == "已完成")
                pending = max(0, len(self.jobs) - failed - success)
                if self.stop_event.is_set():
                    self.status_var.set("已停止：已完成的译文保留在右侧预览")
                elif failed or pending:
                    self.status_var.set(f"处理结束：成功 {success} 个，失败 {failed} 个，未完成 {pending} 个")
                else:
                    self.status_var.set(f"处理完成：{success} 个任务全部成功")
    except queue.Empty:
        pass
    if dirty:
        self.refresh()
    self.after(100, self._poll)


def _fixed_transcribe(self, video, source):
    """本地 Whisper 识别：音频生成器必须在信号量内完整消费，避免多任务把 CPU 挤满。"""
    key = self._audio_cache_key(video, source)
    with self._cache_lock:
        cached = self._transcript_cache.get(key)
    if cached:
        return [Segment(s.start, s.end, s.text, s.translated) for s in cached[0]], cached[1]
    work, audio = self._extract_audio(video)
    try:
        from faster_whisper import WhisperModel
        if self._whisper is None:
            with self._model_lock:
                if self._whisper is None:
                    logical = os.cpu_count() or 4
                    threads = max(1, min(8, logical - 2))
                    # 优先使用 NVIDIA CUDA；找不到运行库时自动回退 CPU，保证软件仍可用。
                    for dll_dir in (Path(sys.executable).parent / "_internal" / "ctranslate2", Path(sys.executable).parent / "_internal", Path(sys.executable).parent):
                        try:
                            if dll_dir.exists() and hasattr(os, "add_dll_directory"):
                                os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")
                                os.add_dll_directory(str(dll_dir))
                        except Exception:
                            pass
                    try:
                        self._whisper = WhisperModel(self._model_name, device="cuda", compute_type="int8_float16", num_workers=1, local_files_only=True)
                        self._asr_device = "cuda"
                    except Exception:
                        self._whisper = WhisperModel(self._model_name, device="cpu", compute_type="int8", cpu_threads=threads, num_workers=1, local_files_only=True)
                        self._asr_device = "cpu"
        language = None if source == "auto" else source.split("-")[0]
        with self._asr_slots:
            chunks, info = self._whisper.transcribe(audio, language=language, vad_filter=True, beam_size=5, condition_on_previous_text=True)
            result = [Segment(float(c.start), float(c.end), c.text.strip()) for c in chunks if c.text and c.text.strip()]
        detected = getattr(info, "language", "") or language or ""
        if not result:
            raise RuntimeError("未识别到有效口播内容，请确认视频包含清晰人声")
        with self._cache_lock:
            self._transcript_cache[key] = (result, detected)
        self._detected_language = detected
        return result, detected
    except Exception as exc:
        raise RuntimeError(f"本地语音识别失败：{exc}") from exc
    finally:
        shutil.rmtree(work, ignore_errors=True)

TranslationBackend.transcribe_with_language = _fixed_transcribe
TranslatorApp._poll = _fixed_poll

# Five active local ASR slots: enough throughput for batch work while keeping
# CPU and memory stable on the target Windows workstation.
_app_init = TranslatorApp.__init__
def _fixed_app_init(self, *args, **kwargs):
    _app_init(self, *args, **kwargs)
    try:
        self.backend._asr_slots = threading.Semaphore(5)
    except Exception:
        pass
TranslatorApp.__init__ = _fixed_app_init

_app_ui = TranslatorApp._ui
def _standalone_ui(self, *args, **kwargs):
    _app_ui(self, *args, **kwargs)
TranslatorApp._ui = _standalone_ui
try:
    TranslatorApp().mainloop()
except Exception:
    (Path(sys.executable).parent / "bootstrap_error.txt").write_text(traceback.format_exc(), encoding="utf-8")
    raise

