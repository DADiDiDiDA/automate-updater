# -*- coding: utf-8 -*-
"""玻璃背景：取系统壁纸 → 高斯模糊 → 叠加主题色，得到稳定的亚克力观感。

不依赖 DWM 合成（在部分 Windows / 远程桌面环境下会把窗口画成透明），
所以任何机器上都能正确显示；关闭模糊时退回纯色/渐变。
"""

from __future__ import annotations

import ctypes
import os
import sys
import time

from PIL import Image, ImageFilter
from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPixmap

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
    """缓存并生成窗口背景图。

    主窗口、对话框、弹层、右键菜单都从这里取同一张模糊壁纸，
    所以各处玻璃质感是一致且连续（按各自在屏幕上的位置裁剪）。
    """

    MAX_CACHE = 10
    SNAP_TTL = 1.5          # 屏幕取样有效期：超过就退回壁纸模糊

    def __init__(self):
        self._cache: dict = {}
        self._wall = None
        self._wall_sig = None
        self._snap = None          # (屏幕区域, 截屏, 时间戳)
        self._snap_cache: dict = {}

    # ---------- 真实屏幕取样 ----------
    def snapshot(self, screen=None) -> None:
        """弹层 / 右键菜单出现前截一张屏，作为亚克力的取样来源。

        这样菜单模糊的是「它背后的真实内容」，而不是只贴一张壁纸，
        毛玻璃开关打开时才有真正的透亮感。
        """
        try:
            from PySide6.QtGui import QGuiApplication
            scr = screen or QGuiApplication.primaryScreen()
            if scr is None:
                self._snap = None
                return
            pm = scr.grabWindow(0)
            self._snap = None if pm.isNull() else (scr.geometry(), pm, time.time())
            self._snap_cache.clear()
        except Exception:
            self._snap = None

    def _snapshot_blur(self, p: Palette, w: int, h: int, pos: QPoint):
        snap = self._snap
        if snap is None:
            return None
        geo, shot, ts = snap
        if time.time() - ts > self.SNAP_TTL:
            return None
        if not geo.contains(QRect(pos, QSize(w, h))):
            return None
        key = (w, h, pos.x(), pos.y(), p.id, p.dark, ts)
        hit = self._snap_cache.get(key)
        if hit is not None:
            return hit
        try:
            dpr = shot.devicePixelRatio() or 1.0
            x = int(round((pos.x() - geo.x()) * dpr))
            y = int(round((pos.y() - geo.y()) * dpr))
            cw, ch = max(1, int(round(w * dpr))), max(1, int(round(h * dpr)))
            crop = shot.copy(x, y, cw, ch)
            if crop.isNull() or crop.width() < cw or crop.height() < ch:
                return None
            img = crop.toImage().convertToFormat(QImage.Format_RGB888)
            data = bytes(img.constBits())
            pil = Image.frombytes("RGB", (img.width(), img.height()), data, "raw", "RGB",
                                  img.bytesPerLine())
            if pil.size != (w, h):
                pil = pil.resize((w, h), Image.LANCZOS)
            small = pil.resize((max(1, w // 8), max(1, h // 8)), Image.LANCZOS)
            small = small.filter(ImageFilter.GaussianBlur(radius=6))
            blur = small.resize((w, h), Image.BILINEAR)
            # 根据背后画面的明暗自适应着色：底子越亮，深色主题压得越狠，
            # 反过来浅色主题也一样，保证菜单文字永远读得清，又保留玻璃的透。
            lum = blur.convert("L").resize((1, 1)).getpixel((0, 0))
            if p.dark:
                alpha = 0.30 + 0.52 * max(0.0, (lum - 64) / 120.0)
            else:
                alpha = 0.30 + 0.42 * max(0.0, (152 - lum) / 120.0)
            overlay = Image.new("RGB", (w, h), QColor(p.bg).getRgb()[:3])
            composed = Image.blend(blur, overlay, min(0.94, alpha))
            raw = composed.tobytes("raw", "RGB")
            out = QImage(raw, w, h, w * 3, QImage.Format_RGB888).copy()
            pm = QPixmap.fromImage(out)
        except Exception:
            return None
        if len(self._snap_cache) >= self.MAX_CACHE:
            self._snap_cache.pop(next(iter(self._snap_cache)), None)
        self._snap_cache[key] = pm
        return pm

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
            # 一列像素做竖向渐变再横向拉伸，避免 18 万次空循环
            grad = Image.new("RGB", (1, 900))
            gp = grad.load()
            for y in range(900):
                t = y / 899.0
                gp[0, y] = (int(32 + 16 * t), int(40 + 18 * t), int(60 + 26 * t))
            img = grad.resize((1600, 900), Image.BILINEAR)
        self._wall = img
        return img

    # ---------- 生成 ----------
    def render(self, p: Palette, size, pos: QPoint, blur: bool) -> QPixmap:
        w = max(1, int(size[0]))
        h = max(1, int(size[1]))
        key = (w, h, pos.x(), pos.y(), p.id, p.dark, blur)
        hit = self._cache.get(key)
        if hit is not None:
            return hit

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
        if len(self._cache) >= self.MAX_CACHE:
            # 简单的 FIFO 淘汰，足够覆盖「主窗口 + 若干弹层」
            self._cache.pop(next(iter(self._cache)), None)
        self._cache[key] = pm
        return pm

    def render_glass(self, p: Palette, size, pos: QPoint) -> QPixmap:
        """弹层专用的玻璃底：优先模糊「背后的真实画面」，取不到才退回壁纸。"""
        w = max(1, int(size[0]))
        h = max(1, int(size[1]))
        pm = self._snapshot_blur(p, w, h, pos)
        if pm is not None:
            return pm
        return self.render(p, size, pos, True)

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


def paint_glass(painter: QPainter, rect: QRectF, radius: float, global_pos: QPoint,
                p: Palette, blur: bool, tint: float | None = None) -> QPainterPath:
    """在任意顶层窗口上画一块「毛玻璃」底板，返回它的圆角路径。

    对话框、下拉弹层、右键菜单都用它，保证和主窗口是同一种玻璃；
    弹层会先用「背后真实画面」的模糊图，取不到才退回壁纸；
    关闭模糊时退回不透明表面色。
    """
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)

    painter.save()
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setClipPath(path)
    if blur:
        pm = BACKDROP.render_glass(p, (int(rect.width()), int(rect.height())), global_pos)
        painter.drawPixmap(rect.topLeft(), pm)
        if tint:
            surf = p.surface()
            surf.setAlphaF(tint)
            painter.fillPath(path, surf)
        # 顶部一道极淡的高光，让弹层有玻璃的厚度感
        glow = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        glow.setColorAt(0.0, qc("#FFFFFF", 0.07 if p.dark else 0.30))
        glow.setColorAt(0.35, qc("#FFFFFF", 0.0))
        painter.fillPath(path, glow)
    else:
        painter.fillPath(path, p.surface())
    painter.restore()
    return path
