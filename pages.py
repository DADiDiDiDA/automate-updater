# -*- coding: utf-8 -*-
"""页面：首页概览、程序列表、设置。"""

from __future__ import annotations

import os

from PySide6.QtCore import QPoint, QRectF, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QDialog, QFileDialog,
                               QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect,
                               QGridLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QPushButton, QScrollArea, QSizePolicy,
                               QStackedWidget, QVBoxLayout, QWidget)

from backdrop import BACKDROP, paint_glass
from theme import (GLYPH, PRESET_HINTS, PRESET_LABELS, PRESET_ORDER, THEME, flat, glyph_font,
                   qc, rgba, scoped, tint)
from widgets import (Card, Chip, ExpandSetting, Glyph, NumberStepper, ProgramRow, ProgressLine,
                     SearchBox, SegmentedControl, SettingGroup, SettingRow, StatTile, TabButton,
                     ThemedCombo, ToggleSwitch, WrapLabel)


class _DingTestWorker(QThread):
    """「发送测试」放到后台线程，避免 urlopen 卡住整个界面。"""
    done = Signal(bool, str)

    def __init__(self, cfg: dict, text: str):
        super().__init__()
        self.cfg, self.text = cfg, text

    def run(self):
        try:
            ok = core.send_dingtalk_raw(self.cfg, self.text)
            self.done.emit(bool(ok), "" if ok else "检查 Webhook 与加签密钥")
        except Exception as e:
            self.done.emit(False, str(e))

import autostart
import core


def _label(text: str, size: int = 13, color_key: str = "text", bold: bool = False,
           wrap: bool = False) -> QLabel:
    p = THEME.p
    # 需要换行的用 WrapLabel：布局会按实际宽度把高度撑开，避免折行处被裁掉
    lbl = WrapLabel(text) if wrap else QLabel(text)
    f = QFont("Microsoft YaHei UI")
    f.setPixelSize(size)
    if bold:
        f.setWeight(QFont.DemiBold)
    lbl.setFont(f)
    if not wrap:
        lbl.setWordWrap(False)
    tint(lbl, getattr(p, color_key))
    return lbl


