"""常客 AI 在线更新器。

更新器只依赖 Python 标准库，适合使用 PyInstaller 打成单文件 EXE。
远程 manifest 示例见 update-manifest.example.json。

设计目标：
* 已有完整运行目录时，下载增量或完整压缩包并覆盖更新；
* 电脑只有更新器时，下载完整包并完成首次安装；
* 更新前备份启动器、核心、配置和贴纸；失败时保留备份；
* 不覆盖用户的 DeepSeek/DeepL 密钥、模型缓存、贴纸和任务临时目录。
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import tkinter as tk
from tkinter import filedialog, messagebox


APP_ID = "changke-ai"
DEFAULT_MANIFEST_URL = (
    "https://raw.githubusercontent.com/WenRan007/-aI-/main/update-manifest.json"
)
LAUNCHER_NAMES = ("常客AI2.2.exe", "常客AI核心.exe")
PROCESS_NAMES = ("常客AI2.2.exe", "常客AI核心.exe", "常客AI自动更新器.exe")
PRESERVE_NAMES = (
    "deepseek_api_key.txt",
    "deepl_api_key.txt",
    "translation_config.json",
    "models_cache",
    "stickers",
    "temp_audio",
)


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundle_dir() -> Path:
    """Return PyInstaller's bundled data directory or the source directory."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", app_dir()))
    return Path(__file__).resolve().parent


def configured_manifest_url() -> str:
    """Allow the administrator to change the feed without rebuilding the EXE."""
    override = os.getenv("CHANGKE_UPDATE_MANIFEST_URL", "").strip()
    if override:
        return override
    config_path = app_dir() / "online_update_config.json"
    try:
        data = json.loads(config_path.read_text(encoding="utf-8-sig"))
        value = data.get("manifest_url")
        if isinstance(value, str) and value.strip():
            return value.strip()
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return DEFAULT_MANIFEST_URL


def candidate_targets() -> list[Path]:
    home = Path.home()
    candidates = [
        app_dir() / "常客AI2.2",
        home / "Desktop" / "常客AI2.2",
        home / "桌面" / "常客AI2.2",
        Path.cwd() / "常客AI2.2",
        Path.cwd(),
    ]
    result: list[Path] = []
    seen: set[Path] = set()
    for item in candidates:
        try:
            item = item.resolve()
        except OSError:
            continue
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def is_app_dir(folder: Path) -> bool:
    return folder.is_dir() and (folder / "_internal").is_dir() and any(
        (folder / name).is_file() for name in LAUNCHER_NAMES
    )


def find_app_dir() -> Path:
    for folder in candidate_targets():
        if is_app_dir(folder):
            return folder
        # Handle an extracted folder containing one extra 常客AI2.2 layer.
        if folder.is_dir():
            try:
                for child in folder.iterdir():
                    if is_app_dir(child):
                        return child
            except OSError:
                pass
    return next((p for p in candidate_targets() if p.name == "常客AI2.2"), candidate_targets()[0])


def read_json_url(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ChangkeAI-Updater/2.2", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
    data = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError("更新清单格式错误：根节点必须是对象")
    return data


def parse_version(value: str | None) -> tuple[int, ...]:
    if not value:
        return (0,)
    parts: list[int] = []
    for token in str(value).strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in token if ch.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts or [0])


def local_version(target: Path) -> str:
    for name in ("version.json", "version.txt"):
        path = target / name
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8-sig").strip()
            if name.endswith(".json"):
                value = json.loads(text).get("version")
                if value:
                    return str(value)
            elif text:
                return text.splitlines()[0].strip()
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    # 老版本没有版本文件，按 2.2.0 处理，保证第一次在线升级会执行。
    return "2.2.0"


def sha256(path: Path, callback=None) -> str:
    digest = hashlib.sha256()
    total = path.stat().st_size
    read = 0
    with path.open("rb") as handle:
        while True:
            block = handle.read(1024 * 1024)
            if not block:
                break
            digest.update(block)
            read += len(block)
            if callback:
                callback(read, total)
    return digest.hexdigest()


