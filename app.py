# -*- coding: utf-8 -*-
"""主窗口与应用入口。"""

from __future__ import annotations

import ctypes
import html
import json
import math
import os
import sys
import time
import winreg

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (QColor, QFont, QGuiApplication, QIcon, QLinearGradient, QPainter,
                           QPainterPath, QPen, QPixmap)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QLineEdit, QPushButton,
                               QStackedWidget, QVBoxLayout, QWidget)

import core
import autostart
from backdrop import BACKDROP
from pages import (HomePage, ItemListView, MirrorDialog, PreserveDialog, SettingsPage,
                   ThemedDialog, VersionDialog, _label)
from theme import GLYPH, THEME, build_qss, glyph_font, qc, tint
from tray import TrayIcon
from widgets import NavButton, Toast

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
            # 扫描前先落地镜像测速（以前 ensure_mirror 只在 git fetch 里被调到，
            # 设置页的「chosen」形同虚设，界面一直显示「检测中…」）
            try:
                self.engine.ensure_mirror()
            except Exception:
                pass
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
        last_emit = [0.0]

        def on_prog(item, progress, msg=None):
            # 阶段/线路提示（解压中、下载中 · 当前线路）要立刻显示；
            # 纯进度刷新限流，避免每个 256KB 数据块都把两个列表重绘一遍
            now = time.monotonic()
            if msg or progress >= 1.0 or now - last_emit[0] >= 0.12:
                last_emit[0] = now
                self.item_progress.emit(item, progress)

        try:
            for it in self.items:
                if self.engine.cancel.is_set():
                    it.detail = "已取消"
                    break
                self.item_started.emit(it)
                res = self.engine.update_item(it, on_progress=on_prog)
                self.item_done.emit(res)
        finally:
            self.finished_all.emit(self.items)


class UndoWorker(QThread):
    """撤销上次更新：把最近一次备份还原回程序目录。"""

    done = Signal(object)

    def __init__(self, engine: core.Engine, item):
        super().__init__()
        self.engine = engine
        self.item = item

    def run(self):
        try:
            res = self.engine.undo_update(
                self.item, on_progress=lambda i, p, m=None: None)
        except Exception as e:
            self.item.state = "failed"
            self.item.status = "error"
            self.item.detail = f"撤销异常：{type(e).__name__}: {e}"
            res = self.item
        finally:
            self.done.emit(res)




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


