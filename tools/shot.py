# -*- coding: utf-8 -*-
"""开发用截图助手：把目标窗口置顶后截图，便于检查界面效果。

用法：python tools/shot.py out.png [窗口标题关键字]
"""

import ctypes
import sys
import time
from ctypes import wintypes

from PIL import ImageGrab

user32 = ctypes.windll.user32


def find_window(keyword: str):
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        n = user32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            if keyword in buf.value and user32.IsWindowVisible(hwnd):
                found.append((hwnd, buf.value))
        return True

    user32.EnumWindows(cb, 0)
    return found[0][0] if found else None


def minimize_all():
    VK_LWIN, VK_D = 0x5B, 0x44
    user32.keybd_event(VK_LWIN, 0, 0, 0)
    user32.keybd_event(VK_D, 0, 0, 0)
    user32.keybd_event(VK_D, 0, 2, 0)
    user32.keybd_event(VK_LWIN, 0, 2, 0)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "shot.png"
    keyword = sys.argv[2] if len(sys.argv) > 2 else "Automate"
    hwnd = find_window(keyword)
    if not hwnd:
        print("没找到窗口")
        return 1
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = r.right - r.left, r.bottom - r.top

    minimize_all()
    time.sleep(0.9)
    HWND_TOPMOST, SWP_SHOWWINDOW, SWP_NOACTIVATE = -1, 0x40, 0x10
    user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, w, h, SWP_SHOWWINDOW | SWP_NOACTIVATE)
    time.sleep(1.0)
    img = ImageGrab.grab(all_screens=True)
    img.crop((0, 0, w, h)).save(out)
    user32.SetWindowPos(hwnd, -2, 0, 0, w, h, SWP_NOACTIVATE)  # 取消置顶
    print(f"已保存 {out} ({w}x{h})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
