"""Tkinter entry point for the standalone BrNavi updater."""
from __future__ import annotations
import argparse, os, subprocess, sys, tkinter as tk, zipfile
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from updater.patch_applier import PatchApplyError, apply_patch, read_current_version

APP_NAME = "BrNavi Fandom JP Updater"

def _is_app_running(install_dir: Path) -> bool:
    """Check only the BrNavi process belonging to this installation.

    If Windows cannot provide a reliable answer, fail closed rather than risk
    replacing files while the application is running.
    """
    if sys.platform != "win32":
        return False
    target = str((install_dir / "BrNavi-GUI.exe").resolve())
    script = (
        "$ErrorActionPreference = 'Stop'; "
        "$target = [IO.Path]::GetFullPath($env:BRNAVI_TARGET); "
        "$items = Get-CimInstance Win32_Process -Filter \"Name='BrNavi-GUI.exe'\"; "
        "foreach ($item in $items) { "
        "if (-not $item.ExecutablePath) { exit 10 }; "
        "if ($item.ExecutablePath -and "
        "[String]::Equals([IO.Path]::GetFullPath($item.ExecutablePath), "
        "$target, [StringComparison]::OrdinalIgnoreCase)) { exit 10 } }; exit 0"
    )
    env = os.environ.copy()
    env["BRNAVI_TARGET"] = target
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            check=False, env=env,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"BrNaviの起動状態を確認できません: {exc}") from exc
    if result.returncode == 10:
        return True
    if result.returncode != 0:
        detail = result.stderr.strip() or f"PowerShell exit code: {result.returncode}"
        raise RuntimeError(f"BrNaviの起動状態を確認できません: {detail}")
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
        try:
            app_running = _is_app_running(self.install_dir)
        except RuntimeError as exc:
            messagebox.showerror(APP_NAME, f"{exc}\n安全のため更新を中止しました。"); return
        if app_running:
            messagebox.showwarning(APP_NAME, "このインストール先のBrNavi-GUI.exeが起動中です。終了してから再試行してください。"); return
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
