# -*- coding: utf-8 -*-
"""主窗口与应用入口。"""

from __future__ import annotations

import ctypes
import math
import os
import sys

from PySide6.QtCore import (QEasingCurve, QEvent, QPoint, QPointF, QPropertyAnimation, QRectF,
                            QSize, Qt, QThread, QTimer, Signal)
from PySide6.QtGui import (QColor, QCursor, QFont, QGuiApplication, QIcon, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap, QRadialGradient)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QButtonGroup, QDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QPushButton, QSizePolicy, QStackedWidget,
                               QVBoxLayout, QWidget)

import core
import autostart
from backdrop import BACKDROP
from pages import (HomePage, ItemListView, SettingsPage, ThemedDialog, VersionDialog, _label)
from theme import (GLYPH, PRESET_LABELS, PRESET_ORDER, THEME, build_qss, qc, rgba)
from tray import TrayIcon
from widgets import Card, Glyph, NavButton, Toast

def mode_icon(to_dark: bool) -> QIcon:
    """自绘太阳 / 月亮图标：图标字体缺字时也不会变成怪字符。"""
    color = QColor(THEME.p.text_dim)
    pm = QPixmap(36, 36)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = QPointF(18, 18)
    if to_dark:
        outer = QPainterPath()
        outer.addEllipse(QRectF(7, 7, 22, 22))
        cut = QPainterPath()
        cut.addEllipse(QRectF(14, 3, 21, 21))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPath(outer.subtracted(cut))
    else:
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(c, 7.0, 7.0)
        p.setPen(QPen(color, 2.0, Qt.SolidLine, Qt.RoundCap))
        for i in range(8):
            a = i * math.pi / 4
            p.drawLine(QPointF(c.x() + math.cos(a) * 10, c.y() + math.sin(a) * 10),
                       QPointF(c.x() + math.cos(a) * 14, c.y() + math.sin(a) * 14))
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------- 亚克力模糊

class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint), ("AnimationId", ctypes.c_int)]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p),
                ("SizeOfData", ctypes.c_size_t)]


def _abgr(hex_color: str, alpha: int) -> int:
    c = QColor(hex_color)
    return (alpha << 24) | (c.blue() << 16) | (c.green() << 8) | c.red()


def apply_acrylic(hwnd: int, enabled: bool, tint: str = "#15171C", alpha: int = 0xB4) -> bool:
    """给窗口加亚克力模糊（Win10/11）。返回是否成功。"""
    if sys.platform != "win32":
        return False
    try:
        user32 = ctypes.windll.user32
        set_attr = user32.SetWindowCompositionAttribute
        set_attr.argtypes = [ctypes.c_void_p, ctypes.POINTER(WINDOWCOMPOSITIONATTRIBDATA)]
        policy = ACCENT_POLICY()
        policy.AccentState = 4 if enabled else 0        # 4 = ACRYLICBLURBEHIND
        policy.AccentFlags = 0
        policy.GradientColor = _abgr(tint, alpha if enabled else 0)
        policy.AnimationId = 0
        data = WINDOWCOMPOSITIONATTRIBDATA(19, ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p),
                                          ctypes.sizeof(policy))  # 19 = WCA_ACCENT_POLICY
        set_attr(ctypes.c_void_p(hwnd), ctypes.byref(data))
        return True
    except Exception:
        return False


def set_rounded_corners(hwnd: int) -> None:
    try:
        dwm = ctypes.windll.dwmapi
        pref = ctypes.c_int(2)      # DWMWCP_ROUND
        dwm.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), ctypes.c_int(33),
                                  ctypes.byref(pref), ctypes.sizeof(pref))
    except Exception:
        pass


# ---------------------------------------------------------------- 后台线程

class ScanWorker(QThread):
    item_ready = Signal(object)
    progress = Signal(int, int, str)
    finished_all = Signal(list)

    def __init__(self, engine: core.Engine, items):
        super().__init__()
        self.engine = engine
        self.items = items

    def run(self):
        try:
            self.engine.scan_all(
                self.items,
                on_item=lambda it: self.item_ready.emit(it),
                on_progress=lambda d, t, it: self.progress.emit(d, t, it.name),
            )
        finally:
            self.finished_all.emit(self.items)


class UpdateWorker(QThread):
    item_started = Signal(object)
    item_progress = Signal(object, float)
    item_done = Signal(object)
    finished_all = Signal(list)

    def __init__(self, engine: core.Engine, items):
        super().__init__()
        self.engine = engine
        self.items = items

    def run(self):
        try:
            for it in self.items:
                if self.engine.cancel.is_set():
                    it.detail = "已取消"
                    break
                self.item_started.emit(it)
                res = self.engine.update_item(
                    it, on_progress=lambda i, p, m=None: self.item_progress.emit(i, p))
                self.item_done.emit(res)
        finally:
            self.finished_all.emit(self.items)


class PrepareWorker(QThread):
    """扫描前的准备工作：探测代理、测速镜像。"""

    done = Signal(str, str)

    def __init__(self, engine: core.Engine):
        super().__init__()
        self.engine = engine

    def run(self):
        proxy = self.engine.proxy or ""
        mirror = self.engine.ensure_mirror() or ""
        self.done.emit(proxy, mirror)


class MirrorWorker(QThread):
    """并发测速所有镜像。"""

    done = Signal(list, str)

    def __init__(self, engine: core.Engine):
        super().__init__()
        self.engine = engine

    def run(self):
        try:
            ranked = self.engine.measure_mirrors()
            self.done.emit(ranked, "")
        except Exception as e:
            self.done.emit([], f"{type(e).__name__}: {e}")


# ---------------------------------------------------------------- 窗口按钮