class SecretEdit(QWidget):
    """密码式输入框 + 「显示/隐藏」切换按钮。

    Webhook 的 access_token 与加签密钥都是敏感凭证，默认打码；点「显示」临时明文。
    控件对外暴露 ``edit``（内部 QLineEdit），并转发 editingFinished / text / setText。
    """

    def __init__(self, text: str = "", placeholder: str = "", width: int = 330, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.edit = QLineEdit(text)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setEchoMode(QLineEdit.Password)
        self.edit.setMinimumWidth(width)
        lay.addWidget(self.edit, 1)
        self._btn = QPushButton("显示")
        self._btn.setObjectName("link")
        self._btn.setCursor(Qt.PointingHandCursor)
        self._btn.setFixedHeight(28)
        self._btn.clicked.connect(self._toggle)
        lay.addWidget(self._btn, 0, Qt.AlignVCenter)

    def _toggle(self) -> None:
        show = self.edit.echoMode() == QLineEdit.Password
        self.edit.setEchoMode(QLineEdit.Normal if show else QLineEdit.Password)
        self._btn.setText("隐藏" if show else "显示")

    # 转发常用接口，调用方无需直接访问 .edit
    @property
    def editingFinished(self):
        return self.edit.editingFinished

    def text(self) -> str:
        return self.edit.text()

    def setText(self, text: str) -> None:
        self.edit.setText(text)


class ItemListView(QWidget):
    """可筛选的程序列表。"""

    row_update = Signal(str)
    row_open = Signal(str)
    row_bind = Signal(str)
    row_exclude = Signal(str)
    row_repo = Signal(str)
    row_mark = Signal(str)
    row_undo = Signal(str)
    row_preserve = Signal(str)
    sort_changed = Signal(str)
    prior_changed = Signal(str)

    FILTERS = [("all", "全部"), ("update", "有更新"), ("git", "Git"), ("release", "Release"),
               ("problem", "失败")]

    # 基础排序（决定整体顺序）
    BASES = [("name", "名称 A→Z"), ("name_desc", "名称 Z→A"),
             ("size_desc", "体积 大→小"), ("size_asc", "体积 小→大"),
             ("recent", "最近发布"), ("old", "最早发布"),
             ("updated_desc", "最近更新"), ("updated_asc", "最早更新")]
    # 特殊规则（在基础顺序上把一类项提到最前）
    PRIORS = [("update", "有更新最先"), ("none", "不额外排序"),
              ("git", "Git 优先"), ("release", "Release 优先")]

    @staticmethod
    def is_update(it) -> bool:
        return (it.state == "running" or it.status == "update"
                or (it.status == "unknown" and it.scanned))

    def __init__(self, parent=None, show_problem: bool = True):
        super().__init__(parent)
        self.items = []
        self.rows: dict[str, ProgramRow] = {}
        self.filter = "all"
        self.sort_key = "name"
        self.prior = "update"
        self.keyword = ""
        self.filters = [f for f in self.FILTERS if show_problem or f[0] != "problem"]

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.chips: dict[str, Chip] = {}
        for key, text in self.filters:
            chip = Chip(text, 0)
            chip.setCursor(Qt.PointingHandCursor)
            chip.clicked.connect(lambda _=False, k=key: self.set_filter(k))
            self.chips[key] = chip
            bar.addWidget(chip)
        bar.addStretch(1)
        self.search = SearchBox()
        self.search.setFixedWidth(184)
        self.search.textChanged.connect(self._on_search)
        bar.addWidget(self.search)
        self.base_combo = ThemedCombo(self.BASES, self.sort_key, 122)
        self.base_combo.setToolTip("基础排序：名称 / 体积 / 发布时间 / 上次更新时间")
        self.base_combo.changed.connect(self._on_sort)
        bar.addWidget(self.base_combo)

        self.prior_combo = ThemedCombo(self.PRIORS, self.prior, 128)
        self.prior_combo.setToolTip("特殊规则：把某一类程序提到列表最前")
        self.prior_combo.changed.connect(self._on_prior)
        bar.addWidget(self.prior_combo)
        outer.addLayout(bar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.list_lay = QVBoxLayout(holder)
        self.list_lay.setContentsMargins(0, 0, 6, 4)
        self.list_lay.setSpacing(8)
        self.list_lay.addStretch(1)
        self.scroll.setWidget(holder)
        outer.addWidget(self.scroll, 1)

        self.empty = QLabel("还没有检查结果，点右上角「重新检查」开始")
        self.empty.setAlignment(Qt.AlignCenter)
        tint(self.empty, THEME.p.text_muted)
        self.empty.setVisible(False)
        outer.addWidget(self.empty)

        self.set_filter("all")

    # ---------- 数据 ----------
    def set_items(self, items) -> None:
        self.items = list(items)
        for row in self.rows.values():
            # 只 hide + 延后销毁，保持父子关系：可见控件一旦脱离父级会立刻
            # 变成一个独立顶层小窗，每次重建列表都闪一批
            row.hide()
            self.list_lay.removeWidget(row)
            row.deleteLater()
        self.rows.clear()
        order = self.sorted_items()
        for it in order:
            row = ProgramRow(it)
            row.update_requested.connect(self.row_update)
            row.open_requested.connect(self.row_open)
            row.bind_requested.connect(self.row_bind)
            row.exclude_requested.connect(self.row_exclude)
            row.repo_requested.connect(self.row_repo)
            row.mark_requested.connect(self.row_mark)
            row.undo_requested.connect(self.row_undo)
            row.preserve_requested.connect(self.row_preserve)
            self.rows[it.key] = row
            self.list_lay.insertWidget(self.list_lay.count() - 1, row)
        self._update_counts()
        self.apply_filter()

    def sorted_items(self) -> list:
        """两级排序：先按基础规则排，再用特殊规则把某一类提到最前（稳定排序）。"""
        k = self.sort_key
        if k == "name_desc":
            items = sorted(self.items, key=lambda i: i.name.lower(), reverse=True)
        elif k == "size_desc":
            items = sorted(self.items, key=lambda i: (-(i.local_size or 0), i.name.lower()))
        elif k == "size_asc":
            items = sorted(self.items, key=lambda i: (i.local_size or 0, i.name.lower()))
        elif k == "recent":
            items = sorted(self.items, key=lambda i: (i.published or "", i.name.lower()),
                           reverse=True)
        elif k == "old":
            items = sorted(self.items, key=lambda i: (i.published or "9999-99-99",
                                                      i.name.lower()))
        elif k == "updated_desc":
            # 没记录更新时间的排在最后（"0" 最小，反向后落在尾部）
            items = sorted(self.items, key=lambda i: (i.updated_at or "0", i.name.lower()),
                           reverse=True)
        elif k == "updated_asc":
            items = sorted(self.items, key=lambda i: (i.updated_at or "9999", i.name.lower()))
        else:
            items = sorted(self.items, key=lambda i: i.name.lower())
        pri = self.prior
        if pri == "update":
            items = sorted(items, key=lambda i: 0 if self.is_update(i) else 1)
        elif pri == "git":
            items = sorted(items, key=lambda i: 0 if i.kind == "git" else 1)
        elif pri == "release":
            items = sorted(items, key=lambda i: 0 if i.kind == "release" else 1)
        return items

    def _on_sort(self, key: str = "") -> None:
        self.sort_key = key or "name"
        self._rebuild_order()
        self.sort_changed.emit(self.sort_key)

    def _on_prior(self, key: str = "") -> None:
        self.prior = key or "update"
        self._rebuild_order()
        self.prior_changed.emit(self.prior)

    def set_sort(self, key: str) -> None:
        self.sort_key = key or "name"
        self.base_combo.set_value(self.sort_key)
        self._rebuild_order()

    def set_prior(self, key: str) -> None:
        self.prior = key or "update"
        self.prior_combo.set_value(self.prior)
        self._rebuild_order()

    def _rebuild_order(self) -> None:
        """只调整行顺序，不重建控件（扫描过程中调用也很轻）。"""
        order = [it.key for it in self.sorted_items() if it.key in self.rows]
        cur = [self.list_lay.itemAt(i).widget().item.key
               for i in range(self.list_lay.count() - 1)]
        if order == cur:
            return
        for idx, key in enumerate(order):
            w = self.rows.get(key)
            if w is None:
                continue
            self.list_lay.removeWidget(w)
            self.list_lay.insertWidget(idx, w)
        self.apply_filter()

    def update_item(self, item) -> None:
        for i, old in enumerate(self.items):
            if old.key == item.key:
                self.items[i] = item
                break
        row = self.rows.get(item.key)
        if row:
            row.set_item(item)
        self._update_counts()
        self._rebuild_order()      # 扫描过程中状态变化后立刻重排，否则「有更新最先」不生效
        self.apply_filter()

    def _update_counts(self) -> None:
        counts = {
            "all": len(self.items),
            "update": sum(1 for i in self.items
                          if i.status == "update" or (i.status == "unknown" and i.scanned)),
            "git": sum(1 for i in self.items if i.kind == "git"),
            "release": sum(1 for i in self.items if i.kind == "release"),
            "problem": sum(1 for i in self.items if i.status in ("error", "skip", "unbound")),
        }
        for k, chip in self.chips.items():
            chip.set_count(counts.get(k, 0))

    def _on_search(self, text: str) -> None:
        self.keyword = text.strip().lower()
        self.apply_filter()

    def set_filter(self, key: str) -> None:
        self.filter = key
        for k, chip in self.chips.items():
            chip.set_active(k == key)
        self.apply_filter()

    def apply_filter(self) -> None:
        visible = 0
        for it in self.items:
            row = self.rows.get(it.key)
            if not row:
                continue
            ok = True
            if self.filter == "update":
                ok = it.status in ("update", "unknown")
            elif self.filter == "git":
                ok = it.kind == "git"
            elif self.filter == "release":
                ok = it.kind == "release"
            elif self.filter == "problem":
                ok = it.status in ("error", "skip", "unbound")
            if ok and self.keyword:
                ok = self.keyword in it.name.lower() or self.keyword in (it.repo or "").lower()
            row.setVisible(ok)
            visible += 1 if ok else 0
        self.empty.setVisible(visible == 0 and bool(self.items))
        if visible == 0 and self.items:
            self.empty.setText("没有符合条件的程序")
        self.scroll.setVisible(visible > 0 or not self.items)

    def refresh_theme(self) -> None:
        for row in self.rows.values():
            row.refresh()
        for chip in self.chips.values():
            chip.set_active(chip is self.chips.get(self.filter))
            chip._apply()
        self.search.refresh_theme()
        self.base_combo._restyle()
        self.prior_combo._restyle()
        tint(self.empty, THEME.p.text_muted)


class HomePage(QWidget):
    scan_requested = Signal()
    cancel_requested = Signal()
    update_all = Signal()
    row_update = Signal(str)
    row_open = Signal(str)
    row_bind = Signal(str)
    row_exclude = Signal(str)
    row_repo = Signal(str)
    row_mark = Signal(str)
    row_undo = Signal(str)
    row_preserve = Signal(str)
    sort_changed = Signal(str)
    prior_changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        p = THEME.p
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)

        # ---------- 顶部总览 ----------
        self.hero = Card(padding=18)
        h = QVBoxLayout()
        h.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(10)
        left = QVBoxLayout()
        left.setSpacing(3)
        self.title = _label("准备就绪", 20, "text", bold=True)
        self.subtitle = _label("点「重新检查」扫描所有程序的最新版本", 12, "text_muted")
        left.addWidget(self.title)
        left.addWidget(self.subtitle)
        top.addLayout(left, 1)

        self.scan_btn = QPushButton("重新检查")
        self.scan_btn.setObjectName("ghost")
        self.scan_btn.setCursor(Qt.PointingHandCursor)
        self.scan_btn.clicked.connect(self._on_scan_clicked)

        self.update_btn = QPushButton("全部更新")
        self.update_btn.setObjectName("primary")
        self.update_btn.setCursor(Qt.PointingHandCursor)
        self.update_btn.clicked.connect(self.update_all.emit)
        self.update_btn.setEnabled(False)

        top.addWidget(self.scan_btn)
        top.addWidget(self.update_btn)
        h.addLayout(top)

        self.progress = ProgressLine()
        self.progress.setVisible(False)
        h.addWidget(self.progress)

        self.scan_hint = _label("", 11, "text_muted")
        self.scan_hint.setVisible(False)
        h.addWidget(self.scan_hint)

        stats = QHBoxLayout()
        stats.setSpacing(10)
        self.tile_update = StatTile("—", "可更新", "accent")
        self.tile_latest = StatTile("—", "已是最新", "success")
        self.tile_problem = StatTile("—", "需要处理", "warning")
        self.tile_time = StatTile("—", "上次检查", "text_dim")
        for t in (self.tile_update, self.tile_latest, self.tile_problem, self.tile_time):
            t.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            stats.addWidget(t)
        h.addLayout(stats)
        self.hero.lay.addLayout(h)
        lay.addWidget(self.hero)

        # ---------- 列表 ----------
        self.list = ItemListView()
        self.list.row_update.connect(self.row_update)
        self.list.row_open.connect(self.row_open)
        self.list.row_bind.connect(self.row_bind)
        self.list.row_exclude.connect(self.row_exclude)
        self.list.row_repo.connect(self.row_repo)
        self.list.row_mark.connect(self.row_mark)
        self.list.row_undo.connect(self.row_undo)
        self.list.row_preserve.connect(self.row_preserve)
        self.list.sort_changed.connect(self.sort_changed)
        self.list.prior_changed.connect(self.prior_changed)
        lay.addWidget(self.list, 1)

        self._running = False

    # ---------- 交互 ----------
    def _on_scan_clicked(self) -> None:
        if self._running:
            self.cancel_requested.emit()
        else:
            self.scan_requested.emit()

    def set_scan_state(self, running: bool, done: int = 0, total: int = 0, current: str = "") -> None:
        self._running = running
        self.progress.setVisible(running)
        self.scan_hint.setVisible(running)
        self.scan_btn.setText("停止检查" if running else "重新检查")
        self.scan_btn.setObjectName("danger" if running else "ghost")
        self.scan_btn.style().unpolish(self.scan_btn)
        self.scan_btn.style().polish(self.scan_btn)
        if running:
            self.progress.set_indeterminate(total == 0)
            if total:
                self.progress.set_value(done / max(1, total))
            self.scan_hint.setText(f"正在检查 {done}/{total}　{current}")
            self.title.setText("正在检查更新…")
            self.subtitle.setText("正在读取各程序的最新版本信息")
        else:
            self.progress.set_indeterminate(False)
            self.scan_hint.setVisible(False)

    def set_items(self, items) -> None:
        self.list.set_items(items)
        self.refresh_stats()

    def set_sort(self, key: str) -> None:
        self.list.set_sort(key)

    def set_prior(self, key: str) -> None:
        self.list.set_prior(key)

    def update_item(self, item) -> None:
        self.list.update_item(item)
        self.refresh_stats()

    def refresh_stats(self) -> None:
        items = self.list.items
        upd = sum(1 for i in items
                  if i.status == "update" or (i.status == "unknown" and i.scanned))
        latest = sum(1 for i in items if i.status == "latest")
        prob = sum(1 for i in items if i.status in ("error", "skip", "unbound"))
        self.tile_update.set_values(str(upd))
        self.tile_latest.set_values(str(latest))
        self.tile_problem.set_values(str(prob))
        self.tile_time.set_values(self.last_scan_time or "—")
        self.update_btn.setText(f"全部更新（{upd}）" if upd else "全部更新")
        self.update_btn.setEnabled(upd > 0 and not self._running)
        if not self._running and items:
            if upd:
                self.title.setText(f"{upd} 个程序有新版本")
                self.subtitle.setText("展开「日志」可查看每个程序的更新内容")
            else:
                self.title.setText("全部已是最新")
                self.subtitle.setText(f"共检查 {len(items)} 个程序")

    last_scan_time = ""

    def refresh_theme(self) -> None:
        self.list.refresh_theme()
        tint(self.title, THEME.p.text)
        tint(self.subtitle, THEME.p.text_muted)
        tint(self.scan_hint, THEME.p.text_muted)
        for t in (self.tile_update, self.tile_latest, self.tile_problem, self.tile_time):
            t.refresh()
        self.hero.update()


class _DialogPanel(QFrame):
    """对话框的圆角底板（玻璃质感，跟随主题）。"""

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = max(10, min(p.radius_card, 20))       # 跟随皮肤圆角
        path = paint_glass(painter, rect, radius, self.mapToGlobal(QPoint(0, 0)), p,
                           THEME.blur_active, 0.18 if p.dark else 0.24)
        painter.setPen(QPen(qc(p.border, 0.16), 1))
        painter.drawPath(path)
        painter.end()


class ThemedDialog(QDialog):
    """无边框 + 跟随皮肤 + 可拖动的对话框，替代系统白底弹窗。"""

    def __init__(self, parent=None, title: str = "", width: int = 470):
        super().__init__(parent)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setModal(True)
        self.setMinimumWidth(width)
        self._drag_pos = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)      # 给投影留白
        self.panel = _DialogPanel(self)
        outer.addWidget(self.panel)
        shadow = QGraphicsDropShadowEffect(self.panel)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 110))
        self.panel.setGraphicsEffect(shadow)

        self.body = QVBoxLayout(self.panel)
        self.body.setContentsMargins(20, 16, 20, 18)
        self.body.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.title_lbl = _label(title, 14, "text", bold=True)
        head.addWidget(self.title_lbl, 1)
        close = QPushButton()
        close.setFixedSize(24, 24)
        close.setCursor(Qt.PointingHandCursor)
        close.setText(GLYPH["close"])
        f = glyph_font(10)
        close.setFont(f)
        tp = THEME.p
        close.setStyleSheet(
            f"QPushButton{{background:transparent;border:none;border-radius:6px;"
            f"color:{tp.text_muted};}}"
            f"QPushButton:hover{{background:{rgba(tp.danger, 0.85)};color:#FFFFFF;}}")
        close.clicked.connect(self.reject)
        head.addWidget(close)
        self.body.addLayout(head)

    def exec(self) -> int:
        # 弹出前截屏：对话框底板的毛玻璃要模糊「背后的真实窗口」
        BACKDROP.snapshot(self.screen())
        return super().exec()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        super().mouseReleaseEvent(e)

    def add_widget(self, w) -> None:
        self.body.addWidget(w)

    # ---------- 通用底部按钮 ----------
    def add_footer(self, extra: QPushButton | None = None, ok_text: str = "保存") -> QPushButton:
        """底部操作行：左侧可选的附加按钮 + 右侧「取消 / 保存」。"""
        row = QHBoxLayout()
        row.setSpacing(8)
        if extra is not None:
            extra.setCursor(Qt.PointingHandCursor)
            extra.setFixedHeight(32)
            row.addWidget(extra)
        row.addStretch(1)
        cancel = QPushButton("取消")
        cancel.setObjectName("ghost")
        cancel.setCursor(Qt.PointingHandCursor)
        cancel.setFixedHeight(32)
        cancel.clicked.connect(self.reject)
        ok = QPushButton(ok_text)
        ok.setObjectName("primary")
        ok.setCursor(Qt.PointingHandCursor)
        ok.setFixedHeight(32)
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        self.body.addLayout(row)
        return ok


