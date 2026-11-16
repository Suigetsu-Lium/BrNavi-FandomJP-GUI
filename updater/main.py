"""Tkinter entry point for the standalone BrNavi updater."""
from __future__ import annotations
import argparse, subprocess, sys, tkinter as tk, zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from updater.patch_applier import PatchApplyError, apply_patch, read_current_version

APP_NAME = "BrNavi Fandom JP Updater"

def _is_app_running() -> bool:
    if sys.platform != "win32": return False
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq BrNavi-GUI.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="mbcs", errors="replace",
            timeout=5, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
        return "BrNavi-GUI.exe" in result.stdout
    except (OSError, subprocess.SubprocessError):
        return False

class UpdaterWindow:
    def __init__(self, root: tk.Tk, install_dir: Path, patch_arg: str | None = None):
        self.root, self.install_dir = root, install_dir.resolve()
        root.title(APP_NAME); root.geometry("560x310"); root.minsize(520, 290); root.resizable(False, False)
        frame = ttk.Frame(root, padding=20); frame.pack(fill="both", expand=True)
        ttk.Label(frame, text=APP_NAME, font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(frame, text=f"インストール先: {self.install_dir}", wraplength=510).pack(anchor="w", pady=(8, 4))
        try: current = read_current_version(self.install_dir); startup_error = None
        except PatchApplyError as exc: current, startup_error = "不明", str(exc)
        ttk.Label(frame, text=f"現在のバージョン: v{current}").pack(anchor="w", pady=(4, 12))
        row = ttk.Frame(frame); row.pack(fill="x")
        self.path_var = tk.StringVar(value=patch_arg or "")
        ttk.Entry(row, textvariable=self.path_var).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="参照...", command=self.choose_patch).pack(side="left", padx=(8, 0))
        self.status = tk.StringVar(value=startup_error or "適用するパッチZIPを選択してください。")
        ttk.Label(frame, textvariable=self.status, wraplength=510).pack(anchor="w", pady=16)
        buttons = ttk.Frame(frame); buttons.pack(side="bottom", fill="x")
        ttk.Button(buttons, text="閉じる", command=root.destroy).pack(side="right")
        self.apply_button = ttk.Button(buttons, text="パッチを適用", command=self.apply)
        self.apply_button.pack(side="right", padx=(0, 8))
        if startup_error: self.apply_button.configure(state="disabled")

    def choose_patch(self):
        selected = filedialog.askopenfilename(title="BrNavi更新パッチを選択",
            filetypes=[("BrNavi patch", "*.zip"), ("All files", "*.*")])
        if selected:
            self.path_var.set(selected)
            self.status.set("パッチを選択しました。BrNavi本体を終了してから適用してください。")

    def apply(self):
        value = self.path_var.get().strip()
        if not value:
            messagebox.showwarning(APP_NAME, "先にパッチZIPを選択してください。"); return
        if _is_app_running():
            messagebox.showwarning(APP_NAME, "BrNavi-GUI.exeが起動中です。終了してから再試行してください。"); return
        if not messagebox.askyesno(APP_NAME, "パッチを適用します。既存のユーザーデータとデータベースは変更しません。続行しますか？"):
            return
        self.apply_button.configure(state="disabled"); self.status.set("パッチを検証・適用しています...")
        self.root.update_idletasks()
        try: version, backup = apply_patch(self.install_dir, Path(value))
        except (PatchApplyError, OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
            self.status.set("適用に失敗しました。"); messagebox.showerror(APP_NAME, str(exc))
            self.apply_button.configure(state="normal"); return
        self.status.set(f"v{version}への更新が完了しました。")
        messagebox.showinfo(APP_NAME, f"更新が完了しました。\nバージョン: v{version}\nバックアップ: {backup}")
        self.root.destroy()

def main() -> int:
    parser = argparse.ArgumentParser(description=APP_NAME)
    parser.add_argument("--install-dir", type=Path, help="BrNavi installation directory")
    parser.add_argument("--patch", help="Patch ZIP path")
    args = parser.parse_args()
    install_dir = args.install_dir or (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path.cwd())
    root = tk.Tk(); UpdaterWindow(root, install_dir, args.patch); root.mainloop()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
