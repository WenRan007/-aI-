"""Offline delivery verification, invoked only with --self-check <fixture-folder>."""
import json
import os
from pathlib import Path
import random
import time
import traceback


def run(module, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    report = {"version": "2.0.3", "passed": False}
    app = None
    try:
        os.environ["HF_HUB_OFFLINE"] = "1"
        app = module.TranslatorApp()
        app.withdraw()
        app.select_mode("视频批量去重")
        panel = app.dedup_page
        panel.preview.load(str(folder / "preview_fixture.mp4"))
        deadline = time.monotonic() + 10
        while panel.preview.last_image is None and time.monotonic() < deadline:
            app.update()
            time.sleep(.02)
        assert panel.preview.last_image is not None, panel.preview.message
        report["embedded_preview"] = True
        panel.open_options()
        assert panel.options_panel.winfo_manager() == "pack"
        assert panel.drawer.active
        assert app.grab_current() is panel.drawer
        panel.opts["effect"].set(False)
        assert panel.effects_controls.winfo_manager() == ""
        panel.opts["effect"].set(True)
        gallery = panel.effect_picker()
        assert len(gallery.animations) == 15
        assert all(len(frames) > 1 for _, frames in gallery.animations)
        gallery.destroy()
        assert app.grab_current() is panel.drawer
        panel.close_options()
        assert app.grab_current() is None
        report["modal_drawer_and_gif_gallery"] = True
        app.select_mode("批量翻译短视频")
        app.select_mode("视频批量去重")
        assert app.dedup_page is panel
        report["tab_navigation"] = True
        panel.sticker_dir = folder / "stickers"
        settings = {key: True for key in panel.opts}
        settings.update(opacity=25, effects=["透明边框"])
        random.seed(17)
        out = folder / "packaged_export.mp4"
        panel._process_video(str(folder / "preview_fixture.mp4"), out, 1, 1, settings)
        import av
        with av.open(str(out)) as video:
            stream = video.streams.video[0]
            assert (stream.width, stream.height) == (720, 1280)
            next(video.decode(stream))
        report["video_export"] = True
        if app.backend.engine == "ollama":
            app.backend._ollama.unload()
        segments, language = app.backend.transcribe_with_language(str(folder / "speech.wav"), "en")
        assert segments and all(s.text.strip() for s in segments)
        report["offline_whisper"] = {"language": language, "text": " ".join(s.text for s in segments)}
        report["passed"] = True
    except Exception:
        report["error"] = traceback.format_exc()
    finally:
        if app is not None:
            app.destroy()
        (folder / "package_verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["passed"] else 1