class ExcludeDialog(ThemedDialog):
    """编辑某个检查文件夹的排除项。"""

    def __init__(self, parent, path: str, excludes: list[str]):
        super().__init__(parent, "排除子文件夹", 470)
        self.path = path
        self.body.addWidget(_label(f"扫描 {path} 时跳过这些文件夹（每行一个，不区分大小写）",
                                   11, "text_muted", wrap=True))
        tp = THEME.p
        self.edit = QPlainTextEdit("\n".join(excludes))
        self.edit.setPlaceholderText("My-maawork\nCache\nnode_modules")
        self.edit.setFixedHeight(158)
        self.edit.setStyleSheet(
            f"QPlainTextEdit{{background:{rgba(tp.text, 0.05 if tp.dark else 0.04)};"
            f"border:1px solid {rgba(tp.border, 0.14)};border-radius:10px;color:{tp.text};"
            f"font-family:'Consolas','Microsoft YaHei UI';font-size:12px;padding:8px 10px;}}"
            f"QPlainTextEdit:focus{{border:1px solid {rgba(tp.accent, 0.55)};}}")
        self.body.addWidget(self.edit)
        self.add_footer()

    def values(self) -> list[str]:
        return [l.strip() for l in self.edit.toPlainText().splitlines() if l.strip()]


