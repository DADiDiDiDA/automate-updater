# -*- coding: utf-8 -*-
"""开机自启动（HKCU Run 键）。绿色便携：删掉 exe 后改路径即可，不写任何系统服务。"""

from __future__ import annotations

import os
import sys

try:
    import winreg
except Exception:                                      # 非 Windows
    winreg = None

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "AutomateUpdater"


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def launch_command(silent: bool = True) -> str:
    """生成自启命令行：打包版直接跑 exe，源码版用 pythonw 跑 app.py。"""
    flag = " --tray" if silent else ""
    if getattr(sys, "frozen", False):
        return f'"{os.path.abspath(sys.executable)}"{flag}'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    runner = pyw if os.path.exists(pyw) else sys.executable
    script = os.path.join(app_dir(), "app.py")
    return f'"{runner}" "{script}"{flag}'


def current_value() -> str:
    if winreg is None:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            v, _ = winreg.QueryValueEx(k, VALUE_NAME)
            return str(v or "")
    except Exception:
        return ""


def is_enabled() -> bool:
    return bool(current_value())


def enable(silent: bool = True) -> bool:
    if winreg is None:
        return False
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.SetValueEx(k, VALUE_NAME, 0, winreg.REG_SZ, launch_command(silent))
        return True
    except Exception:
        return False


def disable() -> bool:
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, VALUE_NAME)
        return True
    except FileNotFoundError:
        return True
    except Exception:
        return False


def set_enabled(on: bool, silent: bool = True) -> bool:
    return enable(silent) if on else disable()
