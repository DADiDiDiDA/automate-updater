# -*- coding: utf-8 -*-
"""截图：挑面积最大的目标窗口。用法：python tools/shot2.py out.png [关键字]"""
import ctypes, sys, time
from ctypes import wintypes
from PIL import ImageGrab

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

user32 = ctypes.windll.user32

def windows(keyword):
    found = []
    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if keyword in buf.value:
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                found.append(((r.right-r.left)*(r.bottom-r.top), hwnd, buf.value))
        return True
    user32.EnumWindows(cb, 0)
    found.sort(reverse=True)
    return found

def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "shot.png"
    kw = sys.argv[2] if len(sys.argv) > 2 else "Automate"
    ws = windows(kw)
    if not ws:
        print("没找到窗口"); return 1
    _, hwnd, title = ws[0]
    r = wintypes.RECT(); user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right-r.left, r.bottom-r.top
    user32.SetWindowPos(hwnd, -1, 0, 0, w, h, 0x40 | 0x10)
    time.sleep(1.0)
    ImageGrab.grab(all_screens=True).crop((0, 0, w, h)).save(out)
    user32.SetWindowPos(hwnd, -2, 0, 0, w, h, 0x10)
    print(f"已保存 {out} ({w}x{h}) title={title}")
    return 0

sys.exit(main())