def stop_running_app() -> None:
    for image in PROCESS_NAMES:
        subprocess.run(
            ["taskkill", "/IM", image, "/F", "/T"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False,
        )
    time.sleep(0.8)


def safe_extract(zip_path: Path, destination: Path) -> None:
    destination = destination.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            name = member.filename.replace("\\", "/")
            if not name or name.endswith("/"):
                continue
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError(f"更新包包含不安全路径：{name}")
            target = (destination / relative).resolve()
            if destination != target and destination not in target.parents:
                raise ValueError(f"更新包包含不安全路径：{name}")
        archive.extractall(destination)


def locate_payload_root(staging: Path) -> Path:
    if (staging / "_internal").is_dir() or (staging / "常客AI2.2.exe").is_file():
        return staging
    candidates = [item for item in staging.iterdir() if item.is_dir()]
    for candidate in candidates:
        if (candidate / "_internal").is_dir() or (candidate / "常客AI2.2.exe").is_file():
            return candidate
    # 增量包通常只有几个文件，直接把 staging 作为覆盖层。
    return staging


def ensure_bundled_core(target: Path) -> None:
    """Bootstrap a core executable when a legacy full archive omitted it."""
    core = target / "常客AI核心.exe"
    launcher = target / "常客AI2.2.exe"
    if core.is_file() or not launcher.is_file():
        return
    bundled = bundle_dir() / "常客AI核心.exe"
    if not bundled.is_file():
        raise RuntimeError(
            "完整更新包缺少常客AI核心.exe，且更新器没有内置核心文件；"
            "请重新发布完整包。"
        )
    shutil.copy2(bundled, core)


def copy_tree_overlay(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)


def backup_user_files(target: Path, backup: Path) -> None:
    for name in PRESERVE_NAMES:
        source = target / name
        if not source.exists():
            continue
        # ``backup`` lives under update_backups.  Never copy the backup tree
        # into itself (older builds included update_backups in PRESERVE_NAMES,
        # which caused recursive paths and WinError 206).
        if source == backup or backup in source.parents:
            continue
        destination = backup / name
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def restore_user_files(backup: Path, target: Path) -> None:
    if not backup.is_dir():
        return
    copy_tree_overlay(backup, target)


def write_version(target: Path, manifest: dict) -> None:
    payload = {
        "app_id": APP_ID,
        "version": str(manifest["version"]),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    (target / "version.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def download(url: str, destination: Path, callback=None) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "file":
        source = Path(urllib.request.url2pathname(parsed.path))
        shutil.copy2(source, destination)
        if callback:
            callback(destination.stat().st_size, destination.stat().st_size)
        return
    request = urllib.request.Request(url, headers={"User-Agent": "ChangkeAI-Updater/2.2"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        total = int(response.headers.get("Content-Length") or 0)
        done = 0
        while True:
            block = response.read(1024 * 1024)
            if not block:
                break
            output.write(block)
            done += len(block)
            if callback:
                callback(done, total)


def apply_update(target: Path, package: Path, manifest: dict, status=None) -> tuple[Path, bool]:
    target = target.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target / "update_backups" / ("online_" + time.strftime("%Y%m%d_%H%M%S"))
    backup.mkdir(parents=True, exist_ok=True)
    existing = target.exists()
    if existing:
        backup_user_files(target, backup)
    if status:
        status("正在关闭常客AI…")
    stop_running_app()

    staging = Path(tempfile.mkdtemp(prefix="changke_update_", dir=str(target.parent)))
    try:
        if status:
            status("正在解压更新包…")
        safe_extract(package, staging)
        payload = locate_payload_root(staging)
        # A delta patch also contains ``_internal`` for the patched pyc.  A
        # package is full only when it includes both the runtime and launcher.
        is_full = (payload / "_internal").is_dir() and (payload / "常客AI2.2.exe").is_file()
        if not is_full and not (target / "_internal").is_dir():
            raise RuntimeError("这是增量包，电脑上没有完整常客AI目录；请在清单中提供 full_package_url。")
        if is_full:
            # 全量包允许首次安装；保留用户目录后覆盖运行文件。
            copy_tree_overlay(payload, target)
        else:
            copy_tree_overlay(payload, target)
        restore_user_files(backup, target)
        ensure_bundled_core(target)
        write_version(target, manifest)
    except Exception:
        # 原目录没有被清空，失败时只保留备份，方便人工恢复。
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return backup, is_full


class UpdaterWindow:
    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("常客AI · 在线更新器")
        self.root.geometry("680x390")
        self.root.resizable(False, False)
        self.root.configure(bg="#111521")
        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

        tk.Label(self.root, text="常客AI 在线更新器", fg="#ffffff", bg="#111521", font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w", padx=26, pady=(22, 4))
        tk.Label(self.root, text="自动检查版本，按需下载增量包；首次使用会下载完整运行包。模型、贴纸和密钥会保留。", fg="#aeb8d0", bg="#111521", wraplength=620, justify="left").pack(anchor="w", padx=26, pady=(0, 17))

        target_override = os.getenv("CHANGKE_APP_DIR", "").strip()
        self.target_var = tk.StringVar(value=target_override or str(find_app_dir()))
        self.manifest_var = tk.StringVar(value=configured_manifest_url())
        self.add_row("安装目录", self.target_var, self.choose_target)
        self.add_row("更新清单", self.manifest_var, self.choose_manifest)

        self.progress = tk.DoubleVar(value=0)
        tk.Label(self.root, textvariable=self._make_status(), fg="#8fe6c4", bg="#111521").pack(anchor="w", padx=26, pady=(17, 2))
        tk.Scale(self.root, variable=self.progress, from_=0, to=100, orient="horizontal", showvalue=False, length=620, state="disabled", highlightthickness=0, bg="#111521", troughcolor="#252c40", fg="#7657ed").pack(padx=26, pady=(0, 10))

        self.restart_var = tk.BooleanVar(value=True)
        tk.Checkbutton(self.root, text="更新完成后启动常客AI", variable=self.restart_var, fg="#dbe4ff", bg="#111521", activebackground="#111521", activeforeground="#ffffff", selectcolor="#1b2130").pack(anchor="w", padx=26)
        buttons = tk.Frame(self.root, bg="#111521")
        buttons.pack(fill="x", padx=26, pady=(15, 22))
        self.check_button = tk.Button(buttons, text="检查更新", command=self.check, width=14, bg="#2b3244", fg="#ffffff", activebackground="#3a4560")
        self.check_button.pack(side="left")
        self.update_button = tk.Button(buttons, text="开始更新", command=self.start, width=14, bg="#6f54ed", fg="#ffffff", activebackground="#846df5")
        self.update_button.pack(side="right")
        self.status = None

    def _make_status(self):
        self.status = tk.StringVar(value="准备就绪")
        return self.status

    def add_row(self, label: str, variable: tk.StringVar, command) -> None:
        row = tk.Frame(self.root, bg="#111521")
        row.pack(fill="x", padx=26, pady=4)
        tk.Label(row, text=label, width=9, anchor="w", fg="#dbe4ff", bg="#111521").pack(side="left")
        tk.Entry(row, textvariable=variable, bg="#1b2130", fg="#ffffff", insertbackground="#ffffff", relief="flat").pack(side="left", fill="x", expand=True, ipady=6)
        tk.Button(row, text="选择", command=command, width=8).pack(side="left", padx=(8, 0))

    def choose_target(self) -> None:
        value = filedialog.askdirectory(title="选择常客AI安装目录（首次安装可选择空目录）")
        if value:
            self.target_var.set(value)

    def choose_manifest(self) -> None:
        value = filedialog.askopenfilename(title="选择本地更新清单", filetypes=[("JSON", "*.json"), ("所有文件", "*.*")])
        if value:
            self.manifest_var.set(Path(value).resolve().as_uri())

    def set_status(self, text: str) -> None:
        self.root.after(0, lambda: self.status.set(text))

    def set_progress(self, done: int, total: int) -> None:
        value = (done / total * 100) if total else 0
        self.root.after(0, lambda: self.progress.set(value))

    def load_manifest(self) -> dict:
        manifest = read_json_url(self.manifest_var.get().strip())
        if manifest.get("app_id", APP_ID) != APP_ID:
            raise ValueError("更新清单不是常客AI的清单")
        # Accept the earlier {delta, full} format as well as the compact
        # package_url format used by the current updater.
        if not manifest.get("package_url"):
            channel = "delta" if is_app_dir(Path(self.target_var.get().strip().strip('"'))) else "full"
            entry = manifest.get(channel) or {}
            if entry.get("url") and entry.get("sha256"):
                manifest["package_url"] = entry["url"]
                manifest["package_sha256"] = entry["sha256"]
        for key in ("version", "package_url", "package_sha256"):
            if not manifest.get(key):
                raise ValueError(f"更新清单缺少字段：{key}")
        if str(manifest["package_sha256"]).lower().startswith(("put_", "替换", "your_")):
            raise ValueError("更新清单中的 package_sha256 还是占位符，请填写真实 SHA-256")
        return manifest

    def check(self) -> None:
        def worker():
            try:
                self.set_status("正在读取更新清单…")
                manifest = self.load_manifest()
                current = local_version(Path(self.target_var.get().strip().strip('"')))
                latest = str(manifest["version"])
                if parse_version(latest) <= parse_version(current):
                    self.set_status(f"当前已是最新版本 {current}")
                    self.root.after(0, lambda: messagebox.showinfo("检查更新", f"当前版本：{current}\n最新版本：{latest}"))
                else:
                    self.set_status(f"发现新版本 {latest}（当前 {current}）")
                    self.root.after(0, lambda: messagebox.showinfo("发现更新", f"最新版本：{latest}\n\n{manifest.get('release_notes', '')}"))
            except Exception as exc:
                self.set_status("读取更新清单失败")
                self.root.after(0, lambda: messagebox.showerror("检查更新失败", str(exc)))
        threading.Thread(target=worker, daemon=True).start()

    def start(self) -> None:
        if getattr(self, "worker", None) and self.worker.is_alive():
            return
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def _run(self) -> None:
        try:
            target = Path(self.target_var.get().strip().strip('"')).expanduser()
            self.set_status("正在读取更新清单…")
            manifest = self.load_manifest()
            current = local_version(target)
            latest = str(manifest["version"])
            if parse_version(latest) <= parse_version(current) and is_app_dir(target):
                self.root.after(0, lambda: messagebox.showinfo("无需更新", f"当前已经是 {current}"))
                self.set_status(f"当前已是最新版本 {current}")
                return

            cache_dir = target.parent / "update_downloads"
            cache_dir.mkdir(parents=True, exist_ok=True)
            package = cache_dir / ("changke-ai-" + latest + ".zip")
            self.set_status("正在下载更新包…")
            download(str(manifest["package_url"]), package, self.set_progress)
            self.set_status("正在校验下载包…")
            actual = sha256(package, self.set_progress)
            expected = str(manifest["package_sha256"]).lower().strip()
            if actual.lower() != expected:
                package.unlink(missing_ok=True)
                raise RuntimeError(f"更新包校验失败。\n期望：{expected}\n实际：{actual}")
            backup, full = apply_update(target, package, manifest, self.set_status)
            self.set_progress(100, 100)
            if self.restart_var.get():
                launcher = target / "常客AI2.2.exe"
                if launcher.is_file():
                    subprocess.Popen([str(launcher)], cwd=str(target), close_fds=True)
            self.set_status(f"更新完成：{latest}")
            self.root.after(0, lambda: messagebox.showinfo("更新完成", f"常客AI {latest} 更新完成。\n\n更新类型：{'完整安装' if full else '增量更新'}\n备份位置：\n{backup}"))
        except Exception as exc:
            self.set_status("更新失败，请检查网络和更新清单")
            self.root.after(0, lambda: messagebox.showerror("更新失败", str(exc)))

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    UpdaterWindow().run()
