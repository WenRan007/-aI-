"""Animated GIF gallery for selecting video effects."""
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk, ImageSequence


class EffectPicker(tk.Toplevel):
    def __init__(self, parent, effect_vars):
        super().__init__(parent)
        self.title("选择视频特效")
        self.configure(bg="#171a22")
        self.geometry("720x650")
        self.minsize(640, 460)
        self.effect_vars = effect_vars
        self.pending = {name: tk.BooleanVar(self, value=value.get()) for name, value in effect_vars.items()}
        self.animations = []
        self.frame_number = 0
        self._timer = None
        top = tk.Frame(self, bg="#171a22", padx=16, pady=12); top.pack(fill="x")
        tk.Label(top, text="选择视频特效", bg="#171a22", fg="white", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        tk.Label(top, text="GIF 循环展示特效样式；示例强度 100%，导出按设置的强度应用", bg="#171a22", fg="#929db5").pack(anchor="w", pady=(6, 0))
        toolbar = tk.Frame(self, bg="#171a22", padx=16); toolbar.pack(fill="x", pady=(0, 8))
        ttk.Button(toolbar, text="全选", style="Dedup.TButton", command=lambda: self.select_all(True)).pack(side="left")
        ttk.Button(toolbar, text="取消全选", style="Dedup.TButton", command=lambda: self.select_all(False)).pack(side="left", padx=6)
        self.selection_text = tk.StringVar()
        tk.Label(toolbar, textvariable=self.selection_text, bg="#171a22", fg="#a99cff").pack(side="right")
        bottom = tk.Frame(self, bg="#171a22", padx=16, pady=12); bottom.pack(side="bottom", fill="x")
        self.confirm = ttk.Button(bottom, text="确定", style="Dedup.Accent.TButton", command=self.apply)
        self.confirm.pack(side="right")
        ttk.Button(bottom, text="取消", style="Dedup.TButton", command=self.destroy).pack(side="right", padx=8)
        scroll = tk.Frame(self, bg="#171a22"); scroll.pack(fill="both", expand=True, padx=12)
        self.canvas = tk.Canvas(scroll, bg="#171a22", highlightthickness=0)
        bar = ttk.Scrollbar(scroll, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y"); self.canvas.pack(side="left", fill="both", expand=True)
        cards = tk.Frame(self.canvas, bg="#171a22")
        window_id = self.canvas.create_window((0, 0), window=cards, anchor="nw")
        cards.bind("<Configure>", lambda _: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(window_id, width=event.width))
        self.bind("<MouseWheel>", lambda event: self.canvas.yview_scroll(-int(event.delta / 120), "units"))
        assets = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets" / "effects"
        for index, (name, variable) in enumerate(self.pending.items()):
            card = tk.Frame(cards, bg="#242936", padx=8, pady=8)
            card.grid(row=index // 3, column=index % 3, padx=5, pady=5, sticky="nsew")
            preview = tk.Label(card, bg="#111620", width=160, height=200, cursor="hand2")
            preview.pack()
            try:
                with Image.open(assets / f"effect_{index:02d}.gif") as gif:
                    frames = [ImageTk.PhotoImage(frame.convert("RGB"), master=self) for frame in ImageSequence.Iterator(gif)]
                preview.configure(image=frames[0])
                self.animations.append((preview, frames))
            except (OSError, ValueError):
                preview.configure(text="预览暂不可用", fg="#9aa7c2", width=20, height=10)
            preview.bind("<Button-1>", lambda _, v=variable: v.set(not v.get()))
            ttk.Checkbutton(card, text=name, variable=variable, style="Gallery.TCheckbutton").pack(anchor="w", pady=(7, 0))
            variable.trace_add("write", lambda *_: self.update_count())
        for column in range(3): cards.grid_columnconfigure(column, weight=1)
        ttk.Style(self).configure("Gallery.TCheckbutton", background="#242936", foreground="#e7ebf5")
        self.bind("<Escape>", lambda _: self.destroy())
        self.update_count()
        self.animate()

    def select_all(self, enabled):
        for variable in self.pending.values():
            variable.set(enabled)

    def update_count(self):
        count = sum(variable.get() for variable in self.pending.values())
        self.selection_text.set(f"已选 {count} / {len(self.pending)}")
        self.confirm.configure(state="normal" if count else "disabled")

    def apply(self):
        if not any(variable.get() for variable in self.pending.values()):
            return
        for name, variable in self.pending.items():
            self.effect_vars[name].set(variable.get())
        self.destroy()

    def animate(self):
        for label, frames in self.animations:
            label.configure(image=frames[self.frame_number % len(frames)])
        self.frame_number += 1
        self._timer = self.after(125, self.animate)

    def destroy(self):
        if self._timer is not None:
            self.after_cancel(self._timer)
            self._timer = None
        super().destroy()