class NodesSyncWorker(QThread):
    """后台抓取社区加速节点，别卡住界面。"""

    done = Signal(list, str)

    def __init__(self, engine: core.Engine, force: bool = True):
        super().__init__()
        self.engine = engine
        self.force = force

    def run(self):
        try:
            nodes, err, _ = self.engine.sync_nodes(force=self.force)
            self.done.emit(nodes, err)
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
        self.setFixedSize(38, 28)
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
        self.tray: TrayIcon | None = None
        self._silent_scan = start_hidden
        self._last_upd_sig = None   # 上次弹过通知的待更新名单，避免重复弹窗
        self._pending_updates: list = []   # 更新任务运行中点「更新」的待更新队列
        self._undo_worker = None
        self._background_update = start_hidden
        self._quitting = False
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
        # 最小尺寸按屏幕可用区自适应：高分屏 150% 缩放时逻辑分辨率可能只有 910×490，
        # 写死 1120×690 会让窗口超出屏幕、按钮点不到
        screen = QGuiApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else None
        min_w = min(1120, max(760, int(avail.width() * 0.92))) if avail else 1120
        min_h = min(690, max(520, int(avail.height() * 0.88))) if avail else 690
        self.setMinimumSize(min_w, min_h)
        geo = (self.cfg.get("ui") or {}).get("geometry") or []
        if len(geo) == 4:
            x, y, w, h = (int(v) for v in geo)
            if avail:
                # 拔掉副屏后旧坐标可能整个落在屏幕外，先夹回当前屏幕
                w = max(min_w, min(w, avail.width()))
                h = max(min_h, min(h, avail.height()))
                x = max(avail.left(), min(x, avail.right() - w + 1))
                y = max(avail.top(), min(y, avail.bottom() - h + 1))
            self.setGeometry(x, y, w, h)
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
        f2.setPixelSize(11)
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
        fs.setPixelSize(11)
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
        self.header.setFixedHeight(46)
        self.header.setMouseTracking(True)
        hl = QHBoxLayout(self.header)
        hl.setContentsMargins(22, 6, 6, 0)      # 右侧只留 6px，按钮贴住右上角
        hl.setSpacing(2)
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
            hl.addWidget(b, 0, Qt.AlignTop)
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
        self.page_home.row_undo.connect(self.undo_update)
        self.page_home.row_preserve.connect(self.edit_preserve)

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
        self.progs_list.row_undo.connect(self.undo_update)
        self.progs_list.row_preserve.connect(self.edit_preserve)
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
        page.mirror_manage_requested.connect(self._manage_mirrors)
        page.nodes_sync_requested.connect(lambda: self._sync_nodes(force=True))
        page.proxy_changed.connect(self._on_proxy_changed)
        page.mirrors_changed.connect(self._on_mirrors_changed)

    def _on_mirrors_changed(self) -> None:
        # 镜像总开关/每日测速开关变了：清掉缓存镜像，下次检查按新设置重新走
        self.engine.mirror = None
        self._refresh_net_info()

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

    def _sync_nodes(self, force: bool = True) -> None:
        """立即同步社区加速节点（设置页按钮 / 镜像对话框）。"""
        if getattr(self, "_nodes_worker", None) and self._nodes_worker.isRunning():
            return
        self._nodes_worker = NodesSyncWorker(self.engine, force)
        self._nodes_worker.done.connect(self._on_nodes_synced)
        self._nodes_worker.start()

    def _on_nodes_synced(self, nodes, error) -> None:
        self._save_cfg()
        self.engine.mirror = None        # 候选变了，下次检查重新测速
        if hasattr(self, "page_settings"):
            self.page_settings.set_nodes_info()
        dlg = getattr(self, "_mirror_dlg", None)
        if dlg is not None:
            try:
                dlg.set_nodes_info()
            except RuntimeError:
                pass
        if error:
            self.toast.show_text(f"节点同步失败：{error[:70]}", ok=False)
        else:
            self.toast.show_text(f"已同步 {len(nodes)} 个社区加速节点，下次测速生效")

    def _manage_mirrors(self) -> None:
        """设置页「镜像地址 · 管理」：按测速结果排序展示，可增删 / 同步节点。"""
        mcfg = self.cfg.setdefault("mirrors", {})
        dlg = MirrorDialog(self, [x for x in (mcfg.get("list") or [])
                                  if isinstance(x, str)], mcfg.get("chosen") or "",
                           ranking=mcfg.get("ranking") or [], cfg=self.cfg)
        dlg.sync_requested.connect(lambda: self._sync_nodes(force=True))
        self._mirror_dlg = dlg
        if not dlg.exec():
            self._mirror_dlg = None
            return
        self._mirror_dlg = None
        before = len(mcfg.get("list") or [])
        left = core.set_mirrors(self.cfg, dlg.values())
        mcfg["ranking_sig"] = ""             # 候选变了，下次检查重新测速
        self.engine.mirror = None
        self._save_cfg()
        if hasattr(self, "page_settings"):
            self.page_settings.refresh_mirror_row()
        self._refresh_net_info()
        added = len(left) - before
        tail = "；下次检查 / 测速时重新挑最快的" if added or len(left) != before else ""
        self.toast.show_text(f"镜像列表已保存（{len(left)} 个）{tail}")

    def _on_proxy_changed(self) -> None:
        """设置页改了代理方式 / 地址后立刻生效。"""
        proxy = self.engine.refresh_proxy()
        self._update_brand()
        if proxy:
            self.toast.show_text(f"代理已切换为 {core.mask_url(proxy)}")
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
        # 三态循环，能回到「跟随系统」；并立即落盘
        nxt = {"auto": "light", "light": "dark", "dark": "auto"}.get(THEME.mode, "auto")
        THEME.set_mode(nxt)
        self.cfg.setdefault("ui", {})["theme"] = nxt
        core.save_config(self.cfg)
        self._apply_theme()

    def _apply_theme(self) -> None:
        p = THEME.p
        # 必须挂在 qApp 上：QToolTip 是原生弹层，挂在主窗口上它读不到，会退化成系统黑框
        QApplication.instance().setStyleSheet(build_qss(p))
        self.theme_btn.setText("主题：" + {"auto": "跟随系统", "light": "浅色",
                                      "dark": "深色"}.get(THEME.mode, "跟随系统"))
        self.theme_btn.setIcon(mode_icon(not THEME.is_dark))

        tint(self.brand_name, p.text)
        tint(self.brand_ver, p.text_muted)
        tint(self.page_title, p.text)
        tint(self.progs_header, p.text)
        tint(self.side_status, p.text_muted)
        for b in (self.btn_min, self.btn_max, self.btn_close):
            b.update()

        # 图标与自绘控件
        for nav in self.nav_group:
            nav.update()
        self._update_logo()
        self.page_home.refresh_theme()
        self.progs_list.refresh_theme()
        # 只刷配色，不重建整页（重建会丢掉正在输入但未失焦的代理/Webhook）
        if hasattr(self, "page_settings"):
            self.page_settings.refresh_theme()
        self._update_background()
        self.update()


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
        # 与 assets/icon.ico 同款蓝紫渐变（make_icon.py 采样值）
        g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        g.setColorAt(0, QColor(108, 139, 254))
        g.setColorAt(1, QColor(149, 110, 240))
        painter.fillPath(path, g)
        f = glyph_font(15)
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
        self.tray.check_now.connect(self._tray_check_now)
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

    def _shutdown(self) -> None:
        """统一退出入口：取消并等待所有后台线程（扫描/更新/测速/撤销）后再退出。

        托盘「退出」以前完全绕过 closeEvent，更新器可能正在 rmtree/copy2，
        直接 quit 会留下半成品程序目录甚至崩溃。
        """
        if getattr(self, "_shutting_down", False):
            return
        self._shutting_down = True
        self._quitting = True
        self.engine.cancel.set()
        try:
            # 撤销不可取消，必须等它真正结束；其余线程请求取消后等待，超时也不静默放行
            for name in ("scan_worker", "update_worker",
                         "_probe_worker", "_undo_worker"):
                w = getattr(self, name, None)
                if w is not None and hasattr(w, "isRunning") and w.isRunning():
                    if not w.wait(8000):
                        # 仍在线程里写文件就退出会留下半残目录；继续延长等待
                        w.wait(30000)
            # 设置页的钉钉测试线程也纳入统一等待
            ps = getattr(self, "page_settings", None)
            dw = getattr(ps, "_ding_test_worker", None) if ps is not None else None
            if dw is not None and hasattr(dw, "isRunning") and dw.isRunning():
                dw.wait(12000)
        finally:
            # 保存失败不能阻断退出（否则下次退出因 _shutting_down 直接 return，再也退不掉）
            try:
                self._save_cfg()
            except Exception:
                pass
            if self.tray is not None:
                self.tray.hide()
            QApplication.quit()

    def quit_app(self) -> None:
        self._shutdown()

    # ---------- 数据与扫描 ----------
    def _update_brand(self) -> None:
        proxy = core.mask_url(self.engine.proxy) or "未检测到本地代理"
        self.side_status.setText(
            f'<p style="margin:0;line-height:160%">网络：{html.escape(proxy)}</p>'
            f'<p style="margin:4px 0 0 0;line-height:160%">扫描目录 {len(self.cfg.get("roots", []))} 个</p>')
        self._refresh_net_info()

    def _refresh_net_info(self) -> None:
        mode = (self.cfg.get("proxy") or {}).get("mode", "auto")
        tag = {"auto": "自动探测", "manual": "手动指定", "off": "不使用"}.get(mode, "自动探测")
        proxy = core.mask_url(self.engine.proxy) or "未检测到"
        mirror = self.engine.mirror
        mirror_txt = "检测中…" if mirror is None else (mirror or "无可用镜像")
        self._net_text = f"代理：{proxy}（{tag}）\n镜像：{mirror_txt}"
        if hasattr(self, "page_settings"):
            self.page_settings.set_network_info(self._net_text.replace("\n", "　|　"))

    def _tray_check_now(self) -> None:
        """托盘「立即检查」是用户主动动作，窗口隐藏时也要给一点反馈。"""
        self.start_scan(silent=False)
        if not self.isVisible() and self.tray is not None:
            self.tray.notify("正在检查", "正在检查程序更新，完成后托盘角标会更新")

    def start_scan(self, silent: bool = False) -> None:
        if self.scan_worker and self.scan_worker.isRunning():
            return
        if self.update_worker and self.update_worker.isRunning():
            # 扫描与更新共用 items，并发会互相覆盖状态
            if not silent:
                self.toast.show_text("正在更新程序，等更新结束后再检查", ok=False)
            return
        if self._undo_worker and self._undo_worker.isRunning():
            if not silent:
                self.toast.show_text("正在撤销更新，等结束后再检查", ok=False)
            return
        self._silent_scan = silent
        self._scan_silent = silent        # 本次任务快照，打开窗口不会改变它的语义
        self.engine.cancel.clear()
        self.page_home.set_scan_state(True, 0, 0, "正在准备…")
        self.page_home.last_scan_time = self.engine.records.get("last_scan", "—") or "—"
        items = self.engine.collect()
        if not items:
            # 空结果也要清空旧列表/统计：删除全部根目录后，旧程序不能继续显示、更不能更新
            self.items = []
            self.page_home.set_items([])
            self.progs_list.set_items([])
            self.page_home.refresh_stats()
            self.page_home.set_scan_state(False)
            if self.tray is not None:
                self.tray.set_busy(False)
                self.tray.set_tooltip(self.engine.records.get("last_scan", ""), 0)
            if not silent:
                self.toast.show_text("没有找到可检查的程序，请先在设置里添加文件夹", ok=False)
            return
        self.items = items
        # 重启会自动触发一次检查：列表第一帧就要显示「检查中」，不能等 worker 启动
        for it in items:
            it.scanning = True
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
        # 取消 / 异常时没有收到 item_ready 的行不会自己复位，统一清掉「检查中」并刷新
        for it in self.items:
            it.scanning = False
        for it in self.items:
            self.page_home.update_item(it)
            self.progs_list.update_item(it)
        last = self.engine.records.get("last_scan", "—") or "—"
        self.page_home.last_scan_time = last
        self.page_home.refresh_stats()
        upd = [i for i in items
               if i.status == "update" or (i.status == "unknown" and i.scanned)]
        errs = [i for i in items if i.status == "error"]
        cancelled = self.engine.cancel.is_set()
        if self.tray is not None:
            self.tray.set_busy(False)
            self.tray.set_tooltip(self.engine.records.get("last_scan", ""), len(upd))
        self._update_brand()
        if not getattr(self, "_scan_silent", False):
            if cancelled:
                self.toast.show_text("已停止检查，结果不完整", ok=False)
            elif errs and not upd:
                self.toast.show_text(f"检查完成：{len(errs)} 个程序未能确认", ok=False)
            elif upd:
                self.toast.show_text(f"检查完成：{len(upd)} 个程序有新版本")
            else:
                self.toast.show_text("检查完成：全部已是最新")
            return
        # ---- 后台检查：按设置决定通知 / 静默更新 ----
        if not upd:
            return
        bg = self.cfg.get("background", {})
        mode = bg.get("auto_mode", "off")
        names = "、".join(i.name for i in upd[:6]) + ("…" if len(upd) > 6 else "")
        # 去重签名必须纳入「策略」：off 只通知过一次，之后改成自动更新时，相同版本名单
        # 若被提前去重，自动更新永远不会执行。分开「通知去重」和「自动更新去重」。
        sig = tuple(sorted((i.name, i.remote or "") for i in upd))
        if mode == "off":
            # 用户选了「只通知不自动更新」：必须真的通知，否则这个选项名不副实
            if sig != self._last_upd_sig:
                self._last_upd_sig = sig
                self._notify(f"发现 {len(upd)} 个程序有新版本",
                             f"{names}\n右键托盘图标可以静默更新，或打开主界面查看详情。")
            return
        # 自动更新模式（silent / notify）：按「是否已处理过」去重，处理失败则允许下次重试
        if sig == getattr(self, "_last_auto_sig", None):
            return
        if mode == "notify":
            self._notify(f"发现 {len(upd)} 个新版本",
                         f"正在后台更新：{names}")
        self._last_auto_sig = sig
        self._start_update(upd, background=True)

    def cancel_tasks(self) -> None:
        self.engine.cancel.set()
        self.toast.show_text("已请求停止，等待当前任务结束…")

    def update_all(self) -> None:
        targets = [i for i in self.items
                   if i.status == "update" or (i.status == "unknown" and i.scanned)]
        if not targets:
            self.toast.show_text("没有需要更新的程序")
            return
        self._start_update(targets, from_user=True)

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
        self._start_update(targets, background=True, from_user=True)

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
        self._start_update([it], from_user=True)

    def _start_update(self, targets, background: bool = False,
                      from_user: bool = False) -> None:
        if from_user and self.scan_worker and self.scan_worker.isRunning():
            # 用户手动点更新时别和扫描抢同一批 items；自动更新走另一条路径不受影响
            self.toast.show_text("正在检查新版本，等检查结束后再更新", ok=False)
            return
        if self._undo_worker and self._undo_worker.isRunning():
            self.toast.show_text("正在撤销更新，等结束后再更新", ok=False)
            return
        if self.update_worker and self.update_worker.isRunning():
            # 已有任务在跑：不丢弃请求，加入待更新队列，本轮结束后自动继续
            run = {t.key for t in targets}
            run |= {t.key for t in getattr(self, "_running_targets", [])}
            queued = {t.key for t in self._pending_updates}
            fresh = [t for t in targets
                     if t.key not in queued and t.key not in run]
            if fresh:
                self._pending_updates.extend(fresh)
                self.toast.show_text(
                    f"更新任务进行中，已把 {len(fresh)} 个程序加入队列")
            else:
                self.toast.show_text("所选程序已在更新队列里")
            return
        self._launch_update(targets, background)

    def _launch_update(self, targets, background: bool = False,
                       keep_cancel: bool = False) -> None:
        self._running_targets = list(targets)
        self._background_update = background
        # 队列续跑不是用户新发起的任务，不能把「停止」请求清掉
        if not keep_cancel:
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
        # 等待文件释放时两个页面都要能看到最新提示，不只是首页
        # page_home 是页面（内部才是 ItemListView），progs_list 本身就是列表
        for view in (self.page_home.list, self.progs_list):
            row = view.rows.get(item.key)
            if row is not None:
                row.refresh()

    def _on_update_done(self, item) -> None:
        for i, old in enumerate(self.items):
            if old.key == item.key:
                self.items[i] = item
        self.page_home.update_item(item)
        self.progs_list.update_item(item)

    def _on_update_all_done(self, items) -> None:
        ok = sum(1 for i in items if i.state == "done")
        bad = sum(1 for i in items if i.state == "failed")
        # 有失败项时清掉自动更新去重标记，下轮扫描会重试，避免「失败一次就再也不更新」
        if bad:
            self._last_auto_sig = None
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
        # 队列：攒齐所有批次再统一发一次通知（以前每批弹一条，还会重跑时清掉「停止」）
        self._running_targets = []
        self._queue_results = (getattr(self, "_queue_results", None) or []) + list(items)
        # 直接由 finished_all 续跑下一批，不用 singleShot 延时：
        # 延时窗口内用户再点「全部更新」会启动第二个 worker，两个线程同时写同一目录
        if self._pending_updates:
            nxt = self._pending_updates[:]
            self._pending_updates = []
            self._launch_update(nxt, background, keep_cancel=True)
            return
        all_items = self._queue_results
        self._queue_results = []
        ok = sum(1 for i in all_items if i.state == "done")
        bad = sum(1 for i in all_items if i.state == "failed")
        detail = "\n".join(f"- {i.name}：{i.detail}" for i in all_items[:20])
        body = f"成功 {ok} 个" + (f"，失败 {bad} 个" if bad else "") + "\n" + detail
        # 手动更新和后台更新统一走 _notify，遵守设置里的通知方式
        mode = self.cfg.get("background", {}).get("auto_mode", "off")
        if background:
            if mode == "notify":
                self._notify("后台更新完成", body)
            return
        self._notify("Automate 更新完成", body)

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

    def undo_update(self, key: str) -> None:
        """右键「撤销上次更新」：把最近一次备份还原回程序目录。"""
        it = next((i for i in self.items if i.key == key), None)
        if not it:
            return
        if it.kind == "git":
            self.toast.show_text("Git 仓库按提交拉取，没有备份可还原", ok=False)
            return
        if it.state == "running":
            self.toast.show_text(f"{it.name} 正在更新，等结束后再撤销", ok=False)
            return
        if (self.update_worker and self.update_worker.isRunning()) or self._pending_updates:
            self.toast.show_text("有更新任务进行中，等结束后再撤销", ok=False)
            return
        if self._undo_worker and self._undo_worker.isRunning():
            self.toast.show_text("已有一个撤销任务在进行中", ok=False)
            return
        self.toast.show_text(f"正在把 {it.name} 还原到更新前的备份…")
        it.state = "running"            # 立即在 GUI 线程置 running，禁用相关操作
        self.page_home.update_item(it)
        self.progs_list.update_item(it)
        self._undo_worker = UndoWorker(self.engine, it)
        self._undo_worker.done.connect(self._on_undo_done)
        self._undo_worker.start()

    def _on_undo_done(self, it) -> None:
        ok = it.state != "failed"
        self.toast.show_text(f"{it.name}：{it.detail}", ok=ok)
        for i, old in enumerate(self.items):
            if old.key == it.key:
                self.items[i] = it
        self.page_home.update_item(it)
        self.progs_list.update_item(it)
        self.page_home.refresh_stats()

    def edit_preserve(self, key: str) -> None:
        """右键「更新时保留的文件夹」：按程序单独指定覆盖安装时保留的目录。"""
        it = next((i for i in self.items if i.key == key), None)
        if not it:
            return
        if it.kind == "git":
            self.toast.show_text("Git 仓库按提交拉取更新，不涉及文件覆盖", ok=False)
            return
        custom = (self.cfg.get("preserve_map") or {}).get(it.name.lower())
        dlg = PreserveDialog(self, it.name, it.path, list(it.preserve),
                             bool(custom and custom.get("custom")))
        if not dlg.exec():
            return
        self.engine.set_preserve_map(it.name, dlg.chosen)
        it.preserve = self.engine.preserve_for(it.name, {})
        for view in (self.page_home.list, self.progs_list):
            row = view.rows.get(it.key)
            if row is not None:
                row.refresh()
        if dlg.chosen is None:
            self.toast.show_text(f"「{it.name}」已改为跟随全局保留列表")
        else:
            self.toast.show_text(f"「{it.name}」将保留 {len(dlg.chosen)} 个文件夹")

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
            # 静默驻留托盘，不弹任何提示（托盘图标本身已说明程序在后台）
            e.ignore()
            self.hide()
            return
        self._shutdown()
        super().closeEvent(e)


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

    # Windows 通知需要显式的 AppUserModelID；同时在注册表里登记显示名，
    # 否则通知横幅标题直接显示 AUMID 字符串（之前是硬编码的 GitHub 用户名）
    try:
        aumid = f"{core.APP_ID}.Tray"
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(aumid)
        key = winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{aumid}",
            0, winreg.KEY_SET_VALUE | winreg.KEY_WOW64_64KEY)
        winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, core.APP_NAME)
        ico = resource_path("assets/icon.ico")
        if os.path.exists(ico):
            winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, os.path.abspath(ico))
        winreg.CloseKey(key)
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
        # 明确告诉常驻实例这次是「叫出窗口」还是「后台静默启动」，
        # 否则 --tray 二次启动反而把主窗口弹到前台
        try:
            probe.write(json.dumps({"show": not start_hidden,
                                    "hidden": start_hidden}).encode("utf-8"))
            probe.flush()
            probe.waitForBytesWritten(400)
        except Exception:
            pass
        probe.disconnectFromServer()
        return 0

    QLocalServer.removeServer(ipc_key)            # 清理上次异常退出留下的名字
    server = QLocalServer()
    if not server.listen(ipc_key):
        # 监听失败（另一个实例抢先 / 名字被占）：绝不能再建第二套完整实例
        print(f"无法建立单实例服务：{server.errorString()}", file=sys.stderr)
        return 0

    win = MainWindow(start_hidden=start_hidden)

    def _on_second_instance() -> None:
        while server.hasPendingConnections():
            conn = server.nextPendingConnection()
            state = {"buf": b"", "done": False}

            def _read(c=conn, st=state):
                st["buf"] += bytes(c.readAll())
                if st["done"]:
                    return
                try:
                    payload = json.loads(st["buf"].decode("utf-8"))
                except Exception:
                    return                  # 分包没收齐
                st["done"] = True
                if isinstance(payload, dict) and (payload.get("show") or not payload.get("hidden")):
                    win.show_main()

            def _try_parse(st):
                # 数据可能在 disconnected 之前就全部到达，但 readyRead 没再触发；
                # 断开前先尝试解析剩余缓冲，否则 JSON 负载会丢
                if st["done"]:
                    return
                try:
                    payload = json.loads(st["buf"].decode("utf-8"))
                except Exception:
                    return
                st["done"] = True
                if isinstance(payload, dict) and (payload.get("show") or not payload.get("hidden")):
                    win.show_main()

            def _on_disconnect(c=conn, st=state):
                _try_parse(st)
                # 兼容旧版只发 b"show" 的客户端
                if not st["done"] and st["buf"].strip() == b"show":
                    win.show_main()
                c.deleteLater()

            conn.readyRead.connect(_read)
            conn.disconnected.connect(_on_disconnect)
            # 连接后立即主动读一次：数据可能先于 readyRead 到达
            _read(conn, state)

    server.newConnection.connect(_on_second_instance)

    if not start_hidden:
        win.show()
    return app.exec()