class WinBtn(QPushButton):
    """自绘的窗口按钮（最小化 / 最大化 / 还原 / 关闭）。

    不用图标字体，避免不同系统上字形错位或显示成方块。
    """

    def __init__(self, kind: str, tip: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setToolTip(tip)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(34, 26)
        self.setFlat(True)
        self.setFocusPolicy(Qt.NoFocus)
        self._hover = False

    def set_kind(self, kind: str) -> None:
        if kind != self.kind:
            self.kind = kind
            self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, 6, 6)
        if self._hover:
            if self.kind == "close":
                painter.fillPath(path, qc(p.danger, 0.92))
            else:
                painter.fillPath(path, qc("#FFFFFF" if p.dark else "#000000", 0.10))
        if self._hover and self.kind == "close":
            col = QColor("#FFFFFF")
        else:
            col = QColor(p.text if self._hover else p.text_dim)
        pen = QPen(col, 1.15)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        cx = r.center().x()
        cy = r.center().y()
        s = 4.2
        if self.kind == "min":
            painter.drawLine(QPointF(cx - s, cy + 0.6), QPointF(cx + s, cy + 0.6))
        elif self.kind == "max":
            painter.drawRect(QRectF(cx - s, cy - s, s * 2, s * 2))
        elif self.kind == "restore":
            painter.drawRect(QRectF(cx - s + 1.8, cy - s - 0.8, s * 2 - 1.8, s * 2 - 1.8))
            painter.drawRect(QRectF(cx - s - 0.8, cy - s + 1.8, s * 2 - 1.8, s * 2 - 1.8))
        elif self.kind == "close":
            painter.drawLine(QPointF(cx - s, cy - s), QPointF(cx + s, cy + s))
            painter.drawLine(QPointF(cx + s, cy - s), QPointF(cx - s, cy + s))
        painter.end()


# ---------------------------------------------------------------- 绑定仓库对话框

class BindDialog(ThemedDialog):
    def __init__(self, parent, name: str, current: str = ""):
        super().__init__(parent, "绑定 GitHub 仓库", 440)
        self.body.addWidget(_label(f"为「{name}」指定 GitHub 仓库", 13, "text", bold=True))
        self.body.addWidget(_label("格式：owner/repo，例如 MAA1999/M9A；之后会自动读取它的最新 Release",
                                   11, "text_muted", wrap=True))
        self.edit = QLineEdit(current)
        self.edit.setPlaceholderText("owner/repo")
        self.edit.setMinimumHeight(32)
        self.body.addWidget(self.edit)
        row = QHBoxLayout()
        row.addStretch(1)
        c = QPushButton("取消")
        c.setObjectName("ghost")
        c.setCursor(Qt.PointingHandCursor)
        c.setFixedHeight(32)
        c.clicked.connect(self.reject)
        ok = QPushButton("保存")
        ok.setObjectName("primary")
        ok.setCursor(Qt.PointingHandCursor)
        ok.setFixedHeight(32)
        ok.clicked.connect(self.accept)
        row.addWidget(c)
        row.addWidget(ok)
        self.body.addLayout(row)

    def value(self) -> str:
        return self.edit.text().strip().strip("/")


# ---------------------------------------------------------------- 主窗口

