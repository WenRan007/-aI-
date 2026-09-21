"""A same-window modal drawer with a dimmed, non-interactive background."""
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageEnhance, ImageGrab, ImageTk


class ModalDrawer(tk.Frame):
    def __init__(self, root, on_cancel, width=446):
        super().__init__(root, bg="#080a10")
        self.root = root
        self.on_cancel = on_cancel
        self.active = False
        self._destroyed = False
        self._snapshot = None
        self._photo = None
        self._previous_focus = None
        self._previous_grab = None
        self.mask = tk.Label(self, bg="#080a10", bd=0, takefocus=False)
        self.mask.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bg="#171a22", padx=16, pady=20, width=width)
        self.body.pack(side="right", fill="y")
        self.body.pack_propagate(False)
        self._bindings = []
        for sequence, callback in (("<Configure>", self._resize), ("<Tab>", self._tab),
                                   ("<Shift-Tab>", self._tab), ("<Escape>", self._escape),
                                   ("<FocusIn>", self._focus_in)):
            self._bindings.append((sequence, root.bind(sequence, callback, add="+")))

    def show(self):
        if self.active:
            return
        self.root.update_idletasks()
        self._previous_focus = self.root.focus_get()
        self._previous_grab = self.root.grab_current()
        width, height = max(1, self.root.winfo_width()), max(1, self.root.winfo_height())
        try:
            if not self.root.winfo_viewable():
                raise RuntimeError("Window is hidden")
            x, y = self.root.winfo_rootx(), self.root.winfo_rooty()
            screenshot = ImageGrab.grab(bbox=(x, y, x + width, y + height), all_screens=True)
            self._snapshot = ImageEnhance.Brightness(screenshot.convert("RGB")).enhance(.32)
        except Exception:
            self._snapshot = Image.new("RGB", (width, height), "#080a10")
        self.active = True
        self.place(x=0, y=0, relwidth=1, relheight=1)
        self.lift()
        self._paint_mask(width, height)
        self.root.update_idletasks()
        self.grab_set()
        self.focus_first()

    def hide(self):
        if not self.active:
            return
        self.active = False
        if self.grab_current() is self:
            self.grab_release()
        self.place_forget()
        if self._previous_grab is not None and self._previous_grab.winfo_exists():
            self._previous_grab.grab_set()
        if self._previous_focus is not None and self._previous_focus.winfo_exists():
            self._previous_focus.focus_set()
        self._snapshot = self._photo = None
        self.mask.configure(image="")

    def _paint_mask(self, width, height):
        if self._snapshot is None:
            return
        image = self._snapshot.resize((max(1, width), max(1, height)), Image.Resampling.BILINEAR)
        self._photo = ImageTk.PhotoImage(image, master=self)
        self.mask.configure(image=self._photo)

    def _resize(self, event):
        if self.active and event.widget is self.root:
            self._paint_mask(event.width, event.height)

    def _focusables(self):
        widgets = []
        def visit(parent):
            for child in parent.winfo_children():
                if isinstance(child, tk.Toplevel):
                    continue
                if child.winfo_viewable():
                    if isinstance(child, (ttk.Button, ttk.Checkbutton, tk.Entry, tk.Spinbox, tk.Scale)) or str(child.cget("takefocus") if "takefocus" in child.keys() else "") == "1":
                        if "state" not in child.keys() or str(child.cget("state")) != "disabled":
                            widgets.append(child)
                    visit(child)
        visit(self.body)
        return widgets

    def focus_first(self):
        candidates = self._focusables()
        (candidates[0] if candidates else self).focus_set()

    def _tab(self, event):
        if not self.active or event.widget.winfo_toplevel() is not self.root:
            return
        candidates = self._focusables()
        if candidates:
            current = self.root.focus_get()
            step = -1 if event.state & 1 else 1
            index = candidates.index(current) if current in candidates else (-1 if step == 1 else 0)
            candidates[(index + step) % len(candidates)].focus_set()
        return "break"

    def _escape(self, event):
        if self.active and event.widget.winfo_toplevel() is self.root:
            self.on_cancel()
            return "break"

    def _focus_in(self, event):
        if self.active and event.widget.winfo_toplevel() is self.root:
            if not str(event.widget).startswith(str(self) + ".") and event.widget is not self:
                self.focus_first()

    def attach_dialog(self, window):
        """Temporarily give a sticker/effects dialog the drawer's local grab."""
        window.transient(self.root)
        window.update_idletasks()
        window.grab_set()
        window.focus_set()
        def restore(event):
            if event.widget is window and self.active and not self._destroyed:
                self.grab_set()
                self.focus_first()
        window.bind("<Destroy>", restore, add="+")

    def destroy(self):
        if self._destroyed:
            return
        self._destroyed = True
        self.hide()
        for sequence, callback in self._bindings:
            self.root.unbind(sequence, callback)
        super().destroy()
