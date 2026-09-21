import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import translator_desktop as app_module


class DedupWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = app_module.TranslatorApp()
        cls.app.withdraw()
        cls.app.select_mode("视频批量去重")
        cls.panel = cls.app.dedup_page

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()

    def test_navigation_keeps_independent_queues_and_settings(self):
        original_jobs = list(self.app.jobs)
        self.app.jobs.append(app_module.Job("translation.mp4", "vi", "zh-CN", "批量翻译短视频"))
        self.panel.videos[:] = ["dedup.mp4"]
        self.app.source_var.set("vi")
        self.app.select_mode("批量翻译短视频")
        self.app.select_mode("视频批量去重")
        self.assertIs(self.panel, self.app.dedup_page)
        self.assertEqual(self.panel.videos, ["dedup.mp4"])
        self.assertEqual(self.app.jobs[-1].video, "translation.mp4")
        self.assertEqual(self.app.source_var.get(), "vi")
        self.assertFalse(any(isinstance(w, app_module.tk.Toplevel) for w in self.app.winfo_children()))
        self.app.jobs[:] = original_jobs

    def test_drawer_and_required_name(self):
        self.panel.batch_name.set("")
        self.panel.open_options()
        self.assertEqual(self.panel.options_panel.winfo_manager(), "pack")
        self.assertTrue(self.panel.drawer.active)
        self.assertIs(self.app.grab_current(), self.panel.drawer)
        self.assertTrue(self.panel.confirm_btn.instate(["disabled"]))
        self.panel.batch_name.set("测试批次")
        self.assertFalse(self.panel.confirm_btn.instate(["disabled"]))
        self.panel.close_options()
        self.assertEqual(self.panel.preview_panel.winfo_manager(), "pack")
        self.assertEqual(self.panel.options_panel.winfo_manager(), "")
        self.assertFalse(self.panel.drawer.active)
        self.assertIsNone(self.app.grab_current())

    def test_defaults_and_effect_controls(self):
        self.assertTrue(all(var.get() for var in self.panel.opts.values()))
        self.assertTrue(all(var.get() for var in self.panel.effect_vars.values()))
        self.panel.opts["effect"].set(False)
        self.assertEqual(self.panel.effects_controls.winfo_manager(), "")
        self.panel.opts["effect"].set(True)
        self.assertEqual(self.panel.effects_controls.winfo_manager(), "grid")

    def test_drawer_blocks_navigation_and_restores_child_grab(self):
        self.app.select_mode("视频批量去重")
        self.panel.open_options()
        self.app.select_mode("批量翻译短视频")
        self.assertEqual(self.app.mode_var.get(), "视频批量去重")
        self.panel.sticker_library()
        window = next(w for w in self.panel.winfo_children() if isinstance(w, app_module.tk.Toplevel))
        self.assertIs(self.app.grab_current(), window)
        window.destroy()
        self.assertIs(self.app.grab_current(), self.panel.drawer)
        self.panel.close_options()
        self.app.select_mode("批量翻译短视频")
        self.assertEqual(self.app.mode_var.get(), "批量翻译短视频")
        self.app.select_mode("视频批量去重")

    def test_gif_gallery_cancel_apply_and_animation_cleanup(self):
        self.panel.open_options()
        gallery = self.panel.effect_picker()
        self.assertEqual(len(gallery.animations), 15)
        self.assertTrue(all(len(frames) > 1 for _, frames in gallery.animations))
        gallery.select_all(False)
        self.assertTrue(gallery.confirm.instate(["disabled"]))
        gallery.destroy()
        self.assertTrue(all(var.get() for var in self.panel.effect_vars.values()))
        self.assertIs(self.app.grab_current(), self.panel.drawer)
        self.panel.close_options()

    def test_batch_failure_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as folder:
            self.panel.events = queue.Queue()
            self.panel.cancel_event.clear()
            with patch.object(self.panel, "_process_video", side_effect=[None, RuntimeError("测试失败")]):
                self.panel._worker(Path(folder), "批次", 1, ("one.mp4", "two.mp4"), {})
            messages = []
            while not self.panel.events.empty():
                messages.append(self.panel.events.get_nowait())
            self.assertEqual(messages[-1][0], "finished")
            self.assertIn("成功 1 个，失败 1 个", messages[-1][1])
            self.assertIn("测试失败", messages[-1][1])

    def test_sticker_library_shows_thumbnail_and_deletes_single_image(self):
        original = self.panel.sticker_dir
        with tempfile.TemporaryDirectory() as folder:
            self.panel.sticker_dir = Path(folder)
            sticker = self.panel.sticker_dir / "测试贴纸.png"
            Image.new("RGBA", (64, 64), "red").save(sticker)
            try:
                self.panel.sticker_library()
                window = next(w for w in self.panel.winfo_children() if isinstance(w, app_module.tk.Toplevel))
                self.assertEqual(len(window._sticker_refs), 1)
                self.panel._delete_sticker(sticker, lambda: None)
                self.assertFalse(sticker.exists())
                window.destroy()
            finally:
                self.panel.sticker_dir = original


if __name__ == "__main__":
    unittest.main()