class MirrorDialog(ThemedDialog):
    """管理 GitHub 镜像 / 代理地址：可以删内置的，也可以手动加自己的。"""

    def __init__(self, parent, mirrors: list[str], chosen: str = ""):
        super().__init__(parent, "GitHub 镜像地址", 580)
        self.items = [m for m in (mirrors or []) if m]
        self.chosen = chosen or ""
        self._rows: list[QWidget] = []

        self.body.addWidget(_label(
            "下载时按「镜像地址 + 原始 GitHub 地址」转发；测速后自动挑最快的。"
            "填域名或完整地址都行，例如 gh-proxy.com 或 https://gh-proxy.com/ 。"
            "自建反代、公司内网代理都可以加进来。", 11, "text_muted", wrap=True))

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setFixedHeight(204)
        self.scroll.viewport().setStyleSheet(
            "QScrollArea > QWidget { background: transparent; }")
        holder = QWidget()
        flat(holder)
        self.rows_lay = QVBoxLayout(holder)
        self.rows_lay.setContentsMargins(0, 0, 4, 0)
        self.rows_lay.setSpacing(6)
        self.scroll.setWidget(holder)
        self.body.addWidget(self.scroll)

        add_line = QHBoxLayout()
        add_line.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("https://你的代理地址/   或   gh-proxy.com")
        self.edit.setMinimumHeight(34)
        self.edit.returnPressed.connect(self._add)
        self.add_btn = QPushButton("添加")
        self.add_btn.setObjectName("soft")
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.setFixedHeight(34)
        self.add_btn.clicked.connect(self._add)
        add_line.addWidget(self.edit, 1)
        add_line.addWidget(self.add_btn)
        self.body.addLayout(add_line)

        self.hint = _label("", 11, "text_muted")
        self.hint.setVisible(False)
        self.body.addWidget(self.hint)

        restore = QPushButton("恢复内置")
        restore.setObjectName("ghost")
        restore.setCursor(Qt.PointingHandCursor)
        restore.setFixedHeight(32)
        restore.setToolTip("把列表恢复成内置的公共镜像")
        restore.clicked.connect(self._restore)
        self.add_footer(restore)
        self._rebuild()

    # ---------- 列表 ----------
    def _clear(self) -> None:
        while self.rows_lay.count():
            item = self.rows_lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.deleteLater()
        self._rows.clear()

    def _tag(self, text: str, color: str) -> QLabel:
        b = QLabel(text)
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(10)
        f.setWeight(QFont.DemiBold)
        b.setFont(f)
        scoped(b, f"background: {rgba(color, 0.16)}; color: {color};"
                  f"border-radius: 8px; padding: 2px 8px;")
        return b

    def _rebuild(self) -> None:
        self._clear()
        tp = THEME.p
        if not self.items:
            self.rows_lay.addWidget(_label("列表是空的，在下面填一个地址试试", 12, "text_muted"))
        for url in self.items:
            row = QFrame()
            row.setFixedHeight(38)
            scoped(row, f"background: {rgba(tp.text, 0.05)}; border: none;"
                        f"border-radius: 10px;")
            h = QHBoxLayout(row)
            h.setContentsMargins(12, 0, 8, 0)
            h.setSpacing(8)
            lbl = _label(url, 12, "text")
            lbl.setToolTip(url)
            h.addWidget(lbl, 1)
            if url.rstrip("/") in {x.rstrip("/") for x in core.DEFAULT_MIRRORS}:
                h.addWidget(self._tag("内置", tp.text_dim))
            if self.chosen and self.chosen.rstrip("/") == url.rstrip("/"):
                h.addWidget(self._tag("当前使用", tp.accent))
            b = QPushButton("删除")
            b.setObjectName("ghost")
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(26)
            b.clicked.connect(lambda _=False, u=url: self._remove(u))
            h.addWidget(b)
            self.rows_lay.addWidget(row)
            self._rows.append(row)
        self.rows_lay.addStretch(1)

    def _remove(self, url: str) -> None:
        self.items = [x for x in self.items if x.rstrip("/") != url.rstrip("/")]
        if self.chosen.rstrip("/") == url.rstrip("/"):
            self.chosen = ""
        self._note(f"已从列表删掉 {url}，点保存生效")
        self._rebuild()

    def _add(self) -> None:
        u = core.normalize_mirror(self.edit.text())
        if not u:
            self._note("这个地址用不了：检查一下域名；官方 github.com 本身不用加", bad=True)
            return
        if u.rstrip("/") in {x.rstrip("/") for x in self.items}:
            self._note("列表里已经有这个地址了")
            self.edit.clear()
            return
        self.items.append(u)
        self.edit.clear()
        self._note(f"已添加 {u}，保存后会一起参与测速")
        self._rebuild()
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _restore(self) -> None:
        self.items = list(core.DEFAULT_MIRRORS)
        self.chosen = ""
        self._note("已恢复成内置镜像列表，点保存生效")
        self._rebuild()

    def _note(self, text: str, bad: bool = False) -> None:
        self.hint.setText(text)
        self.hint.setVisible(True)
        tint(self.hint, THEME.p.danger if bad else THEME.p.text_muted)

    def values(self) -> list[str]:
        return list(self.items)


class VersionDialog(ThemedDialog):
    """手动标记本地版本：程序目录里读不到版本号时用它兜底比对。"""

    def __init__(self, parent, name: str, current: str = "", remote: str = ""):
        super().__init__(parent, "手动标记本地版本", 460)
        self.cleared = False
        self.body.addWidget(_label(f"「{name}」当前安装的是哪个版本？", 13, "text", bold=True))
        self.body.addWidget(_label("填好后会和仓库最新版比对，并作为可更新候选参与排序与批量更新。",
                                   11, "text_muted", wrap=True))
        if remote:
            self.body.addWidget(_label(f"仓库最新版本：{remote}", 11, "text_dim"))
        self.edit = QLineEdit(current)
        self.edit.setPlaceholderText("例如 1.2.3 或 v2.0.0")
        self.edit.setMinimumHeight(32)
        self.edit.returnPressed.connect(self.accept)
        self.body.addWidget(self.edit)
        extra = None
        if current:
            extra = QPushButton("清除标记")
            extra.setObjectName("danger")
            extra.clicked.connect(self._clear)
        self.add_footer(extra)

    def _clear(self) -> None:
        self.cleared = True
        self.accept()

    def value(self) -> str:
        return "" if self.cleared else self.edit.text().strip()


