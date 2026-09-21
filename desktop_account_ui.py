# -*- coding: utf-8 -*-
"""常客 AI Windows 客户端的登录门禁和团队管理界面。

This module deliberately keeps all Tk work on the main thread.  The client
object is a small synchronous HTTP adapter (``desktop_account_api``); network
calls are dispatched to worker threads and results are marshalled back with
``after``.
"""
from __future__ import annotations

import queue
import threading
import sys
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, ttk
from PIL import Image, ImageTk
from typing import Any, Callable, Optional

try:
    from desktop_account import AccountError, AuthRequired, AccessDenied
except ImportError:  # allows the UI to be imported while the adapter is built
    class AccountError(Exception):
        pass
    class AuthRequired(AccountError):
        pass
    class AccessDenied(AccountError):
        pass


class AccountController:
    """Owns the login gate and account dialogs for the desktop application."""

    def __init__(self, root: tk.Misc, client: Any,
                 on_unlocked: Callable[[dict], None],
                 on_locked: Callable[[str], None]):
        self.root, self.client = root, client
        self.on_unlocked, self.on_locked = on_unlocked, on_locked
        self._events: queue.Queue = queue.Queue()
        self._generation = 0
        self._busy = False
        self._sms_cooldown = 0
        self._closed = False
        self.user: dict = {}
        self._gate: Optional[tk.Frame] = None
        self._poll_id = None
        self._cooldown_id = None
        self._team_window = None
        self._profile_window = None
        self._configure_style()
        self.root.after(60, self._drain_events)

    def _configure_style(self):
        style = ttk.Style(self.root)
        try: style.theme_use("clam")
        except tk.TclError: pass
        style.configure("Account.TFrame", background="#0d111b")
        style.configure("Account.Card.TFrame", background="#161c2a")
        style.configure("Account.TLabel", background="#0d111b", foreground="#d9e1f2", font=("Microsoft YaHei UI", 10))
        style.configure("Account.Muted.TLabel", background="#0d111b", foreground="#8895af", font=("Microsoft YaHei UI", 9))
        style.configure("Account.Title.TLabel", background="#0d111b", foreground="#ffffff", font=("Microsoft YaHei UI", 20, "bold"))
        style.configure("Account.Accent.TLabel", background="#161c2a", foreground="#a98bff", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Account.TButton", background="#242d42", foreground="#eef2ff", borderwidth=0, padding=(13, 8))
        style.map("Account.TButton", background=[("active", "#334263")])
        style.configure("Account.Primary.TButton", background="#7657ed", foreground="#ffffff", borderwidth=0, padding=(15, 9))
        style.map("Account.Primary.TButton", background=[("active", "#896eff"), ("disabled", "#34305f")], foreground=[("disabled", "#77738f")])

    def start(self):
        self._show_login()

    def _clear(self):
        if self._cooldown_id:
            self.root.after_cancel(self._cooldown_id)
            self._cooldown_id = None
        self._sms_cooldown = 0
        if self._gate is not None:
            self._gate.destroy()
            self._gate = None

    def _show_login(self, error: str = ""):
        self._clear()
        self._generation += 1
        gate = self._gate = tk.Frame(self.root, bg="#0d111b")
        gate.place(relx=0, rely=0, relwidth=1, relheight=1)
        brand = tk.Frame(gate, bg="#101c35")
        brand.place(relx=0, rely=0, relwidth=.44, relheight=1)
        brand_inner = tk.Frame(brand, bg="#101c35")
        brand_inner.place(relx=.1, rely=.18, relwidth=.82)
        logo_path = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "assets" / "ck-logo.png"
        if logo_path.exists():
            logo = Image.open(logo_path).convert("RGBA")
            logo.thumbnail((96, 96), Image.Resampling.LANCZOS)
            self._logo_image = ImageTk.PhotoImage(logo)
            tk.Label(brand_inner, image=self._logo_image, bg="#101c35").pack(anchor="w", pady=(0, 20))
        tk.Label(brand_inner, text="常客AI工具", bg="#101c35", fg="#ffffff", font=("Microsoft YaHei UI", 28, "bold")).pack(anchor="w")
        tk.Label(brand_inner, text="CHANGKE E-COMMERCE AI", bg="#101c35", fg="#d7b76a", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(8, 42))
        tk.Label(brand_inner, text="以诚信赢信任\n以品质留常客", justify="left", bg="#101c35", fg="#f4e7c9", font=("Microsoft YaHei UI", 24, "bold")).pack(anchor="w")
        tk.Label(brand_inner, text="电商内容智能处理工作台", bg="#101c35", fg="#a4b6d6", font=("Microsoft YaHei UI", 11)).pack(anchor="w", pady=(20, 0))
        tk.Label(brand, text="design by YYH", bg="#101c35", fg="#8fa4c8", font=("Segoe UI", 10, "italic")).place(relx=.1, rely=.9)
        card = tk.Frame(gate, bg="#161c2a")
        card.place(relx=.44, rely=0, relwidth=.56, relheight=1)
        inner = tk.Frame(card, bg="#161c2a"); inner.place(relx=.11, rely=.2, relwidth=.78)
        tk.Label(inner, text="登录常客 AI", bg="#161c2a", fg="#ffffff", font=("Microsoft YaHei UI", 24, "bold")).pack(anchor="w")
        tk.Label(inner, text="主账号或已授权成员登录后才能使用翻译功能", bg="#161c2a", fg="#96a3bc", font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(6, 19))
        tk.Label(inner, text="手机号", bg="#161c2a", fg="#aeb9ce", font=("Microsoft YaHei UI", 9)).pack(anchor="w")
        phone = tk.StringVar(); code = tk.StringVar(); mode = tk.StringVar(value="login")
        phone_entry = tk.Entry(inner, textvariable=phone, bg="#0d111b", fg="#f5f7ff", insertbackground="white", relief="flat", font=("Segoe UI", 11))
        phone_entry.pack(fill="x", ipady=10, pady=(5, 13))
        row = tk.Frame(inner, bg="#161c2a"); row.pack(fill="x")
        code_entry = tk.Entry(row, textvariable=code, bg="#0d111b", fg="#f5f7ff", insertbackground="white", relief="flat", font=("Segoe UI", 11))
        code_entry.pack(side="left", fill="x", expand=True, ipady=10)
        send_btn = ttk.Button(row, text="获取验证码", style="Account.TButton", command=lambda: self._send_code(phone, mode, send_btn))
        send_btn.pack(side="left", padx=(8, 0), ipady=2)
        choice = tk.Frame(inner, bg="#161c2a"); choice.pack(anchor="w", pady=(12, 0))
        tk.Radiobutton(choice, text="登录", variable=mode, value="login", bg="#161c2a", fg="#d9e1f2", selectcolor="#161c2a", activebackground="#161c2a", activeforeground="#ffffff").pack(side="left")
        tk.Radiobutton(choice, text="注册加入团队", variable=mode, value="register", bg="#161c2a", fg="#d9e1f2", selectcolor="#161c2a", activebackground="#161c2a", activeforeground="#ffffff").pack(side="left", padx=13)
        if error: tk.Label(inner, text=error, bg="#161c2a", fg="#ff7d93", font=("Microsoft YaHei UI", 9), wraplength=340, justify="left").pack(anchor="w", pady=(10, 0))
        login_btn = ttk.Button(inner, text="登录常客AI", style="Account.Primary.TButton", command=lambda: self._verify(phone, code, mode, login_btn))
        login_btn.pack(fill="x", pady=(16, 12), ipady=3)
        tk.Label(inner, text="注册验证码会发送到主账号手机，请向主账号获取验证码。", bg="#161c2a", fg="#71809d", font=("Microsoft YaHei UI", 8), wraplength=360, justify="left").pack(anchor="w")
        phone_entry.focus_set()

    def _worker(self, fn: Callable, done: Callable, generation: Optional[int] = None):
        if self._busy: return False
        self._busy = True
        gen = self._generation if generation is None else generation
        def run():
            try: value = (True, fn())
            except Exception as exc: value = (False, exc)
            self._events.put((gen, done, value))
        threading.Thread(target=run, daemon=True).start(); return True

    def _drain_events(self):
        if self._closed: return
        try:
            while True:
                gen, callback, value = self._events.get_nowait()
                self._busy = False
                if gen == self._generation: callback(*value)
        except queue.Empty: pass
        self.root.after(60, self._drain_events)

    def _send_code(self, phone_var, mode_var, button):
        phone = phone_var.get().strip()
        if self._sms_cooldown or self._busy: return
        button.state(["disabled"])
        mode = mode_var.get()
        self._worker(lambda: self.client.send_code(phone, mode=mode), lambda ok, result: self._code_done(ok, result, button))

    def _code_done(self, ok, result, button):
        button.state(["!disabled"])
        if not ok:
            messagebox.showerror("验证码", self._error_text(result), parent=self.root); return
        self._sms_cooldown = 60; button.state(["disabled"]); self._tick_cooldown(button)
        messagebox.showinfo("验证码", "验证码已发送，请查收短信。注册时请向主账号获取验证码。", parent=self.root)

    def _tick_cooldown(self, button):
        if self._closed: return
        if self._sms_cooldown <= 0:
            button.configure(text="获取验证码"); button.state(["!disabled"]); return
        button.configure(text=f"{self._sms_cooldown}s 后重试"); self._sms_cooldown -= 1
        self._cooldown_id = self.root.after(1000, lambda: self._tick_cooldown(button))

    def _verify(self, phone_var, code_var, mode_var, button):
        if self._busy: return
        phone, code, mode = phone_var.get().strip(), code_var.get().strip(), mode_var.get()
        if not phone or not code:
            messagebox.showwarning("登录", "请输入手机号和验证码。", parent=self.root); return
        button.state(["disabled"])
        self._worker(lambda: self.client.verify_code(phone, code, mode=mode), lambda ok, result: self._verify_done(ok, result, button))

    def _verify_done(self, ok, result, button):
        if not ok:
            button.state(["!disabled"]); messagebox.showerror("登录失败", self._error_text(result), parent=self.root); return
        self._refresh_access(initial=True)

    def _refresh_access(self, initial=False):
        if not self._worker(lambda: self.client.get_access(), lambda ok, result: self._access_done(ok, result, initial)):
            self._schedule_access_check()

    def _access_done(self, ok, result, initial=False):
        if ok:
            user = result.get("user", result) if isinstance(result, dict) else {}
            allowed = bool(result.get("canTranslate", user.get("role") == "owner")) if isinstance(result, dict) else False
            if allowed:
                self.user = user; self._clear(); self.on_unlocked(dict(user)); self._schedule_access_check(); return
            self.user = user; self.on_locked("请联系主账号开通视频翻译权限"); getattr(self, "_close_dialogs", lambda: None)(); self._show_waiting(); self._schedule_access_check(); return
        self.on_locked(self._error_text(result))
        getattr(self, "_close_dialogs", lambda: None)()
        if initial:
            self.user = {}
            self._show_login(self._error_text(result))
        else:
            self._show_waiting(self._error_text(result))
            self._schedule_access_check()

    def _schedule_access_check(self):
        if self._closed: return
        if self._poll_id: self.root.after_cancel(self._poll_id)
        self._poll_id = self.root.after(30000, lambda: self._refresh_access(False))

    def _show_waiting(self, message="账号已注册，主账号开启“视频翻译”权限后即可使用。\n页面会自动检查权限。"):
        self._clear(); frame = self._gate = tk.Frame(self.root, bg="#0d111b"); frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        box = tk.Frame(frame, bg="#161c2a", highlightbackground="#323d58", highlightthickness=1); box.place(relx=.5, rely=.5, anchor="center", relwidth=.42, relheight=.32)
        tk.Label(box, text="等待验证使用权限", bg="#161c2a", fg="#ffffff", font=("Microsoft YaHei UI", 18, "bold")).pack(pady=(35, 10))
        tk.Label(box, text=message, wraplength=380, bg="#161c2a", fg="#9aa7c0", font=("Microsoft YaHei UI", 10), justify="center").pack()
        ttk.Button(box, text="退出登录", style="Account.TButton", command=self.logout).pack(pady=22)

    def _error_text(self, exc):
        if isinstance(exc, AccessDenied): return str(exc) or "该账号已被主账号暂停访问。"
        if isinstance(exc, AuthRequired): return "登录已失效，请重新登录。"
        return str(exc) or "网络请求失败，请稍后重试。"

    def show_profile(self):
        if self._profile_window and self._profile_window.winfo_exists(): self._profile_window.lift(); return
        win = self._profile_window = tk.Toplevel(self.root); win.title("个人资料"); win.configure(bg="#161c2a"); win.resizable(False, False)
        tk.Label(win, text="个人资料", bg="#161c2a", fg="white", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w", padx=25, pady=(20, 12))
        tk.Label(win, text=f"手机号：{self.user.get('phone', '')}", bg="#161c2a", fg="#9aa7c0").pack(anchor="w", padx=25)
        name = tk.StringVar(value=self.user.get("displayName", self.user.get("display_name", ""))); tk.Label(win, text="用户名", bg="#161c2a", fg="#b9c2d5").pack(anchor="w", padx=25, pady=(15, 3))
        ent = tk.Entry(win, textvariable=name, bg="#0d111b", fg="white", insertbackground="white", relief="flat"); ent.pack(fill="x", padx=25, ipady=8)
        def save():
            value = name.get().strip()
            if not value: return
            self._worker(lambda: self.client.update_profile(value), lambda ok, result: self._profile_saved(ok, result, value, win))
        ttk.Button(win, text="保存", style="Account.Primary.TButton", command=save).pack(fill="x", padx=25, pady=20)

    def _profile_saved(self, ok, result, name, win):
        if not ok: messagebox.showerror("保存失败", self._error_text(result), parent=win); return
        self.user["displayName"] = name; win.destroy(); self.on_unlocked(dict(self.user))

    def show_team(self):
        if self.user.get("role") != "owner": return
        if self._team_window and self._team_window.winfo_exists(): self._team_window.lift(); return
        win = self._team_window = tk.Toplevel(self.root); win.title("团队管理"); win.geometry("720x430"); win.configure(bg="#0d111b")
        tk.Label(win, text="团队管理", bg="#0d111b", fg="white", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w", padx=20, pady=(18, 3))
        tk.Label(win, text="管理成员状态和视频翻译权限", bg="#0d111b", fg="#8f9bb2").pack(anchor="w", padx=20, pady=(0, 12))
        cols=("phone","name","state","perm"); tree=ttk.Treeview(win, columns=cols, show="headings", height=11); tree.pack(fill="both", expand=True, padx=20)
        for c,t,w in zip(cols,("手机号","用户名","状态","视频翻译"),(190,160,100,120)): tree.heading(c,text=t); tree.column(c,width=w,anchor="center")
        bar=tk.Frame(win,bg="#0d111b"); bar.pack(fill="x", padx=20, pady=13)
        def load():
            self._worker(lambda: self.client.list_members(), lambda ok, result: fill(ok,result))
        def fill(ok,result):
            if not ok: messagebox.showerror("团队管理", self._error_text(result), parent=win); return
            tree.delete(*tree.get_children()); members=result.get("members", []) if isinstance(result, dict) else (result if isinstance(result, list) else [])
            for m in members:
                # The owner is implicitly allowed to translate on the server;
                # reflect that in the team table instead of showing a
                # misleading “未授权” state for the primary account.
                permissions = m.get("permissions", [])
                is_owner = str(m.get("role", "")).lower() == "owner"
                tree.insert("", "end", iid=str(m.get("id")), values=(m.get("phone",""),m.get("display_name",m.get("displayName","")),"已禁用" if m.get("disabled") else "正常","已授权" if is_owner or "视频翻译" in permissions else "未授权"))
        def action(kind):
            sel=tree.selection()
            if not sel: return
            iid=sel[0]; m=tree.item(iid,"values");
            if kind=="remove" and not messagebox.askyesno("移除成员", f"确定移除账号 {m[0]}？", parent=win): return
            payload = {"action": kind}
            if kind in ("permissions", "revoke"):
                payload = {"action": "permissions", "permissions": ["视频翻译"] if kind == "permissions" else []}
            self._worker(lambda: self.client.update_member(iid, payload), lambda ok,result: (load() if ok else messagebox.showerror("团队管理",self._error_text(result),parent=win)))
        for text,kind in (("启用","enable"),("禁用","disable"),("授权翻译","permissions"),("取消授权","revoke"),("移除成员","remove")): ttk.Button(bar,text=text,style="Account.TButton",command=lambda k=kind: action(k)).pack(side="left", padx=(0,8))
        load()

    def logout(self):
        self._generation += 1; self.user = {}
        self.client.logout()
        self._close_dialogs()
        if self._poll_id:
            self.root.after_cancel(self._poll_id); self._poll_id = None
        self.on_locked("已退出登录"); self._show_login()

    def _close_dialogs(self):
        for window in (self._team_window, self._profile_window):
            if window and window.winfo_exists(): window.destroy()
        self._team_window = self._profile_window = None

    def lock(self, reason):
        self.on_locked(str(reason))
        self._close_dialogs()
        self._show_waiting(str(reason))
        self._schedule_access_check()

    def close(self):
        self._closed = True
        if self._poll_id: self.root.after_cancel(self._poll_id)
        if self._cooldown_id: self.root.after_cancel(self._cooldown_id)