class MainWindow(QWidget):
    def __init__(self, start_hidden: bool = False):
        super().__init__()
        self.cfg = core.load_config()
        THEME.set_preset(self.cfg.get("ui", {}).get("preset", "acrylic"))
        THEME.set_mode(self.cfg.get("ui", {}).get("theme", "auto"))
        THEME.set_blur(bool(self.cfg.get("ui", {}).get("blur", True)))

        self.engine = core.Engine(self.cfg)
        self.items: list[core.Item] = []
        self.scan_worker = None
        self.update_worker = None
        self.prep_worker = None
        self.tray: TrayIcon | None = None
        self._silent_scan = start_hidden
        self._background_update = start_hidden
        self._quitting = False
        self._tray_hint_shown = False
        self._dragging = False
        self._drag_pos = QPoint()
        self._bg = None
        self._bg_timer = QTimer(self)
        self._bg_timer.setSingleShot(True)
        self._bg_timer.timeout.connect(self._rebuild_backdrop)

        self.setWindowTitle(core.APP_NAME)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window | Qt.WindowMinMaxButtonsHint)
        if os.environ.get("AM_TOPMOST") == "1":
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        # 最小宽度要容纳「筛选胶囊 + 搜索框 + 两个排序下拉」一整行，否则会被裁掉
        self.setMinimumSize(1120, 690)
        geo = (self.cfg.get("ui") or {}).get("geometry") or []
        if len(geo) == 4:
            self.setGeometry(int(geo[0]), int(geo[1]), int(geo[2]), int(geo[3]))
        else:
            self.resize(1180, 790)

        self._build_ui()
        self._sync_system_theme()
        self._apply_theme()
        QGuiApplication.styleHints().colorSchemeChanged.connect(self._on_system_scheme)

        self.toast = Toast(self)
        start_page = os.environ.get("AM_PAGE", "").strip()
        if start_page in ("home", "programs", "settings"):
            self._on_nav(start_page)
        if os.environ.get("AM_EXPAND_NOTIFY") == "1":
            self.page_settings.notify_card.set_open(True)
        self._init_tray()
        self._init_scheduler()
        shot = os.environ.get("AM_SHOT", "").strip()
        if shot:
            QTimer.singleShot(2600, lambda: self._dev_shot(shot))
        if not start_hidden:
            QTimer.singleShot(600, lambda: self.start_scan(silent=False))
        else:
            # 后台启动：稍等片刻再静默检查，避免和开机时的磁盘/网络高峰抢资源
            QTimer.singleShot(4000, lambda: self.start_scan(silent=True))

    # ---------- UI ----------
    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # 侧边栏
        self.side = QWidget(self)
        self.side.setFixedWidth(226)
        sl = QVBoxLayout(self.side)
        sl.setContentsMargins(14, 14, 10, 14)
        sl.setSpacing(4)

        brand = QWidget()
        bl = QHBoxLayout(brand)
        bl.setContentsMargins(6, 6, 6, 10)
        bl.setSpacing(10)
        self.logo = QLabel()
        self.logo.setFixedSize(30, 30)
        bl.addWidget(self.logo)
        bcol = QVBoxLayout()
        bcol.setSpacing(0)
        self.brand_name = QLabel(core.APP_NAME)
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(13)
        f.setWeight(QFont.DemiBold)
        self.brand_name.setFont(f)
        self.brand_ver = QLabel(f"v{core.APP_VERSION}")
        f2 = QFont("Microsoft YaHei UI")
        f2.setPixelSize(10)
        self.brand_ver.setFont(f2)
        bcol.addWidget(self.brand_name)
        bcol.addWidget(self.brand_ver)
        bl.addLayout(bcol)
        bl.addStretch(1)
        sl.addWidget(brand)
        for w in [brand] + brand.findChildren(QWidget):
            w.installEventFilter(self)

        self.nav_group = []
        self.nav_home = NavButton("home", "home", "首页")
        self.nav_progs = NavButton("programs", "apps", "程序")
        self.nav_settings = NavButton("settings", "settings", "设置")
        for btn in (self.nav_home, self.nav_progs, self.nav_settings):
            btn.clicked.connect(self._on_nav)
            self.nav_group.append(btn)
            sl.addWidget(btn)

        sl.addStretch(1)

        self.side_status = QLabel("")
        fs = QFont("Microsoft YaHei UI")
        fs.setPixelSize(10)
        self.side_status.setFont(fs)
        self.side_status.setTextFormat(Qt.RichText)
        self.side_status.setWordWrap(True)
        self.side_status.setContentsMargins(10, 0, 6, 4)
        sl.addWidget(self.side_status)

        self.theme_btn = QPushButton("切换明暗")
        self.theme_btn.setObjectName("ghost")
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.setIconSize(QSize(16, 16))
        self.theme_btn.setFixedHeight(32)
        self.theme_btn.clicked.connect(self._toggle_quick_theme)
        sl.addWidget(self.theme_btn)

        root.addWidget(self.side)

        # 右侧
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        self.header = QWidget()
        self.header.setFixedHeight(50)
        self.header.setMouseTracking(True)
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(22, 12, 12, 0)
        hl.setSpacing(6)
        self.page_title = QLabel("首页")
        fpt = QFont("Microsoft YaHei UI")
        fpt.setPixelSize(15)
        fpt.setWeight(QFont.DemiBold)
        self.page_title.setFont(fpt)
        hl.addWidget(self.page_title)
        hl.addStretch(1)

        self.btn_min = WinBtn("min", "最小化")
        self.btn_max = WinBtn("max", "最大化")
        self.btn_close = WinBtn("close", "关闭")
        for b in (self.btn_min, self.btn_max, self.btn_close):
            hl.addWidget(b)
        self.btn_min.clicked.connect(self.showMinimized)
        self.btn_max.clicked.connect(self._toggle_max)
        self.btn_close.clicked.connect(self.close)
        rl.addWidget(self.header)
        for w in [self.header, self.page_title]:
            w.installEventFilter(self)

        self.stack = QStackedWidget()
        self.stack.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.stack, 1)
        root.addWidget(right, 1)

        # 页面
        self.page_home = HomePage()
        self.page_home.scan_requested.connect(self.start_scan)
        self.page_home.cancel_requested.connect(self.cancel_tasks)
        self.page_home.update_all.connect(self.update_all)
        self.page_home.row_update.connect(self.update_one)
        self.page_home.row_open.connect(self.open_item_folder)
        self.page_home.row_bind.connect(self.bind_repo)
        self.page_home.row_exclude.connect(self.exclude_item)
        self.page_home.row_repo.connect(self.open_item_repo)
        self.page_home.row_mark.connect(self.mark_version)

        self.page_progs = QWidget()
        pl = QVBoxLayout(self.page_progs)
        pl.setContentsMargins(22, 4, 22, 18)
        self.progs_header = _label("所有程序", 17, "text", bold=True)
        pl.addWidget(self.progs_header)
        pl.addWidget(_label("双击一行可打开所在文件夹；右键可绑定仓库、标记版本或排除", 12, "text_muted"))
        pl.addSpacing(8)
        self.progs_list = ItemListView()
        self.progs_list.row_update.connect(self.update_one)
        self.progs_list.row_open.connect(self.open_item_folder)
        self.progs_list.row_bind.connect(self.bind_repo)
        self.progs_list.row_exclude.connect(self.exclude_item)
        self.progs_list.row_repo.connect(self.open_item_repo)
        self.progs_list.row_mark.connect(self.mark_version)
        pl.addWidget(self.progs_list, 1)

        ui = self.cfg.get("ui") or {}
        for lv in (self.page_home.list, self.progs_list):
            lv.set_prior(ui.get("prior", "update"))
            lv.set_sort(ui.get("sort", "name"))
            lv.sort_changed.connect(self._on_sort_changed)
            lv.prior_changed.connect(self._on_prior_changed)

        self.page_settings = SettingsPage(self.cfg)
        self._wire_settings(self.page_settings)

        for page in (self.page_home, self.page_progs, self.page_settings):
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(22, 4, 18, 18)
            wl.addWidget(page)
            self.stack.addWidget(wrap)
        self._page_wraps = [self.stack.widget(i) for i in range(self.stack.count())]

        self.nav_home.set_active(True)
        self._update_brand()

        # 键盘快捷键：Ctrl+1/2/3 切页，F5 重新检查
        from PySide6.QtGui import QKeySequence, QShortcut
        for key, seq in (("home", "Ctrl+1"), ("programs", "Ctrl+2"), ("settings", "Ctrl+3")):
            sc = QShortcut(QKeySequence(seq), self)
            sc.activated.connect(lambda k=key: self._on_nav(k))
        QShortcut(QKeySequence("F5"), self).activated.connect(self.start_scan)

    def _wire_settings(self, page: SettingsPage) -> None:
        page.theme_changed.connect(self._apply_theme)
        page.roots_changed.connect(self._on_roots_changed)
        page.config_changed.connect(self._save_cfg)
        page.autostart_requested.connect(self._set_autostart)
        page.mirror_probe_requested.connect(self._probe_mirrors)
        page.proxy_changed.connect(self._on_proxy_changed)

    def _probe_mirrors(self) -> None:
        """设置页「立即测速」：后台线程并发测速所有镜像。"""
        if getattr(self, "_probe_worker", None) and self._probe_worker.isRunning():
            return
        self._probe_worker = MirrorWorker(self.engine)
        self._probe_worker.done.connect(self._on_mirror_probed)
        self._probe_worker.start()

    def _on_mirror_probed(self, ranked, error) -> None:
        self.toast.show_text(f"镜像测速完成，可用 {len(ranked)} 个" if ranked
                             else "所有镜像都没有响应", ok=bool(ranked))
        if hasattr(self, "page_settings"):
            self.page_settings.set_mirror_results(ranked, error)
            self.page_settings.set_network_info(self._net_text.replace("\n", "　|　"))
        if self.tray is not None:
            self.tray.set_tooltip(self.engine.records.get("last_scan", ""),
                                  sum(1 for i in self.items
                                      if i.status == "update"
                                      or (i.status == "unknown" and i.scanned)))
        self._refresh_net_info()

    def _on_proxy_changed(self) -> None:
        """设置页改了代理方式 / 地址后立刻生效。"""
        proxy = self.engine.refresh_proxy()
        self._update_brand()
        if proxy:
            self.toast.show_text(f"代理已切换为 {proxy}")
        else:
            self.toast.show_text("当前不使用代理，走直连 / 镜像加速")

    # ---------- 主题 ----------
    def _sync_system_theme(self) -> None:
        try:
            hints = QGuiApplication.styleHints()
            dark = hints.colorScheme() == Qt.ColorScheme.Dark
        except Exception:
            dark = True
        THEME.set_system_dark(dark)

    def _on_system_scheme(self, scheme) -> None:
        THEME.set_system_dark(scheme == Qt.ColorScheme.Dark)
        if THEME.mode == "auto":
            self._apply_theme()

    def _toggle_quick_theme(self) -> None:
        if THEME.mode == "auto":
            THEME.set_mode("light" if THEME.is_dark else "dark")
        else:
            THEME.set_mode("dark" if THEME.mode == "light" else "light")
        self._apply_theme()

    def _apply_theme(self) -> None:
        p = THEME.p
        self.setStyleSheet(build_qss(p))
        self.theme_btn.setText("切换到浅色" if THEME.is_dark else "切换到深色")
        self.theme_btn.setIcon(mode_icon(not THEME.is_dark))

        self.brand_name.setStyleSheet(f"color: {p.text}; background: transparent;")
        self.brand_ver.setStyleSheet(f"color: {p.text_muted}; background: transparent;")
        self.page_title.setStyleSheet(f"color: {p.text}; background: transparent;")
        self.progs_header.setStyleSheet(f"color: {p.text}; background: transparent;")
        self.side_status.setStyleSheet(f"color: {p.text_muted}; background: transparent;")
        for b in (self.btn_min, self.btn_max, self.btn_close):
            b.update()

        # 图标与自绘控件
        for nav in self.nav_group:
            nav.update()
        self._update_logo()
        self.page_home.refresh_theme()
        self.progs_list.refresh_theme()
        self._rebuild_settings()
        self._update_background()
        self.update()

    def _rebuild_settings(self) -> None:
        """主题变化后重建设置页（保证分组文字配色一致）。"""
        idx = self.stack.indexOf(self._page_wraps[2])
        old = self._page_wraps[2]
        new_page = SettingsPage(self.cfg)
        self._wire_settings(new_page)
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(22, 4, 18, 18)
        wl.addWidget(new_page)
        self.stack.insertWidget(idx, wrap)
        self.stack.removeWidget(old)
        old.deleteLater()
        self._page_wraps[2] = wrap
        self.page_settings = new_page
        if getattr(self, "_net_text", ""):
            self.page_settings.set_network_info(self._net_text.replace("\n", "　|　"))
        if idx == self.stack.currentIndex():
            self.stack.setCurrentWidget(wrap)

    def _update_logo(self) -> None:
        p = THEME.p
        pm = QPixmap(60, 60)
        pm.setDevicePixelRatio(2.0)
        pm.fill(Qt.transparent)
        painter = QPainter(pm)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0, 0, 30, 30)
        path = QPainterPath()
        path.addRoundedRect(rect, 9, 9)
        g = QLinearGradient(rect.topLeft(), rect.bottomRight())
        g.setColorAt(0, qc(p.accent, 0.95))
        g.setColorAt(1, qc(p.accent_hover, 0.85))
        painter.fillPath(path, g)
        f = QFont("Segoe Fluent Icons")
        f.setPixelSize(15)
        painter.setFont(f)
        painter.setPen(QPen(qc("#FFFFFF", 0.96)))
        painter.drawText(rect, Qt.AlignCenter, GLYPH["refresh"])
        painter.end()
        self.logo.setPixmap(pm)

    # ---------- 背景绘制 ----------
    def _update_background(self) -> None:
        p = THEME.p
        try:
            hwnd = int(self.winId())
            set_rounded_corners(hwnd)
            if os.environ.get("AM_DWM_ACRYLIC") == "1" and THEME.blur_active:
                apply_acrylic(hwnd, True, p.bg, 168 if p.dark else 120)
            else:
                apply_acrylic(hwnd, False)
        except Exception:
            pass
        self._rebuild_backdrop()

    def showEvent(self, e):
        super().showEvent(e)
        QTimer.singleShot(30, self._update_background)

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(rect, 16, 16)
        painter.setClipPath(path)

        if self._bg is not None:
            painter.drawPixmap(0, 0, self._bg)
        else:
            painter.fillPath(path, qc(p.bg))

        # 侧边栏淡色分层
        painter.fillRect(QRectF(0, 0, self.side.width(), self.height()),
                         qc(p.sidebar, 0.05 if p.dark else 0.5))

        painter.setClipping(False)
        painter.setPen(QPen(qc(p.border, 0.11 if p.dark else 0.08), 1))
        painter.drawPath(path)
        painter.end()

    def _rebuild_backdrop(self) -> None:
        try:
            pos = self.mapToGlobal(QPoint(0, 0))
            self._bg = BACKDROP.render(THEME.p, (self.width(), self.height()), pos,
                                      THEME.blur_active)
            self.update()
        except Exception:
            self._bg = None

    def moveEvent(self, e):
        super().moveEvent(e)
        self._bg_timer.start(140)

    # ---------- 窗口行为 ----------
    def _toggle_max(self) -> None:
        if self.isMaximized():
            self.showNormal()
            self.btn_max.set_kind("max")
        else:
            self.showMaximized()
            self.btn_max.set_kind("restore")

    def eventFilter(self, obj, ev):
        t = ev.type()
        if t == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
            gp = ev.globalPosition().toPoint()
            if self.mapFromGlobal(gp).y() < 48:
                self._dragging = True
                self._drag_pos = gp - self.frameGeometry().topLeft()
        elif t == QEvent.MouseMove and self._dragging:
            if not self.isMaximized():
                self.move(ev.globalPosition().toPoint() - self._drag_pos)
        elif t == QEvent.MouseButtonRelease:
            self._dragging = False
        elif t == QEvent.MouseButtonDblClick:
            gp = ev.globalPosition().toPoint()
            if self.mapFromGlobal(gp).y() < 48:
                self._toggle_max()
                return True
        return super().eventFilter(obj, ev)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and e.position().y() < 48:
            self._dragging = True
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._dragging and not self.isMaximized():
            self.move(e.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._dragging = False
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.position().y() < 48:
            self._toggle_max()
        super().mouseDoubleClickEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if hasattr(self, "toast"):
            self.toast.move((self.width() - self.toast.width()) // 2,
                            self.height() - self.toast.height() - 26)
        if hasattr(self, "_bg_timer"):
            self._bg_timer.start(120)

    # ---------- 导航 ----------
    def _on_nav(self, key: str) -> None:
        idx = {"home": 0, "programs": 1, "settings": 2}[key]
        self.stack.setCurrentIndex(idx)
        for nav in self.nav_group:
            nav.set_active(nav.key == key)
        self.page_title.setText({"home": "首页", "programs": "程序", "settings": "设置"}[key])

    # ---------- 托盘 / 定时 ----------
    def _dev_shot(self, path: str) -> None:
        """开发用：把窗口自身渲染成 PNG（不受 DPI 缩放影响）。"""
        try:
            self.showNormal()
            off = os.environ.get("AM_SCROLL", "").strip()
            if off and self.stack.currentIndex() == 2:
                bar = self.page_settings.stack.currentWidget().verticalScrollBar()
                bar.setValue(int(off))
            tab = os.environ.get("AM_TAB", "").strip()
            if tab and self.stack.currentIndex() == 2:
                self.page_settings.switch_tab(int(tab))
            self.repaint()
            QApplication.processEvents()
            self.grab().save(path)
        except Exception:
            pass
        if os.environ.get("AM_SHOT_EXIT") == "1":
            QTimer.singleShot(200, self.quit_app)

    def _init_tray(self) -> None:
        icon_file = resource_path("assets/icon.ico")
        icon = QIcon(icon_file) if os.path.exists(icon_file) else QIcon()
        self.tray = TrayIcon(icon, self)
        self.tray.open_main.connect(self.show_main)
        self.tray.open_settings.connect(lambda: self.show_main("settings"))
        self.tray.check_now.connect(lambda: self.start_scan(silent=True))
        self.tray.silent_update.connect(self.silent_update_all)
        self.tray.cancel_task.connect(self.cancel_tasks)
        self.tray.autostart_toggled.connect(self._set_autostart)
        self.tray.quit_requested.connect(self.quit_app)
        self.tray.refresh_style()
        self.tray.set_autostart(autostart.is_enabled())
        self.tray.set_tooltip(self.engine.records.get("last_scan", ""), 0)
        self.tray.show()

    def _init_scheduler(self) -> None:
        self.sched_timer = QTimer(self)
        self.sched_timer.timeout.connect(self._on_schedule_tick)
        self._apply_schedule()

    def _apply_schedule(self) -> None:
        bg = self.cfg.setdefault("background", {})
        if not hasattr(self, "sched_timer"):
            return
        self.sched_timer.stop()
        if bg.get("check_enabled", True):
            minutes = max(5, int(bg.get("interval_min", 60) or 60))
            self.sched_timer.start(minutes * 60_000)

    def _on_schedule_tick(self) -> None:
        if (self.scan_worker and self.scan_worker.isRunning()) or \
                (self.update_worker and self.update_worker.isRunning()):
            return
        self.start_scan(silent=True)

    def show_main(self, page: str | None = None) -> None:
        self._silent_scan = False
        if page:
            self._on_nav(page)
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _notify(self, title: str, body: str) -> None:
        """按设置里的通知方式分发（系统通知 / 钉钉 / 弹出主界面）。"""
        methods = (self.cfg.get("background") or {}).get("notify_methods") or ["tray"]
        done = False
        if "tray" in methods and self.tray is not None:
            self.tray.notify(title, body)
            done = True
        if "dingtalk" in methods:
            self.engine.send_dingtalk(f"### {title}\n\n{body}")
            done = True
        if "window" in methods:
            self.show_main()
            self.toast.show_text(f"{title}：{body}")
            done = True
        if not done:
            self.tray and self.tray.notify(title, body)

    def _set_autostart(self, on: bool) -> None:
        ok = autostart.set_enabled(bool(on), silent=True)
        self.cfg.setdefault("background", {})["run_at_startup"] = bool(on and ok)
        self._save_cfg()
        if self.tray is not None:
            self.tray.set_autostart(bool(on and ok))
        if hasattr(self, "page_settings"):
            self.page_settings.refresh_background()
        self.toast.show_text("已设置为开机后台启动" if (on and ok) else
                             ("已取消开机启动" if not on else "设置开机启动失败"))

    def quit_app(self) -> None:
        self._quitting = True
        self._save_cfg()
        if self.tray is not None:
            self.tray.hide()
        QApplication.quit()

    # ---------- 数据与扫描 ----------
    def _update_brand(self) -> None:
        proxy = self.engine.proxy or "无代理"
        self.side_status.setText(
            f'<p style="margin:0;line-height:165%">网络：{proxy}</p>'
            f'<p style="margin:4px 0 0 0;line-height:165%">扫描目录 {len(self.cfg.get("roots", []))} 个</p>')
        self._refresh_net_info()

    def _refresh_net_info(self) -> None:
        mode = (self.cfg.get("proxy") or {}).get("mode", "auto")
        tag = {"auto": "自动探测", "manual": "手动指定", "off": "不使用"}.get(mode, "自动探测")
        proxy = self.engine.proxy or "未检测到"
        mirror = self.engine.mirror
        mirror_txt = "检测中…" if mirror is None else (mirror or "无可用镜像")
        self._net_text = f"代理：{proxy}（{tag}）\n镜像：{mirror_txt}"
        if hasattr(self, "page_settings"):
            self.page_settings.set_network_info(self._net_text.replace("\n", "　|　"))

    def start_scan(self, silent: bool = False) -> None:
        if self.scan_worker and self.scan_worker.isRunning():
            return
        self._silent_scan = silent
        self.engine.cancel.clear()
        self.page_home.set_scan_state(True, 0, 0, "正在准备…")
        self.page_home.last_scan_time = self.engine.records.get("last_scan", "—") or "—"
        items = self.engine.collect()
        if not items:
            self.page_home.set_scan_state(False)
            if self.tray is not None:
                self.tray.set_busy(False)
            if not silent:
                self.toast.show_text("没有找到可检查的程序，请先在设置里添加文件夹", ok=False)
            return
        self.items = items
        self.page_home.set_items(items)
        self.progs_list.set_items(items)
        self.page_home.set_scan_state(True, 0, len(items), "正在连接 GitHub…")
        if self.tray is not None:
            self.tray.set_busy(True, "正在检查更新…")
        self._update_brand()

        self.scan_worker = ScanWorker(self.engine, items)
        self.scan_worker.item_ready.connect(self._on_item_scanned)
        self.scan_worker.progress.connect(self._on_scan_progress)
        self.scan_worker.finished_all.connect(self._on_scan_finished)
        self.scan_worker.start()

    def _on_item_scanned(self, item) -> None:
        self.page_home.update_item(item)
        self.progs_list.update_item(item)

    def _on_scan_progress(self, done: int, total: int, name: str) -> None:
        self.page_home.set_scan_state(True, done, total, name)

    def _on_scan_finished(self, items) -> None:
        self.page_home.set_scan_state(False)
        last = self.engine.records.get("last_scan", "—") or "—"
        self.page_home.last_scan_time = last
        self.page_home.refresh_stats()
        upd = [i for i in items
               if i.status == "update" or (i.status == "unknown" and i.scanned)]
        if self.tray is not None:
            self.tray.set_busy(False)
            self.tray.set_tooltip(self.engine.records.get("last_scan", ""), len(upd))
        self._update_brand()
        if not self._silent_scan:
            self.toast.show_text(f"检查完成：{len(upd)} 个程序有新版本" if upd else "检查完成：全部已是最新")
            return
        # ---- 后台检查：按设置决定通知 / 静默更新 ----
        if not upd:
            return
        bg = self.cfg.get("background", {})
        mode = bg.get("auto_mode", "off")
        names = "、".join(i.name for i in upd[:6]) + ("…" if len(upd) > 6 else "")
        if mode in ("silent", "notify"):
            if mode == "notify":
                self._notify(f"发现 {len(upd)} 个新版本",
                             f"正在后台更新：{names}")
            self._start_update(upd, background=True)
        else:
            self._notify(f"发现 {len(upd)} 个程序有新版本",
                         f"{names}\n右键托盘图标可以静默更新，或打开主界面查看详情。")

    def cancel_tasks(self) -> None:
        self.engine.cancel.set()
        self.toast.show_text("已请求停止，等待当前任务结束…")

    def update_all(self) -> None:
        targets = [i for i in self.items
                   if i.status == "update" or (i.status == "unknown" and i.scanned)]
        if not targets:
            self.toast.show_text("没有需要更新的程序")
            return
        self._start_update(targets)

    def silent_update_all(self) -> None:
        """托盘「静默更新」：不弹主界面，直接后台更新。"""
        targets = [i for i in self.items
                   if i.status == "update" or (i.status == "unknown" and i.scanned)]
        if not targets:
            if not self.items:
                self.start_scan(silent=True)
                self.toast.show_text("还没有检查结果，正在后台检查…")
            else:
                self.toast.show_text("没有需要更新的程序")
            return
        self._start_update(targets, background=True)

    def update_one(self, key: str) -> None:
        it = next((i for i in self.items if i.key == key), None)
        if it is None:
            return
        if it.status == "unbound":
            self.bind_repo(key)
            return
        if it.status == "latest":
            self.toast.show_text(f"{it.name} 已是最新版")
            return
        self._start_update([it])

    def _start_update(self, targets, background: bool = False) -> None:
        if self.update_worker and self.update_worker.isRunning():
            self.toast.show_text("已有更新任务在进行中")
            return
        self._background_update = background
        self.engine.cancel.clear()
        if not background:
            self.toast.show_text(f"开始更新 {len(targets)} 个程序…")
        if self.tray is not None:
            self.tray.set_busy(True, f"正在更新 {len(targets)} 个程序…")
        self.update_worker = UpdateWorker(self.engine, targets)
        self.update_worker.item_started.connect(self._on_update_started)
        self.update_worker.item_progress.connect(self._on_update_progress)
        self.update_worker.item_done.connect(self._on_update_done)
        self.update_worker.finished_all.connect(self._on_update_all_done)
        self.update_worker.start()

    def _on_update_started(self, item) -> None:
        self.page_home.update_item(item)
        self.progs_list.update_item(item)

    def _on_update_progress(self, item, progress: float) -> None:
        for i, old in enumerate(self.items):
            if old.key == item.key:
                self.items[i] = item
        self.page_home.list.rows[item.key].refresh() if item.key in self.page_home.list.rows else None

    def _on_update_done(self, item) -> None:
        for i, old in enumerate(self.items):
            if old.key == item.key:
                self.items[i] = item
        self.page_home.update_item(item)
        self.progs_list.update_item(item)

    def _on_update_all_done(self, items) -> None:
        ok = sum(1 for i in items if i.state == "done")
        bad = sum(1 for i in items if i.state == "failed")
        background = getattr(self, "_background_update", False)
        self._background_update = False
        if background:
            if self.tray is not None:
                self.tray.set_busy(False)
        else:
            self.toast.show_text(f"更新结束：成功 {ok} 个" + (f"，失败 {bad} 个" if bad else ""),
                                 ok=bad == 0)
        # 重新统计
        for it in items:
            if it.state == "done":
                it.status = "latest"
        self.page_home.refresh_stats()
        left = sum(1 for i in self.items
                   if i.status == "update" or (i.status == "unknown" and i.scanned))
        if self.tray is not None:
            self.tray.set_busy(False)
            self.tray.set_tooltip(self.engine.records.get("last_scan", ""), left)
        # 后台模式：静默更新时不打扰；「更新后提示」才发通知
        mode = self.cfg.get("background", {}).get("auto_mode", "off")
        if background:
            if mode == "notify":
                body = f"成功 {ok} 个" + (f"，失败 {bad} 个" if bad else "")
                detail = "\n".join(f"- {i.name}：{i.detail}" for i in items[:6])
                self._notify("后台更新完成", f"{body}\n{detail}")
            return
        self.engine.notify("🔄 Automate 更新完成",
                           f"### Automate 更新完成\n\n- 成功 **{ok}** 个\n- 失败 **{bad}** 个\n\n" +
                           "\n".join(f"- {i.name}：{i.detail}" for i in items[:20]))

    # ---------- 其它交互 ----------
    def open_item_folder(self, key: str) -> None:
        it = next((i for i in self.items if i.key == key), None)
        if it:
            core.open_in_explorer(it.path)

    def open_item_repo(self, key: str) -> None:
        """右键菜单：在浏览器里打开该程序对应的 GitHub 仓库。"""
        it = next((i for i in self.items if i.key == key), None)
        if not it:
            return
        if not it.repo:
            self.toast.show_text(f"{it.name} 还没有绑定 GitHub 仓库", ok=False)
            return
        core.open_url(f"https://github.com/{it.repo}")
        self.toast.show_text(f"已在浏览器打开 {it.repo}")

    def mark_version(self, key: str) -> None:
        """右键「手动标记本地版本」：给读不到版本号的目录补一个版本，参与正常比对。"""
        it = next((i for i in self.items if i.key == key), None)
        if not it:
            return
        if it.kind == "git":
            self.toast.show_text("Git 仓库按提交号比对，不需要手动标记版本", ok=False)
            return
        cur = self.engine.manual_version(it.name) or it.local
        dlg = VersionDialog(self, it.name, cur, it.remote)
        if not dlg.exec():
            return
        ver = dlg.value()
        self.engine.set_manual_version(it.name, ver)
        if ver:
            self.toast.show_text(f"已把「{it.name}」标记为 {ver}，重新检查…")
        else:
            self.toast.show_text(f"已清除「{it.name}」的版本标记，重新检查…")
        QTimer.singleShot(200, self.start_scan)

    def bind_repo(self, key: str) -> None:
        it = next((i for i in self.items if i.key == key), None)
        if not it:
            return
        dlg = BindDialog(self, it.name, it.repo)
        if not dlg.exec():
            return
        repo = dlg.value()
        if not repo or "/" not in repo:
            self.toast.show_text("仓库格式应为 owner/repo", ok=False)
            return
        programs = self.cfg.setdefault("programs", [])
        entry = {"name": it.name, "match": it.name, "repo": repo,
                 "anchor": it.anchor or core.guess_anchor(it.path),
                 "version": it.version_spec or core.guess_version_spec(it.path)}
        for i, p in enumerate(programs):
            if (p.get("match") or "").lower() == it.name.lower():
                programs[i] = {**p, **entry}
                break
        else:
            programs.append(entry)
        self._save_cfg()
        self.toast.show_text(f"已绑定 {repo}，重新检查中…")
        QTimer.singleShot(200, self.start_scan)

    def _on_sort_changed(self, key: str) -> None:
        self.cfg.setdefault("ui", {})["sort"] = key
        for lv in (self.page_home.list, self.progs_list):
            if lv.sort_key != key:
                lv.set_sort(key)
        self._save_cfg()

    def _on_prior_changed(self, key: str) -> None:
        self.cfg.setdefault("ui", {})["prior"] = key
        for lv in (self.page_home.list, self.progs_list):
            if lv.prior != key:
                lv.set_prior(key)
        self._save_cfg()

    def _on_roots_changed(self) -> None:
        self._save_cfg()
        self.toast.show_text("检查文件夹已更新，重新扫描…")
        QTimer.singleShot(200, self.start_scan)

    def exclude_item(self, key: str) -> None:
        """右键「排除此文件夹」：写入所在根目录的排除列表，并从当前列表移除。"""
        it = next((i for i in self.items if i.key == key), None)
        if it is None:
            return
        ok, info = self.engine.exclude_path(it.path)
        if not ok:
            self.toast.show_text(info, ok=False)
            return
        self.items = [i for i in self.items if i.key != it.key]
        self.page_home.set_items(self.items)
        self.progs_list.set_items(self.items)
        self._save_cfg()
        if hasattr(self, "page_settings"):
            self.page_settings.reload_roots()
        self.toast.show_text(f"已排除「{info}」，不再检查它")
        if self.tray is not None:
            self.tray.set_tooltip(self.engine.records.get("last_scan", ""),
                                  sum(1 for i in self.items
                                      if i.status == "update"
                                      or (i.status == "unknown" and i.scanned)))

    def _save_cfg(self) -> None:
        self.cfg.setdefault("ui", {})["preset"] = THEME.preset
        g = self.normalGeometry()
        self.cfg["ui"]["geometry"] = [g.x(), g.y(), g.width(), g.height()]
        self.cfg["ui"]["theme"] = THEME.mode
        self.cfg["ui"]["blur"] = THEME.blur
        core.save_config(self.cfg)
        self._apply_schedule()

    def closeEvent(self, e):
        bg = self.cfg.get("background", {})
        if (not self._quitting and bg.get("close_to_tray", True)
                and self.tray is not None and self.tray.isVisible()):
            e.ignore()
            self.hide()
            if not self._tray_hint_shown:
                self._tray_hint_shown = True
                self.tray.notify("已最小化到托盘",
                                 "程序仍在后台运行，会按设置定时检查更新。\n右键托盘图标可静默更新或退出。",
                                 5000)
            return
        self._save_cfg()
        if self.scan_worker and self.scan_worker.isRunning():
            self.engine.cancel.set()
            self.scan_worker.wait(2000)
        if self.tray is not None:
            self.tray.hide()
        super().closeEvent(e)
        if not self._quitting:
            self._quitting = True
            QApplication.quit()


# ---------------------------------------------------------------- 入口

def resource_path(name: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def main() -> int:
    args = [a.lower() for a in sys.argv[1:]]
    start_hidden = any(a in ("--tray", "--silent", "--background", "-t") for a in args)

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName(core.APP_NAME)
    app.setApplicationDisplayName(core.APP_NAME)
    # 关窗后驻留托盘，由托盘菜单决定何时退出
    app.setQuitOnLastWindowClosed(False)
    f = QFont("Microsoft YaHei UI")
    f.setPixelSize(13)
    app.setFont(f)

    # Windows 通知需要显式的 AppUserModelID，否则托盘消息不一定弹出
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DADiDiDiDA.AutomateUpdater")
    except Exception:
        pass

    icon_file = resource_path("assets/icon.ico")
    if os.path.exists(icon_file):
        app.setWindowIcon(QIcon(icon_file))

    # ---------- 单实例：已有进程就把它的窗口叫到前面，然后本进程退出 ----------
    ipc_key = core.APP_ID + ".single"
    probe = QLocalSocket()
    probe.connectToServer(ipc_key)
    if probe.waitForConnected(400):
        if not start_hidden:                      # 后台启动时不要抢焦点
            probe.write(b"show")
            probe.flush()
            probe.waitForBytesWritten(400)
        probe.disconnectFromServer()
        return 0

    QLocalServer.removeServer(ipc_key)            # 清理上次异常退出留下的名字
    server = QLocalServer()
    server.listen(ipc_key)

    win = MainWindow(start_hidden=start_hidden)

    def _on_second_instance() -> None:
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            conn.readyRead.connect(lambda c=conn: c.readAll())
            conn.disconnected.connect(conn.deleteLater)
        win.show_main()

    server.newConnection.connect(_on_second_instance)

    if not start_hidden:
        win.show()
    return app.exec()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        try:
            log_path = os.path.join(core.config_dir(), "crash.log")
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
        raise
