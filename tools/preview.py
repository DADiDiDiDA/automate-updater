# -*- coding: utf-8 -*-
"""开发用：按指定皮肤/模式启动程序并截图。

用法：python tools/preview.py acrylic dark home out.png
      python tools/preview.py monet light settings out.png
"""

import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes

from PIL import ImageGrab

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(HERE, "config.json")


def set_theme(preset: str, mode: str, blur: bool = True):
    cfg = json.load(open(CFG, encoding="utf-8"))
    cfg.setdefault("ui", {}).update({"preset": preset, "theme": mode, "blur": blur})
    json.dump(cfg, open(CFG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def find_window(keyword="Automate"):
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(h, _):
        n = ctypes.windll.user32.GetWindowTextLengthW(h)
        if n:
            b = ctypes.create_unicode_buffer(n + 1)
            ctypes.windll.user32.GetWindowTextW(h, b, n + 1)
            if keyword in b.value and ctypes.windll.user32.IsWindowVisible(h):
                found.append(h)
        return True

    ctypes.windll.user32.EnumWindows(cb, 0)
    return found[0] if found else None


def main():
    preset = sys.argv[1] if len(sys.argv) > 1 else "acrylic"
    mode = sys.argv[2] if len(sys.argv) > 2 else "dark"
    page = sys.argv[3] if len(sys.argv) > 3 else "home"
    out = sys.argv[4] if len(sys.argv) > 4 else f"shot_{preset}_{mode}_{page}.png"
    blur = os.environ.get("NO_BLUR") != "1"

    set_theme(preset, mode, blur)
    env = dict(os.environ, AM_TOPMOST="1", AM_PAGE=page)
    py = sys.executable
    proc = subprocess.Popen([py, os.path.join(HERE, "app.py")], env=env, cwd=HERE,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        hwnd = None
        for _ in range(40):
            time.sleep(0.5)
            hwnd = find_window()
            if hwnd:
                break
        if not hwnd:
            print("窗口未出现")
            return 1
        time.sleep(6)   # 等首轮扫描出结果
        r = wintypes.RECT()
        ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(r))
        w, h = r.right - r.left, r.bottom - r.top
        HWND_TOPMOST, SWP_SHOWWINDOW, SWP_NOACTIVATE = -1, 0x40, 0x10
        ctypes.windll.user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, w, h,
                                         SWP_SHOWWINDOW | SWP_NOACTIVATE)
        time.sleep(1.2)
        ImageGrab.grab(all_screens=True).crop((0, 0, w, h)).save(os.path.join(HERE, out))
        print(f"已保存 {out} ({w}x{h})")
    finally:
        proc.terminate()
        try:
            proc.wait(5)
        except Exception:
            proc.kill()
        err = proc.stdout.read() if proc.stdout else ""
        if err.strip():
            print("--- 程序输出 ---")
            print(err[-1500:])
    return 0


if __name__ == "__main__":
    sys.exit(main())