class PreserveDialog(ThemedDialog):
    """单个程序：更新时保留哪些文件夹（覆盖安装才不会冲掉存档/配置）。"""

    COLS = 4

    def __init__(self, parent, name: str, path: str, current: list[str], custom: bool):
        super().__init__(parent, "更新时保留的文件夹", 540)
        self.chosen: list[str] | None = None      # None 表示跟随全局设置
        self._follow = False

        self.body.addWidget(_label(f"「{name}」", 13, "text", bold=True))
        self.body.addWidget(_label(
            "勾选的文件夹在更新时原样保留（不删除、也不被新版覆盖）；"
            "未勾选的按设定的安装方式处理。", 11, "text_muted", wrap=True))

        names = self._candidates(path, current)
        self.chips: dict[str, Chip] = {}
        box = QWidget()
        flat(box)
        grid = QGridLayout(box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(7)
        grid.setVerticalSpacing(7)
        for i, nm in enumerate(names):
            c = Chip(nm)
            c.set_active(any(nm.lower() == x.lower() for x in current))
            c.clicked.connect(lambda _=False, cc=c: cc.set_active(not cc.is_active()))
            self.chips[nm] = c
            grid.addWidget(c, i // self.COLS, i % self.COLS, Qt.AlignLeft)
        grid.setColumnStretch(self.COLS, 1)

        if names:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            flat(scroll)
            flat(scroll.viewport())
            scroll.setWidget(box)
            scroll.setFixedHeight(min(230, 39 * ((len(names) + self.COLS - 1) // self.COLS) + 6))
            self.body.addWidget(scroll)
        else:
            self.body.addWidget(_label("这个程序目录里还没有子文件夹。", 11, "text_muted"))

        self.extra = QLineEdit("")
        self.extra.setPlaceholderText("还可以补充文件夹名，用逗号分隔（如 mods, plugins）")
        self.extra.setMinimumHeight(32)
        self.body.addWidget(self.extra)

        self.hint = _label("" if custom else "当前沿用「设置 → 更新策略」里的全局保留列表。",
                           11, "text_muted", wrap=True)
        self.body.addWidget(self.hint)
        self.hint.setVisible(not custom)     # 挂进布局后再显示，避免变成顶层小窗

        extra_btn = QPushButton("跟随全局")
        extra_btn.setObjectName("ghost")
        extra_btn.setToolTip("清除本程序的单独设置，改回设置页里的全局保留列表")
        extra_btn.clicked.connect(self._follow_global)
        self.add_footer(extra_btn)

    # ---------- 数据 ----------
    @staticmethod
    def _candidates(path: str, current: list[str]) -> list[str]:
        found = []
        try:
            for nm in os.listdir(path):
                full = os.path.join(path, nm)
                if not os.path.isdir(full) or nm.startswith("."):
                    continue
                if nm.lower() in ("cache", "_backup", "_updater", "__pycache__"):
                    continue
                found.append(nm)
        except Exception:
            pass
        seen = {x.lower() for x in found}
        for nm in current:                     # 配置里有、但目录里已经不在的也保留，方便取消勾选
            if nm.lower() not in seen:
                found.append(nm)
                seen.add(nm.lower())
        return sorted(found, key=lambda s: (s.lower(), s))

    def values(self) -> list[str]:
        out = [nm for nm, c in self.chips.items() if c.is_active()]
        for raw in self.extra.text().replace("，", ",").split(","):
            raw = raw.strip()
            if raw and all(raw.lower() != v.lower() for v in out):
                out.append(raw)
        return out

    def _follow_global(self) -> None:
        self._follow = True
        self.accept()

    def accept(self) -> None:
        if not self._follow:
            self.chosen = self.values()
        super().accept()


class RootRow(QWidget):
    changed = Signal()
    removed = Signal(str)

    def __init__(self, cfg_root: dict, parent=None):
        super().__init__(parent)
        self.root = cfg_root
        self.setFixedHeight(56)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 10, 8)
        lay.setSpacing(10)

        self.icon = Glyph("folder", 16, THEME.p.accent)
        lay.addWidget(self.icon)

        col = QVBoxLayout()
        col.setSpacing(2)
        self.path_lbl = QLabel(cfg_root.get("path", ""))
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(13)
        self.path_lbl.setFont(f)
        tint(self.path_lbl, THEME.p.text)
        self.sub_lbl = QLabel()
        f2 = QFont("Microsoft YaHei UI")
        f2.setPixelSize(11)
        self.sub_lbl.setFont(f2)
        tint(self.sub_lbl, THEME.p.text_muted)
        col.addWidget(self.path_lbl)
        col.addWidget(self.sub_lbl)
        lay.addLayout(col, 1)

        self.toggle = ToggleSwitch(bool(cfg_root.get("enabled", True)))
        self.toggle.toggled.connect(self._on_toggle)
        lay.addWidget(self.toggle)

        self.ex_btn = QPushButton("排除…")
        self.ex_btn.setObjectName("ghost")
        self.ex_btn.setFixedHeight(28)
        self.ex_btn.setCursor(Qt.PointingHandCursor)
        self.ex_btn.clicked.connect(self._edit_excludes)
        lay.addWidget(self.ex_btn)

        self.del_btn = QPushButton("移除")
        self.del_btn.setObjectName("danger")
        self.del_btn.setFixedHeight(28)
        self.del_btn.setCursor(Qt.PointingHandCursor)
        self.del_btn.clicked.connect(lambda: self.removed.emit(self.root.get("path", "")))
        lay.addWidget(self.del_btn)

        self._refresh_sub()

    def _refresh_sub(self) -> None:
        ex = self.root.get("exclude", [])
        depth = self.root.get("depth", 4)
        self.sub_lbl.setText(f"排除 {len(ex)} 项 · 扫描深度 {depth}" if ex else f"未设置排除 · 扫描深度 {depth}")

    def _on_toggle(self, on: bool) -> None:
        self.root["enabled"] = on
        self.changed.emit()

    def _edit_excludes(self) -> None:
        dlg = ExcludeDialog(self.window(), self.root.get("path", ""), self.root.get("exclude", []))
        if dlg.exec():
            self.root["exclude"] = dlg.values()
            self._refresh_sub()
            self.changed.emit()

    def paintEvent(self, e):
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QPainter, QPainterPath, QPen
        from theme import qc
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path.addRoundedRect(rect, max(9, p.radius_btn), max(9, p.radius_btn))
        painter.fillPath(path, qc(p.text, 0.045))
        painter.setPen(QPen(qc(p.border, p.border_alpha * 0.9), 1))
        painter.drawPath(path)
        painter.end()


class SettingsPage(QWidget):
    """设置页：顶部分页 + 「分组标题 / 设置行」列表。"""

    PROXY_DESC = "自动探测（7890 / 1080 / 10809…）或手动指定，也可以完全不用"

    theme_changed = Signal()
    roots_changed = Signal()
    config_changed = Signal()
    mirrors_changed = Signal()
    mirror_probe_requested = Signal()
    mirror_manage_requested = Signal()
    autostart_requested = Signal(bool)
    proxy_changed = Signal()

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self._seg_controls: list = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        bar = QHBoxLayout()
        bar.setContentsMargins(2, 0, 0, 0)
        bar.setSpacing(20)
        self.tab_general = TabButton("一般设置")
        self.tab_about = TabButton("关于")
        self.tab_general.clicked.connect(lambda: self.switch_tab(0))
        self.tab_about.clicked.connect(lambda: self.switch_tab(1))
        bar.addWidget(self.tab_general)
        bar.addWidget(self.tab_about)
        bar.addStretch(1)
        outer.addLayout(bar)

        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        self.page_general = self._scroll_page()
        self.page_about = self._scroll_page()
        self.stack.addWidget(self.page_general)
        self.stack.addWidget(self.page_about)

        g = self.page_general.lay
        self._build_appearance(g)
        self._build_background(g)
        self._build_strategy(g)
        self._build_roots(g)
        self._build_network(g)
        g.addStretch(1)

        a = self.page_about.lay
        self._build_about(a)
        a.addStretch(1)

        try:
            idx = int((self.cfg.get("ui") or {}).get("settings_tab", 0))
        except Exception:
            idx = 0
        self.switch_tab(idx if idx in (0, 1) else 0)

    # ---------- 结构 ----------
    def _scroll_page(self) -> QScrollArea:
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        lay = QVBoxLayout(holder)
        lay.setContentsMargins(0, 4, 10, 22)
        lay.setSpacing(8)
        sc.setWidget(holder)
        sc.lay = lay
        return sc

    def switch_tab(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        self.tab_general.set_checked(idx == 0)
        self.tab_about.set_checked(idx == 1)
        self.cfg.setdefault("ui", {})["settings_tab"] = idx

    def _group(self, lay: QVBoxLayout, text: str) -> None:
        lay.addWidget(SettingGroup(text))

    def _row(self, lay: QVBoxLayout, icon: str, title: str, desc: str = "",
             control=None, content=None) -> SettingRow:
        row = SettingRow(icon, title, desc, control, content)
        lay.addWidget(row)
        return row

    def _sub_line(self, title: str, desc: str, control=None) -> QWidget:
        """展开卡片内部的一行：左标题说明 + 右侧控件。"""
        box = QWidget()
        flat(box)
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(_label(title, 12, "text_dim"))
        if desc:
            col.addWidget(_label(desc, 11, "text_muted", wrap=True))
        lay.addLayout(col, 1)
        if control is not None:
            lay.addWidget(control, 0, Qt.AlignVCenter)
        return box

    def _segmented(self, items, current: str, on_pick) -> SegmentedControl:
        ctl = SegmentedControl(items, current)
        ctl.changed.connect(on_pick)
        self._seg_controls.append(ctl)
        return ctl

    def _style_segmented(self) -> None:
        for ctl in self._seg_controls:
            ctl.restyle()

    # ---------- 外观 ----------
    def _build_appearance(self, lay: QVBoxLayout) -> None:
        self._group(lay, "用户界面")
        skin = self._segmented([(k, PRESET_LABELS[k]) for k in PRESET_ORDER],
                               THEME.preset, self._on_preset)
        for key in PRESET_ORDER:
            skin.set_tooltip(key, PRESET_HINTS[key])
        self._row(lay, "settings", "皮肤样式", "亚克力玻璃 / 莫奈 Material You / Miuix 澎湃 OS", skin)

        mode = self._segmented([("auto", "跟随系统"), ("light", "浅色"), ("dark", "深色")],
                               THEME.mode, self._on_mode)
        self._row(lay, "bright", "明暗模式", "默认跟随系统配色，也可以手动锁定", mode)

        self.blur_toggle = ToggleSwitch(bool(self.cfg.get("ui", {}).get("blur", True)))
        self.blur_toggle.toggled.connect(self._on_blur)
        self.blur_row = self._row(lay, "funnel", "毛玻璃模糊",
                                  "关闭后背景不再模糊，低配机器更流畅", self.blur_toggle)
        self._sync_blur_control()
        self._style_segmented()

    def _sync_blur_control(self) -> None:
        """Miuix 之类不使用毛玻璃的皮肤，把开关禁用并说明，别让用户白点。"""
        if not hasattr(self, "blur_toggle"):
            return
        ok = bool(getattr(THEME.p, "support_blur", True))
        self.blur_toggle.setEnabled(ok)
        if hasattr(self, "blur_row"):
            self.blur_row.set_desc("关闭后背景不再模糊，低配机器更流畅" if ok
                                   else "当前皮肤不使用毛玻璃，此开关无效")

    def _on_preset(self, key: str) -> None:
        THEME.set_preset(key)
        self.cfg.setdefault("ui", {})["preset"] = key
        self._sync_blur_control()
        self.config_changed.emit()
        self.theme_changed.emit()

    def _on_mode(self, key: str) -> None:
        THEME.set_mode(key)
        self.cfg.setdefault("ui", {})["theme"] = key
        self.config_changed.emit()
        self.theme_changed.emit()

    def _on_blur(self, on: bool) -> None:
        THEME.set_blur(on)
        self.cfg.setdefault("ui", {})["blur"] = on
        self.config_changed.emit()
        self.theme_changed.emit()

    # ---------- 后台运行与自动更新 ----------
    def _build_background(self, lay: QVBoxLayout) -> None:
        bg = self.cfg.setdefault("background", {})
        self._group(lay, "后台与自动更新")

        self.tray_toggle = ToggleSwitch(bool(bg.get("close_to_tray", True)))
        self.tray_toggle.toggled.connect(self._save_background)
        self._row(lay, "apps", "关闭窗口时最小化到托盘",
                  "程序继续在后台运行，托盘右键「退出」才真正结束", self.tray_toggle)

        self.startup_toggle = ToggleSwitch(autostart.is_enabled())
        self.startup_toggle.toggled.connect(lambda on: self.autostart_requested.emit(on))
        self._row(lay, "rocket", "开机自动启动",
                  "开机后在后台静默运行，不弹出主界面；移动文件夹后重新打开本开关即可",
                  self.startup_toggle)

        mins_opts = [(15, "15 分钟"), (30, "30 分钟"), (60, "1 小时"), (180, "3 小时"),
                     (360, "6 小时"), (720, "12 小时"), (1440, "24 小时")]
        cur = int(bg.get("interval_min", 60) or 60)
        near = min(mins_opts, key=lambda kv: abs(kv[0] - cur))[0]
        self.interval_combo = ThemedCombo([(str(m), t) for m, t in mins_opts], str(near), 118)
        self.interval_combo.changed.connect(self._save_background)

        box = QWidget()
        flat(box)
        bl = QHBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(10)
        self.sched_toggle = ToggleSwitch(bool(bg.get("check_enabled", True)))
        self.sched_toggle.toggled.connect(self._save_background)
        bl.addWidget(self.sched_toggle)
        bl.addWidget(self.interval_combo)
        self.sched_row = self._row(lay, "clock", "定时检查更新",
                                   "按间隔自动检查所有程序的新版本", box)

        self.auto_combo = ThemedCombo(
            [("off", "只通知，不自动更新"), ("notify", "静默更新，完成后提示"),
             ("silent", "完全静默更新（不打扰）")],
            bg.get("auto_mode", "off"), 196)
        self.auto_combo.changed.connect(self._save_background)
        self._row(lay, "download", "发现新版本时",
                  "静默更新会在后台直接替换文件（覆盖前仍会备份）", self.auto_combo)

        # ---- 通知方式：一个可展开的设置项，里面放全部通知相关设置 ----
        body = QWidget()
        flat(body)
        bl2 = QVBoxLayout(body)
        bl2.setContentsMargins(0, 0, 0, 0)
        bl2.setSpacing(14)

        chips_box = QWidget()
        flat(chips_box)
        il = QHBoxLayout(chips_box)
        il.setContentsMargins(0, 0, 0, 0)
        il.setSpacing(7)
        self.notify_chips: dict[str, Chip] = {}
        self._notify_state: dict[str, bool] = {}
        chosen = set(bg.get("notify_methods") or ["tray"])
        for key, text in (("tray", "系统通知"), ("dingtalk", "钉钉机器人"), ("window", "弹出主界面")):
            on = key in chosen
            c = Chip(text)
            c.set_active(on)
            c.clicked.connect(lambda _=False, k=key, cc=c: self._toggle_notify(k, cc))
            self.notify_chips[key] = c
            self._notify_state[key] = on
            il.addWidget(c)
        bl2.addWidget(self._sub_line("推送渠道", "可多选；都不选时只用系统通知", chips_box))


        self.webhook_edit = SecretEdit(self.cfg.get("dingtalk", {}).get("webhook", ""),
                                       "https://oapi.dingtalk.com/robot/send?access_token=…")
        self.webhook_edit.edit.editingFinished.connect(self._save_ding)
        self.secret_edit = SecretEdit(self.cfg.get("dingtalk", {}).get("secret", ""),
                                      "SEC…（加签密钥）")
        self.secret_edit.edit.editingFinished.connect(self._save_ding)

        ding_box = QWidget()
        flat(ding_box)
        dl = QVBoxLayout(ding_box)
        dl.setContentsMargins(0, 0, 0, 0)
        dl.setSpacing(14)
        dl.addWidget(self._sub_line("Webhook 端点",
                                    "在钉钉群机器人设置里获取 access_token 链接",
                                    self.webhook_edit))
        dl.addWidget(self._sub_line("加签密钥",
                                    "安全设置里选择「加签」后得到的 SEC 开头字符串",
                                    self.secret_edit))
        test_row = QWidget()
        flat(test_row)
        tl = QHBoxLayout(test_row)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(12)
        self.ding_test_lbl = _label("", 11, "text_muted", wrap=True)
        tl.addWidget(self.ding_test_lbl, 1)
        self.test_btn = QPushButton("发送测试")
        self.test_btn.setObjectName("soft")
        self.test_btn.setCursor(Qt.PointingHandCursor)
        self.test_btn.setFixedHeight(30)
        self.test_btn.clicked.connect(self._test_ding)
        tl.addWidget(self.test_btn)
        dl.addWidget(test_row)
        bl2.addWidget(ding_box)
        self.ding_box = ding_box

        self.notify_card = ExpandSetting("notify", "通知方式", "", body=body)
        lay.addWidget(self.notify_card)

        self._refresh_bg_hint()
        self.interval_combo.setEnabled(self.sched_toggle.isChecked())
        self._sync_ding_visibility()

    def _refresh_bg_hint(self) -> None:
        bg = self.cfg.get("background", {})
        if not bg.get("check_enabled", True):
            self.sched_row.set_desc("定时检查已关闭，只能在托盘右键或主界面手动检查")
            return
        mins = int(bg.get("interval_min", 60) or 60)
        span = f"{mins} 分钟" if mins < 60 else f"{mins // 60} 小时"
        self.sched_row.set_desc(f"每 {span}自动检查一次；关闭主窗口不会退出程序")

    def _toggle_notify(self, key: str, chip) -> None:
        on = not self._notify_state.get(key, False)
        self._notify_state[key] = on
        chip.set_active(on)
        self._save_background()
        self._sync_ding_visibility()

    def _notify_summary(self) -> str:
        names = {"tray": "系统通知", "dingtalk": "钉钉机器人", "window": "弹出主界面"}
        picked = [names[k] for k in ("tray", "dingtalk", "window")
                  if self._notify_state.get(k)]
        return " · ".join(picked) if picked else "未选择（默认只用系统通知）"

    def _sync_ding_visibility(self) -> None:
        """勾选钉钉时才显示 Webhook / 加签密钥。"""
        on = bool(self._notify_state.get("dingtalk", False))
        if hasattr(self, "ding_box"):
            self.ding_box.setVisible(on)
            if self.notify_card.is_open():
                QTimer.singleShot(0, lambda: self.notify_card.set_open(True))
        if hasattr(self, "notify_card"):
            self.notify_card.set_summary(self._notify_summary())
        self.cfg.setdefault("dingtalk", {})["enabled"] = on

    def _test_ding(self) -> None:
        # 输入框可能还没失焦（editingFinished 未发），先把当前文本落进 cfg 再测
        self._save_ding()
        cfg = self.cfg.get("dingtalk", {})
        if not (cfg.get("webhook") or "").strip():
            self.ding_test_lbl.setText("先填写 Webhook 端点再测试")
            return
        self.ding_test_lbl.setText("正在发送测试消息…")
        w = getattr(self, "_ding_test_worker", None)
        if w is not None and w.isRunning():
            return
        w = _DingTestWorker(cfg, "### 测试消息\n\n如果你看到这条，说明通知配置是通的。")
        w.done.connect(self._on_ding_test_done)
        self._ding_test_worker = w
        w.start()

    def _on_ding_test_done(self, ok: bool, info: str) -> None:
        self.ding_test_lbl.setText("测试消息已发送，去群里看看" if ok
                                   else "发送失败：" + (info or "检查 Webhook 与加签密钥"))

    def _save_background(self, *_):
        bg = self.cfg.setdefault("background", {})
        bg["close_to_tray"] = self.tray_toggle.isChecked()
        bg["check_enabled"] = self.sched_toggle.isChecked()
        try:
            bg["interval_min"] = int(self.interval_combo.value() or 60)
        except Exception:
            bg["interval_min"] = 60
        bg["auto_mode"] = self.auto_combo.value() or "off"
        bg["notify_methods"] = [k for k, on in self._notify_state.items() if on] or ["tray"]
        self.interval_combo.setEnabled(bg["check_enabled"])
        self._refresh_bg_hint()
        self.config_changed.emit()

    def refresh_background(self) -> None:
        self.startup_toggle.blockSignals(True)
        self.startup_toggle.setChecked(autostart.is_enabled())
        self.startup_toggle.blockSignals(False)

    # ---------- 更新策略 ----------
    def _build_strategy(self, lay: QVBoxLayout) -> None:
        self._group(lay, "更新策略")

        self.install_seg = self._segmented(
            [("clean", "只保留指定文件夹"), ("overwrite", "直接覆盖")],
            self.cfg.get("update", {}).get("install_mode", "clean"),
            self._on_install_mode)
        self.install_row = self._row(lay, "restore", "安装方式", "", self.install_seg)

        chips_box = QWidget()
        flat(chips_box)
        cl = QVBoxLayout(chips_box)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(8)
        chip_row = QHBoxLayout()
        chip_row.setSpacing(7)
        self.preserve_chips: dict[str, Chip] = {}
        for name, text in core.COMMON_PRESERVE:
            c = Chip(text)
            c.setToolTip(f"更新时保留 {name} 文件夹，不删除也不覆盖其内容")
            c.set_active(self._preserve_on(name))
            c.clicked.connect(lambda _=False, n=name, cc=c: self._toggle_preserve(n, cc))
            self.preserve_chips[name] = c
            chip_row.addWidget(c)
        chip_row.addStretch(1)
        cl.addLayout(chip_row)

        self.extra_edit = QLineEdit("，".join(self._preserve_extras()))
        self.extra_edit.setPlaceholderText("其他要保留的文件夹名，用逗号分隔（如 mods，plugins）")
        self.extra_edit.setMinimumHeight(32)
        self.extra_edit.editingFinished.connect(self._save_extras)
        cl.addWidget(self.extra_edit)
        self.preserve_row = self._row(lay, "folder", "默认保留的文件夹",
                                      "更新时这些文件夹原样保留，其余按安装方式处理", None, chips_box)
        self.preserve_box = chips_box
        self._preserve_fx = QGraphicsOpacityEffect(chips_box)
        self._preserve_fx.setOpacity(1.0)
        chips_box.setGraphicsEffect(self._preserve_fx)
        self._sync_install_desc()

        self.timeout_spin = NumberStepper(
            int(self.cfg.get("update", {}).get("timeout_min", 10)), 1, 180, 5, " 分钟", 108)
        self.timeout_spin.valueChanged.connect(self._save_timeout)
        self._row(lay, "timer", "单个程序更新超时",
                  "下载 + 解压总时长超过该值即中止，避免卡死", self.timeout_spin)

        bk = QWidget()
        flat(bk)
        bkl = QHBoxLayout(bk)
        bkl.setContentsMargins(0, 0, 0, 0)
        bkl.setSpacing(10)
        self.backup_toggle = ToggleSwitch(bool(self.cfg.get("backup", {}).get("enabled", True)))
        self.backup_toggle.toggled.connect(self._save_backup)
        self.keep_spin = NumberStepper(
            int(self.cfg.get("backup", {}).get("keep", 2)), 1, 10, 1, " 份", 96)
        self.keep_spin.valueChanged.connect(self._save_backup)
        bkl.addWidget(self.backup_toggle)
        bkl.addWidget(self.keep_spin)
        self._row(lay, "save", "覆盖前自动备份", "备份存放在根目录 Cache\\_backup 下", bk)

    def _on_install_mode(self, mode: str) -> None:
        self.cfg.setdefault("update", {})["install_mode"] = mode
        self._sync_install_desc()
        self.config_changed.emit()

    def _sync_install_desc(self) -> None:
        """安装方式与「默认保留的文件夹」联动：直接覆盖时保留列表不生效。"""
        clean = (self.cfg.get("update", {}).get("install_mode", "clean") != "overwrite")
        self.install_row.set_desc(
            "只保留指定文件夹：删除旧的无关文件后再解压新版" if clean
            else "直接覆盖：保留全部已有文件，只用新版同名内容替换")
        self.preserve_row.set_desc(
            "更新时这些文件夹原样保留，其余按安装方式处理" if clean
            else "直接覆盖模式下不生效；需要精细控制时可右键单个程序单独设置")
        self.preserve_box.setEnabled(clean)
        self._preserve_fx.setOpacity(1.0 if clean else 0.42)

    def _preserve_on(self, name: str) -> bool:
        low = name.lower()
        return any(str(e).lower() == low for e in self.cfg.get("preserve", []))

    def _preserve_extras(self) -> list[str]:
        common = {n.lower() for n, _ in core.COMMON_PRESERVE}
        return [str(e) for e in self.cfg.get("preserve", []) if str(e).lower() not in common]

    def _toggle_preserve(self, name: str, chip) -> None:
        on = not self._preserve_on(name)
        chip.set_active(on)
        cur = [e for e in self.cfg.get("preserve", []) if str(e).lower() != name.lower()]
        if on:
            cur.append(name)
        self.cfg["preserve"] = cur
        self.config_changed.emit()

    def _save_extras(self) -> None:
        common = {n.lower() for n, _ in core.COMMON_PRESERVE}
        keep = [e for e in self.cfg.get("preserve", []) if str(e).lower() in common]
        seen = {str(e).lower() for e in keep}
        raw = self.extra_edit.text().replace("，", ",")
        for x in raw.split(","):
            x = x.strip()
            if x and x.lower() not in seen:
                keep.append(x)
                seen.add(x.lower())
        self.cfg["preserve"] = keep
        self.extra_edit.setText("，".join(self._preserve_extras()))
        self.config_changed.emit()

    def _save_timeout(self, *_):
        self.cfg.setdefault("update", {})["timeout_min"] = int(self.timeout_spin.value())
        self.config_changed.emit()

    def _save_backup(self, *_):
        self.cfg.setdefault("backup", {})
        self.cfg["backup"]["enabled"] = self.backup_toggle.isChecked()
        self.cfg["backup"]["keep"] = self.keep_spin.value()
        self.config_changed.emit()

    # ---------- 检查文件夹 ----------
    def _build_roots(self, lay: QVBoxLayout) -> None:
        self._group(lay, "检查文件夹")
        holder = Card(padding=12)
        self.roots_box = QVBoxLayout()
        self.roots_box.setSpacing(6)
        holder.lay.addLayout(self.roots_box)
        add = QPushButton("+  添加文件夹")
        add.setObjectName("soft")
        add.setCursor(Qt.PointingHandCursor)
        add.setFixedHeight(34)
        add.clicked.connect(self._add_root)
        holder.lay.addWidget(add)
        lay.addWidget(holder)
        self._render_roots()

    def _render_roots(self) -> None:
        while self.roots_box.count():
            w = self.roots_box.takeAt(0).widget()
            if w:
                w.hide()            # 先隐藏再销毁，脱离父级会变成顶层小窗闪一下
                w.deleteLater()
        for r in self.cfg.get("roots", []):
            row = RootRow(r)
            row.changed.connect(self._roots_dirty)
            row.removed.connect(self._remove_root)
            self.roots_box.addWidget(row)

    def reload_roots(self) -> None:
        """外部（右键排除）改了排除列表后刷新显示。"""
        self._render_roots()

    def _roots_dirty(self) -> None:
        self.roots_changed.emit()

    def _add_root(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择要检查的文件夹")
        if not path:
            return
        path = os.path.normpath(path)
        for r in self.cfg.get("roots", []):
            if os.path.normpath(r.get("path", "")).lower() == path.lower():
                QMessageBox.information(self, "已存在", "这个文件夹已经在列表里了")
                return
        self.cfg.setdefault("roots", []).append(
            {"path": path, "exclude": ["Cache", ".git", "node_modules"], "depth": 4, "enabled": True})
        self._render_roots()
        self.roots_changed.emit()

    def _remove_root(self, path: str) -> None:
        self.cfg["roots"] = [r for r in self.cfg.get("roots", [])
                             if os.path.normpath(r.get("path", "")) != os.path.normpath(path)]
        self._render_roots()
        self.roots_changed.emit()

    # ---------- 网络 ----------
    def _mirror_summary(self) -> str:
        m = self.cfg.get("mirrors", {})
        lst = core.mirror_list(self.cfg)
        n = len(m.get("list") or [])
        chosen = (m.get("chosen") or "").strip()
        if not m.get("enabled", True):
            return f"镜像加速已关闭，全部走直连 / 本地代理（{n} 个候选已停用）"
        if not chosen:
            return f"共 {n} 个候选镜像，尚未测速（下次检查时自动挑最快的）"
        return f"当前使用 {chosen}　候选 {n} 个"

    def _mirror_addr_desc(self) -> str:
        m = self.cfg.get("mirrors", {})
        lst = [x for x in (m.get("list") or []) if isinstance(x, str)]
        builtin = {x.rstrip("/") for x in core.DEFAULT_MIRRORS}
        n_custom = sum(1 for x in lst if x.rstrip("/") not in builtin)
        head = f"共 {len(lst)} 个（内置 {len(lst) - n_custom} · 自定义 {n_custom}）"
        return head + "；可以手动添加自己的 GitHub 代理地址，保存后一起参与测速"

    def refresh_mirror_row(self) -> None:
        self.mirror_row.set_desc(self._mirror_addr_desc())
        self.mirror_lbl.setText(self._mirror_summary())

    def _build_network(self, lay: QVBoxLayout) -> None:
        self._group(lay, "网络")

        # ---- 代理：自动探测 / 手动指定 / 不使用 ----
        pcfg = self.cfg.setdefault("proxy", {})
        mode = pcfg.get("mode", "auto")
        self.proxy_mode = ThemedCombo(
            [("auto", "自动探测"), ("manual", "手动指定"), ("off", "不使用代理")],
            mode if mode in ("auto", "manual", "off") else "auto", 128)
        self.proxy_mode.changed.connect(self._save_proxy)

        self.net_lbl = None
        self.proxy_row = self._row(
            lay, "link", "本地代理", self.PROXY_DESC,
            self.proxy_mode)

        self.proxy_edit = QLineEdit((pcfg.get("url") or ""))
        self.proxy_edit.setPlaceholderText("http://127.0.0.1:7890")
        self.proxy_edit.setMinimumHeight(32)
        self.proxy_edit.setMinimumWidth(320)
        self.proxy_edit.editingFinished.connect(self._save_proxy)
        self.proxy_addr_row = self._row(
            lay, "settings", "代理地址",
            "仅支持 http://（SOCKS5 请在本机转成 HTTP 端口）；带密码写成 http://用户名:密码@主机:端口",
            self.proxy_edit)
        self.proxy_addr_row.setVisible(mode == "manual")

        mcfg = self.cfg.setdefault("mirrors", {})
        self.mirror_toggle = ToggleSwitch(bool(mcfg.get("enabled", True)))
        self.mirror_toggle.toggled.connect(self._save_mirrors)
        self._row(lay, "download", "GitHub 镜像加速",
                  f"内置 {len(mcfg.get('list') or [])} 个公共镜像，默认开启；关闭后只走直连与代理",
                  self.mirror_toggle)

        self.mirror_btn = self._small_btn("管理…", self.mirror_manage_requested.emit)
        self.mirror_btn.setToolTip("增删镜像地址：可以加自己的 GitHub 代理 / 反代")
        self.mirror_row = self._row(lay, "link", "镜像地址", self._mirror_addr_desc(),
                                    self.mirror_btn)

        box = QWidget()
        flat(box)
        bl = QHBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(10)
        self.daily_toggle = ToggleSwitch(bool(mcfg.get("daily_check", True)))
        self.daily_toggle.toggled.connect(self._save_mirrors)
        self.probe_btn = QPushButton("立即测速")
        self.probe_btn.setObjectName("soft")
        self.probe_btn.setCursor(Qt.PointingHandCursor)
        self.probe_btn.setFixedHeight(30)
        self.probe_btn.clicked.connect(self._on_probe_mirrors)
        bl.addWidget(self.daily_toggle)
        bl.addWidget(self.probe_btn)
        self.mirror_lbl = _label(self._mirror_summary(), 11, "text_muted", wrap=True)
        self._row(lay, "refresh", "每天自动测速一次", "始终保持使用最快的线路", box, self.mirror_lbl)


    def _save_ding(self, *_):
        self.cfg.setdefault("dingtalk", {})
        self.cfg["dingtalk"]["enabled"] = bool(self._notify_state.get("dingtalk", False))
        self.cfg["dingtalk"]["webhook"] = self.webhook_edit.text().strip()
        self.cfg["dingtalk"]["secret"] = self.secret_edit.text().strip()
        self.config_changed.emit()

    def _save_mirrors(self, *_):
        m = self.cfg.setdefault("mirrors", {})
        m["enabled"] = self.mirror_toggle.isChecked()
        m["daily_check"] = self.daily_toggle.isChecked()
        self.mirror_lbl.setText(self._mirror_summary())
        self.config_changed.emit()
        self.mirrors_changed.emit()

    def _save_proxy(self, *_):
        pc = self.cfg.setdefault("proxy", {})
        mode = self.proxy_mode.value() or "auto"
        pc["mode"] = mode
        url = self.proxy_edit.text().strip()
        pc["url"] = url
        self.proxy_addr_row.setVisible(mode == "manual")
        if url.lower().startswith("socks"):
            # urllib 的 ProxyHandler 不支持 SOCKS，填了也会静默失效，必须说清楚
            self.proxy_addr_row.set_desc(
                "SOCKS5 暂不支持，请填 http:// 开头（或在代理软件里开一个 HTTP 端口）")
        else:
            self.proxy_addr_row.set_desc(
                "仅支持 http://（SOCKS5 请在本机转成 HTTP 端口）；带密码写成 http://用户名:密码@主机:端口")
        self.config_changed.emit()
        self.proxy_changed.emit()

    def _on_probe_mirrors(self) -> None:
        self.probe_btn.setEnabled(False)
        self.probe_btn.setText("测速中…")
        self.mirror_lbl.setText("正在并发测试所有镜像，请稍候…")
        self.mirror_probe_requested.emit()

    def set_mirror_results(self, ranked=None, error: str = "") -> None:
        self.probe_btn.setEnabled(True)
        self.probe_btn.setText("立即测速")
        if error:
            self.mirror_lbl.setText(f"测速失败：{error}")
            return
        ranked = ranked or []
        if not ranked:
            self.mirror_lbl.setText("所有镜像都没有响应，将回退到直连 / 本地代理")
            return
        top = "　".join(f"{m.replace('https://', '')}（{ms} ms）" for ms, m in ranked[:4])
        self.mirror_lbl.setText(f"测速完成，可用 {len(ranked)} 个：{top}")

    def set_network_info(self, text: str) -> None:
        """把当前代理与镜像状态接在「本地代理」行的说明后面。"""
        self.proxy_row.set_desc(f"{self.PROXY_DESC}　·　{text}" if text else self.PROXY_DESC)

    # ---------- 关于 ----------
    def _build_about(self, lay: QVBoxLayout) -> None:
        self._group(lay, "关于")
        self._row(lay, "info", f"{core.APP_NAME}", f"版本 v{core.APP_VERSION}")
        self._row(lay, "folder", "配置目录", core.config_dir(),
                  self._small_btn("打开", lambda: core.open_in_explorer(core.config_dir())))
        self._row(lay, "save", "备份目录", "覆盖前的旧版本都留在这里",
                  self._small_btn("打开", self._open_backup))
        self._row(lay, "check", "便携模式", "配置与备份都在 exe 同目录，整个文件夹可随意移动")
        self._row(lay, "apps", "快捷键",
                  "Ctrl+1/2/3 切换页面　F5 重新检查　右键程序行可绑定仓库、标记版本、排除文件夹")

    def _small_btn(self, text: str, slot) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("ghost")
        b.setCursor(Qt.PointingHandCursor)
        b.setFixedHeight(30)
        b.clicked.connect(slot)
        return b

    def _open_backup(self) -> None:
        roots = [r.get("path", "") for r in self.cfg.get("roots", []) if r.get("path")]
        target = os.path.join(roots[0], "Cache", "_backup") if roots else "."
        os.makedirs(target, exist_ok=True)
        core.open_in_explorer(target)

    # ---------- 主题刷新 ----------
    def refresh_theme(self) -> None:
        self._style_segmented()
        self._sync_blur_control()
        p = THEME.p
        for row in self.findChildren(RootRow):
            row.icon.set_color(p.accent)
            tint(row.path_lbl, p.text)
            tint(row.sub_lbl, p.text_muted)
            row.update()
        for card in self.findChildren(Card):
            card.update()
        for tab in (self.tab_general, self.tab_about):
            tab._apply()
        # 下拉框 / 筛选胶囊内部用了内联样式，theme 切换后必须手动重刷，否则残留旧配色
        for combo in self.findChildren(ThemedCombo):
            combo.refresh_theme()
        for chip in self.findChildren(Chip):
            chip.refresh_theme()
