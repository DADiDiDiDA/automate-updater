# -*- coding: utf-8 -*-
"""托盘图标：后台常驻入口。

鼠标悬停显示「程序名 + 上次检查时间 + 有几个新版本」，
右键菜单提供 打开主界面 / 设置 / 立即检查 / 静默更新 / 开机自启 / 退出。
"""

from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QCursor, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSystemTrayIcon

import core
from widgets import ThemedMenu


def _elapsed_text(stamp: str) -> str:
    stamp = (stamp or "").strip()
    if not stamp:
        return "尚未检查"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            t0 = time.mktime(time.strptime(stamp[:len(time.strftime(fmt))], fmt))
        except Exception:
            continue
        dt = max(0, time.time() - t0)
        if dt < 90:
            return "刚刚"
        if dt < 3600:
            return f"{int(dt // 60)} 分钟前"
        if dt < 86400:
            return f"{int(dt // 3600)} 小时前"
        return f"{int(dt // 86400)} 天前"
    return stamp


def _stamp_text(stamp: str) -> str:
    """把记录里的时间缩成「今天 22:14」这种短格式。"""
    stamp = (stamp or "").strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            t = time.strptime(stamp[:len(time.strftime(fmt))], fmt)
        except Exception:
            continue
        today = time.strftime("%Y-%m-%d")
        if time.strftime("%Y-%m-%d", t) == today:
            return time.strftime("%H:%M", t)
        return time.strftime("%m-%d %H:%M", t)
    return stamp


def badge_icon(base: QIcon, count: int) -> QIcon:
    """在图标右上角画一个红色数字角标。

    Windows 托盘按小图标尺寸（SM_CXSMICON，随 DPI 变化）来取图，
    这里按常用尺寸逐档原生渲染，避免只给一张大图被缩小后显得比别的图标小。
    """
    if count <= 0:
        return base
    src_any = base.pixmap(64, 64)
    if src_any.isNull():
        return base
    icon = QIcon()
    for s in (16, 20, 24, 32, 48, 64):
        pm = QPixmap(s, s)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        src = base.pixmap(s, s)
        if src.width() != s or src.height() != s:
            src = src.scaled(s, s, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        p.drawPixmap(0, 0, s, s, src)        # 底图铺满，和其它托盘图标一样大
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#F0453A"))
        d = s * (26 / 64) if count < 10 else s * (32 / 64)
        box = QRectF(s - d, 0, d, d)
        p.drawEllipse(box)
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(max(8, round(s * (17 / 64) if count < 10 else s * (15 / 64))))
        f.setWeight(QFont.Bold)
        p.setFont(f)
        p.setPen(QPen(QColor("#FFFFFF")))
        p.drawText(box, Qt.AlignCenter, str(count) if count < 100 else "99")
        p.end()
        icon.addPixmap(pm)
    return icon


class TrayIcon(QSystemTrayIcon):
    open_main = Signal()
    open_settings = Signal()
    check_now = Signal()
    silent_update = Signal()
    cancel_task = Signal()
    autostart_toggled = Signal(bool)
    quit_requested = Signal()

    def __init__(self, base_icon: QIcon, parent=None):
        super().__init__(base_icon, parent)
        self.base_icon = base_icon
        self._updates = 0
        self._busy = False
        self._last_check = ""
        self._menu = ThemedMenu()
        self._build_menu()
        self.activated.connect(self._on_activated)
        self.set_tooltip("", 0)

    # ---------- 菜单 ----------
    def _build_menu(self) -> None:
        m = self._menu
        self.act_open = m.addAction("打开主界面")
        self.act_open.triggered.connect(self.open_main.emit)
        self.act_settings = m.addAction("打开设置")
        self.act_settings.triggered.connect(self.open_settings.emit)
        m.addSeparator()
        self.act_check = m.addAction("立即检查更新")
        self.act_check.triggered.connect(self.check_now.emit)
        self.act_update = m.addAction("静默更新")
        self.act_update.triggered.connect(self.silent_update.emit)
        self.act_cancel = m.addAction("停止当前任务")
        self.act_cancel.setVisible(False)
        self.act_cancel.triggered.connect(self.cancel_task.emit)
        m.addSeparator()
        self.act_autostart = m.addAction("开机自动启动")
        self.act_autostart.setCheckable(True)
        self.act_autostart.toggled.connect(self._on_autostart)
        m.addSeparator()
        self.act_quit = m.addAction("退出")
        self.act_quit.triggered.connect(self.quit_requested.emit)

    def refresh_style(self) -> None:
        self._menu.update()

    def set_autostart(self, on: bool) -> None:
        self.act_autostart.blockSignals(True)
        self.act_autostart.setChecked(on)
        self.act_autostart.blockSignals(False)

    def _on_autostart(self, on: bool) -> None:
        self.autostart_toggled.emit(on)

    # ---------- 状态 ----------
    def set_tooltip(self, last_check: str, updates: int) -> None:
        self._last_check = last_check or self._last_check
        self._updates = int(updates or 0)
        when = _elapsed_text(self._last_check)
        if self._last_check:
            when = f"{when}（{_stamp_text(self._last_check)}）"
        lines = [core.APP_NAME, f"上次检查：{when}"]
        if self._busy:
            lines.append("正在检查中…")
        elif self._updates > 0:
            lines.append(f"{self._updates} 个程序有新版本")
        else:
            lines.append("全部已是最新")
        self.setToolTip("\n".join(lines))
        self.act_update.setText(f"静默更新（{self._updates}）" if self._updates else "静默更新")
        self.act_update.setEnabled(self._updates > 0 and not self._busy)
        self.act_check.setEnabled(not self._busy)
        self.setIcon(badge_icon(self.base_icon, self._updates))

    def set_busy(self, busy: bool, label: str = "") -> None:
        self._busy = busy
        self.act_cancel.setVisible(busy)
        self.act_check.setEnabled(not busy)
        self.act_update.setEnabled(self._updates > 0 and not busy)
        if busy and label:
            self.setToolTip(f"{core.APP_NAME}\n{label}")
        else:
            self.set_tooltip(self._last_check, self._updates)

    def notify(self, title: str, body: str, msec: int = 6000) -> None:
        try:
            self.showMessage(title, body, QSystemTrayIcon.Information, msec)
        except Exception:
            pass

    # ---------- 交互 ----------
    def _on_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.open_main.emit()
        elif reason == QSystemTrayIcon.Context:
            # QSystemTrayIcon.setContextMenu 只接受原生 QMenu（会留悬停残影），
            # 这里右键时手动弹出同款自绘玻璃菜单
            self._menu.popup_at(QCursor.pos())
