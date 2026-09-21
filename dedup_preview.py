"""Embedded video preview for the independent video processing page."""
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from PIL import Image, ImageTk


class DedupPreview(tk.Frame):
    def __init__(self, master):
        super().__init__(master, bg="#171a22")
        self.frames = queue.Queue(maxsize=3)
        self.stop_event = threading.Event()
        self.play_event = threading.Event()
        self.token = 0
        self.path = ""
        self.duration = 0.0
        self.position = 0.0
        self.seek_to = None
        self.seek_lock = threading.Lock()
        self.dragging = False
        self.photo = None
        self.canvas = tk.Canvas(self, bg="#0b0d13", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        controls = tk.Frame(self, bg="#171a22")
        controls.pack(fill="x", pady=(8, 0))
        self.play_button = ttk.Button(controls, text="▶ 播放", command=self.toggle, state="disabled", style="Dedup.TButton")
        self.play_button.pack(side="left")
        self.clock = tk.Label(controls, text="00:00 / 00:00", bg="#171a22", fg="#9aa7c2")
        self.clock.pack(side="right")
        self.progress = tk.Scale(self, from_=0, to=1, resolution=0.1, orient="horizontal", showvalue=False,
                                 bg="#171a22", troughcolor="#2a3040", highlightthickness=0, bd=0)
        self.progress.pack(fill="x")
        self.progress.bind("<ButtonPress-1>", lambda _: setattr(self, "dragging", True))
        self.progress.bind("<ButtonRelease-1>", self._seek)
        self.canvas.bind("<Configure>", lambda _: self._redraw())
        self.last_image = None
        self.message = "添加视频后显示预览"
        self._timer = self.after(25, self._pump)

    @staticmethod
    def _clock(seconds):
        minutes, seconds = divmod(int(max(0, seconds)), 60)
        return f"{minutes:02d}:{seconds:02d}"

    def load(self, path):
        self.stop_event.set()
        self.stop_event = threading.Event()
        self.play_event.clear()
        self.token += 1
        self.path = str(path)
        with self.seek_lock:
            self.seek_to = None
        self._drain_frames()
        self.duration = self.position = 0.0
        self.clock.configure(text="00:00 / 00:00")
        self.progress.configure(to=1)
        self.progress.set(0)
        self.last_image = None
        self.message = "正在加载视频…"
        self.play_button.configure(state="disabled", text="▶ 播放")
        self._redraw()
        threading.Thread(target=self._decode, args=(self.path, self.token, self.stop_event), daemon=True).start()

    def clear(self):
        self.stop_event.set()
        self.play_event.clear()
        self.token += 1
        self._drain_frames()
        self.path = ""
        self.last_image = None
        self.message = "添加视频后显示预览"
        self.position = self.duration = 0.0
        self.clock.configure(text="00:00 / 00:00")
        self.progress.set(0)
        self.play_button.configure(state="disabled", text="▶ 播放")
        self._redraw()

    def pause(self):
        self.play_event.clear()
        self.play_button.configure(text="▶ 播放")

    def toggle(self):
        if not self.path:
            return
        if self.play_event.is_set():
            self.pause()
        else:
            if self.duration and self.position >= self.duration - 0.15:
                self.request_seek(0.0)
            self.play_event.set()
            self.play_button.configure(text="Ⅱ 暂停")

    def _seek(self, _event=None):
        self.dragging = False
        if self.path:
            self.request_seek(min(float(self.progress.get()), max(0, self.duration - 0.1)))

    def request_seek(self, seconds):
        with self.seek_lock:
            self.seek_to = seconds

    def _drain_frames(self):
        try:
            while True:
                self.frames.get_nowait()
        except queue.Empty:
            pass

    def _put(self, item):
        if item[0] != self.token:
            return
        try:
            self.frames.put_nowait(item)
        except queue.Full:
            try:
                self.frames.get_nowait()
            except queue.Empty:
                pass
            try:
                self.frames.put_nowait(item)
            except queue.Full:
                pass

    def _decode(self, path, token, stop):
        try:
            import av
            with av.open(path) as container:
                stream = container.streams.video[0]
                duration = float(stream.duration * stream.time_base) if stream.duration else float(container.duration or 0) / av.time_base
                rate = float(stream.average_rate or 25)
                iterator = container.decode(stream)
                first = True
                origin = None
                desired = 0.0
                paused_at = None
                while not stop.is_set():
                    with self.seek_lock:
                        request = self.seek_to if token == self.token else None
                        if request is not None:
                            self.seek_to = None
                    if request is not None:
                        desired = request
                        container.seek(int(desired * av.time_base), backward=True)
                        iterator = container.decode(stream)
                        first, origin = True, None
                    if not first and not self.play_event.is_set():
                        if paused_at is None:
                            paused_at = time.perf_counter()
                        stop.wait(0.02)
                        continue
                    if paused_at is not None:
                        if origin is not None:
                            origin += time.perf_counter() - paused_at
                        paused_at = None
                    try:
                        frame = next(iterator)
                    except StopIteration:
                        self._put((token, "end", duration))
                        while not stop.is_set() and self.seek_to is None:
                            stop.wait(0.03)
                        continue
                    position = float(frame.time) if frame.time is not None else desired
                    if position + 1 / max(rate, 1) < desired:
                        continue
                    if origin is None:
                        origin = time.perf_counter() - position
                    deadline = origin + position
                    interrupted = False
                    while not first and time.perf_counter() < deadline:
                        if stop.is_set() or self.seek_to is not None or not self.play_event.is_set():
                            interrupted = True
                            break
                        stop.wait(min(0.01, deadline - time.perf_counter()))
                    if interrupted:
                        # Seek/pause should take effect promptly; the next frame keeps the source clock.
                        continue
                    if not first and time.perf_counter() - deadline > 0.1:
                        continue
                    image = frame.to_image()
                    w, h = image.size
                    crop_w, crop_h = min(w, h * 9 / 16), min(h, w * 16 / 9)
                    image = image.crop(((w - crop_w) / 2, (h - crop_h) / 2, (w + crop_w) / 2, (h + crop_h) / 2))
                    image.thumbnail((432, 768), Image.Resampling.BILINEAR)
                    self._put((token, "frame", image, position, duration))
                    first = False
        except Exception as exc:
            self._put((token, "error", f"无法预览：{exc}"))

    def _pump(self):
        try:
            while True:
                item = self.frames.get_nowait()
                if item[0] != self.token:
                    continue
                if item[1] == "frame":
                    self.last_image, self.position, self.duration = item[2:]
                    self.play_button.configure(state="normal")
                    self.message = ""
                    self.progress.configure(to=max(1, self.duration))
                    if not self.dragging:
                        self.progress.set(self.position)
                    self.clock.configure(text=f"{self._clock(self.position)} / {self._clock(self.duration)}")
                    self._redraw()
                elif item[1] == "end":
                    self.position = item[2]
                    self.pause()
                    self.progress.set(self.position)
                    self.clock.configure(text=f"{self._clock(self.position)} / {self._clock(self.duration)}")
                elif item[1] == "error":
                    self.message = item[2]
                    self.pause()
                    self.last_image = None
                    self.play_button.configure(state="disabled")
                    self._redraw()
        except queue.Empty:
            pass
        self._timer = self.after(25, self._pump)

    def _redraw(self):
        width, height = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        self.canvas.delete("all")
        if self.last_image is not None:
            image = self.last_image.copy()
            image.thumbnail((width, height), Image.Resampling.BILINEAR)
            self.photo = ImageTk.PhotoImage(image)
            self.canvas.create_image(width / 2, height / 2, image=self.photo)
        elif self.message:
            self.canvas.create_text(width / 2, height / 2, text=self.message, fill="#9aa7c2", width=max(100, width - 24))

    def destroy(self):
        self.stop_event.set()
        if self._timer:
            self.after_cancel(self._timer)
        super().destroy()
