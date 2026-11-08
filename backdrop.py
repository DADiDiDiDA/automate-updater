# -*- coding: utf-8 -*-
"""玻璃背景：取系统壁纸 → 高斯模糊 → 叠加主题色，得到稳定的亚克力观感。

不依赖 DWM 合成（在部分 Windows / 远程桌面环境下会把窗口画成透明），
所以任何机器上都能正确显示；关闭模糊时退回纯色/渐变。
"""

from __future__ import annotations

import ctypes
import os
import sys

from PIL import Image, ImageFilter
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPixmap

from theme import Palette, qc

SPI_GETDESKWALLPAPER = 0x0073


def wallpaper_path() -> str:
    if sys.platform != "win32":
        return ""
    try:
        buf = ctypes.create_unicode_buffer(520)
        ok = ctypes.windll.user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, 520, buf, 0)
        p = buf.value
        return p if ok and os.path.exists(p) else ""
    except Exception:
        return ""


def virtual_screen() -> QRect:
    """所有显示器组成的桌面区域（物理像素，已按 DPI 缩放由 Qt 处理）。"""
    screens = []
    try:
        from PySide6.QtGui import QGuiApplication
        for s in QGuiApplication.screens():
            screens.append(s.geometry())
    except Exception:
        pass
    if not screens:
        return QRect(0, 0, 1920, 1080)
    r = QRect(screens[0])
    for s in screens[1:]:
        r = r.united(s)
    return r


class Backdrop:
    """缓存并生成窗口背景图。"""

    def __init__(self):
        self._key = None
        self._pm: QPixmap | None = None
        self._wall = None
        self._wall_sig = None

    # ---------- 壁纸 ----------
    def _load_wall(self):
        path = wallpaper_path()
        sig = (path, os.path.getmtime(path) if path and os.path.exists(path) else 0)
        if sig == self._wall_sig and self._wall is not None:
            return self._wall
        self._wall_sig = sig
        img = None
        if path:
            try:
                img = Image.open(path).convert("RGB")
            except Exception:
                img = None
        if img is None:
            # 无壁纸（纯色桌面）时造一个柔和的渐变图
            img = Image.new("RGB", (1600, 900), (32, 40, 60))
            for y in range(900):
                for x in range(0, 1600, 8):
                    pass
        self._wall = img
        return img

    # ---------- 生成 ----------
    def render(self, p: Palette, size, pos: QPoint, blur: bool) -> QPixmap:
        w = max(1, int(size[0]))
        h = max(1, int(size[1]))
        key = (w, h, pos.x(), pos.y(), p.id, p.dark, blur)
        if self._pm is not None and key == self._key:
            return self._pm

        pm = QPixmap(w, h)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)

        if blur:
            base = self._blurred_wallpaper(p, w, h, pos)
            if base is not None:
                painter.drawPixmap(0, 0, base)
            else:
                self._fill_solid(painter, p, w, h)
            # 顶部光晕，增加层次
            g = QLinearGradient(0, 0, w, h)
            g.setColorAt(0.0, qc(p.accent, 0.10 if p.dark else 0.07))
            g.setColorAt(0.5, qc(p.accent, 0.02))
            g.setColorAt(1.0, qc(p.accent, 0.0))
            painter.fillRect(0, 0, w, h, g)
        else:
            self._fill_solid(painter, p, w, h)

        painter.end()
        self._pm = pm
        self._key = key
        return pm

    def _fill_solid(self, painter: QPainter, p: Palette, w: int, h: int) -> None:
        base = qc(p.bg)
        if p.dark:
            g = QLinearGradient(0, 0, w, h)
            g.setColorAt(0, qc(p.bg).lighter(112))
            g.setColorAt(1, base)
            painter.fillRect(0, 0, w, h, g)
        else:
            g = QLinearGradient(0, 0, 0, h)
            g.setColorAt(0, qc(p.bg).lighter(104))
            g.setColorAt(1, base)
            painter.fillRect(0, 0, w, h, g)

    def _blurred_wallpaper(self, p: Palette, w: int, h: int, pos: QPoint):
        try:
            wall = self._load_wall()
            scr = virtual_screen()
            # 壁纸铺满桌面（cover），再按窗口位置裁剪
            ratio = max(scr.width() / wall.width, scr.height() / wall.height)
            tw, th = max(1, int(wall.width * ratio)), max(1, int(wall.height * ratio))
            scaled = wall.resize((tw, th), Image.LANCZOS)
            ox = (tw - scr.width()) // 2
            oy = (th - scr.height()) // 2
            box = (max(0, pos.x() - scr.x() + ox), max(0, pos.y() - scr.y() + oy),
                   max(0, pos.x() - scr.x() + ox) + w, max(0, pos.y() - scr.y() + oy) + h)
            crop = scaled.crop(box)
            if crop.size != (w, h):
                crop = crop.resize((w, h), Image.LANCZOS)

            # 降采样 → 模糊 → 放大，速度快且更柔和
            small = crop.resize((max(1, w // 8), max(1, h // 8)), Image.LANCZOS)
            small = small.filter(ImageFilter.GaussianBlur(radius=9))
            blur = small.resize((w, h), Image.BILINEAR)

            # 叠加主题底色，保证文字对比度
            alpha = 0.88 if p.dark else 0.80
            overlay = Image.new("RGB", (w, h), QColor(p.bg).getRgb()[:3])
            composed = Image.blend(blur, overlay, alpha)

            data = composed.tobytes("raw", "RGB")
            img = QImage(data, w, h, w * 3, QImage.Format_RGB888).copy()
            return QPixmap.fromImage(img)
        except Exception:
            return None


BACKDROP = Backdrop()