_EXC_LOG = {"sig": "", "at": 0.0, "repeat": 0}


def _excepthook(exc_type, exc, tb):
    """Qt 事件循环里的异常不会冒到 main()，必须挂 excepthook 才能留下日志。

    同一个位置的异常 60 秒内只记一次，重复次数合并写入；否则槽函数里一个
    每数据块都抛的错，能在几分钟内把 crash.log 刷到几百 MB。
    """
    import traceback
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    frames = traceback.extract_tb(tb)
    last_frame = frames[-1] if frames else None
    sig = (f"{exc_type.__name__}:{exc}|"
           f"{getattr(last_frame, 'filename', '')}:{getattr(last_frame, 'lineno', '')}")
    now = time.time()
    if sig == _EXC_LOG["sig"] and now - _EXC_LOG["at"] < 60:
        _EXC_LOG["repeat"] += 1
        sys.__excepthook__(exc_type, exc, tb)
        return
    try:
        log_path = os.path.join(core.config_dir(), "crash.log")
        with open(log_path, "a", encoding="utf-8") as f:
            if _EXC_LOG["repeat"]:
                f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n"
                        f"[上一条同类异常在 60 秒内又出现 {_EXC_LOG['repeat']} 次，已合并]\n")
            f.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} =====\n{text}")
    except Exception:
        pass
    _EXC_LOG.update(sig=sig, at=now, repeat=0)
    sys.__excepthook__(exc_type, exc, tb)


if __name__ == "__main__":
    sys.excepthook = _excepthook
    try:
        sys.exit(main())
    except Exception:
        import traceback
        try:
            log_path = os.path.join(core.config_dir(), "crash.log")
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            pass
        raise
