# -*- coding: utf-8 -*-
"""自定义控件：玻璃卡片、开关、徽章、筛选胶囊、程序行、标题栏等。"""

from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QEvent, QEventLoop, QPoint, QPointF,
                            QPropertyAnimation, QRectF, QSize, Qt, QTimer, Property, Signal, QObject)
from PySide6.QtGui import (QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath,
                           QPen)
from PySide6.QtWidgets import (QApplication, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QSizePolicy, QVBoxLayout,
                               QWidget)

from backdrop import BACKDROP, paint_glass
from theme import (GLYPH, THEME, badge_fg, card_color, flat, glyph_font, qc, rgba, scoped,
                   tint)


class _MenuAction(QObject):
    """自绘菜单的动作项（对齐 QMenu.addAction 的常用接口）。"""

    triggered = Signal()
    toggled = Signal(bool)

    def __init__(self, text: str = "", separator: bool = False):
        super().__init__()
        self.text = text
        self.separator = separator
        self.enabled = True
        self.visible = True
        self.checkable = False
        self.checked = False

    def setEnabled(self, on: bool) -> None:
        self.enabled = on

    def setVisible(self, on: bool) -> None:
        self.visible = on

    def setText(self, text: str) -> None:
        self.text = text

    def setCheckable(self, on: bool) -> None:
        self.checkable = on

    def setChecked(self, on: bool) -> None:
        if self.checked == on:
            return
        self.checked = on
        if self.checkable:
            self.toggled.emit(on)


class ThemedMenu(QWidget):
    """全自绘的玻璃菜单：底板模糊背后真实画面，悬停高亮自己跟踪、移开即清除。

    不用原生 QMenu —— 半透明 + 无边框模式下它会留下悬停残影，
    且和自绘玻璃叠不出正确效果。
    """

    ITEM_H = 30
    MARGIN = 7
    SEP_V = 5  # 分隔线上下留白

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMouseTracking(True)
        self._actions: list[_MenuAction] = []
        self._hover = -1
        self._rects: list[tuple[_MenuAction, QRectF]] = []
        self._result: _MenuAction | None = None
        self._loop: QEventLoop | None = None

    # ---------- 构建菜单（QMenu 风格） ----------
    def addAction(self, text: str) -> _MenuAction:
        act = _MenuAction(text)
        self._actions.append(act)
        return act

    def addSeparator(self) -> _MenuAction:
        act = _MenuAction(separator=True)
        self._actions.append(act)
        return act

    def _live_actions(self) -> list[_MenuAction]:
        return [a for a in self._actions if a.visible and not a.separator]

    # ---------- 布局 ----------
    def _font(self) -> QFont:
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(12)
        return f

    def _compute_size(self) -> None:
        fm = QFontMetrics(self._font())
        widest = 0
        for a in self._actions:
            if a.visible and not a.separator:
                widest = max(widest, fm.horizontalAdvance(a.text))
        self._w = max(168, widest + self.MARGIN * 2 + 12 + 24)
        n_items = len(self._live_actions())
        n_sep = sum(1 for a in self._actions if a.visible and a.separator)
        self._h = self.MARGIN * 2 + n_items * self.ITEM_H + (n_sep * 2 * self.SEP_V + n_sep)
        self.setFixedSize(self._w, self._h)

    def _layout_items(self) -> None:
        self._rects = []
        y = float(self.MARGIN)
        for a in self._actions:
            if not a.visible:
                continue
            if a.separator:
                y += self.SEP_V
                self._rects.append((a, QRectF(self.MARGIN + 4, y + 0.5, self._w - self.MARGIN * 2 - 8, 1)))
                y += 1 + self.SEP_V
            else:
                self._rects.append((a, QRectF(self.MARGIN, y, self._w - self.MARGIN * 2, self.ITEM_H)))
                y += self.ITEM_H

    def _action_at(self, pos: QPoint) -> _MenuAction | None:
        for a, r in self._rects:
            if a.separator or not a.visible:
                continue
            if r.contains(QPointF(pos)):
                return a
        return None

    def _index_at(self, pos: QPoint) -> int:
        for i, (a, r) in enumerate(self._rects):
            if a.separator or not a.visible:
                continue
            if r.contains(QPointF(pos)):
                return i
        return -1

    # ---------- 绘制 ----------
    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing | QPainter.TextAntialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = max(8, min(p.radius_btn + 2, 14))
        path = paint_glass(painter, rect, radius, self.mapToGlobal(QPoint(0, 0)), p,
                           THEME.blur_active)
        painter.setPen(QPen(qc(p.border, 0.20), 1))
        painter.drawPath(path)

        self._layout_items()
        painter.setFont(self._font())
        for i, (a, r) in enumerate(self._rects):
            if a.separator:
                painter.setPen(QPen(qc(p.border, 0.13), 1))
                painter.drawLine(QPointF(r.left(), r.top()), QPointF(r.right(), r.top()))
                continue
            hovered = i == self._hover and a.enabled
            if hovered:
                painter.setPen(Qt.NoPen)
                painter.setBrush(qc(p.accent, 0.16))
                painter.drawRoundedRect(r, 7, 7)
            painter.setPen(qc(p.accent if hovered else (p.text if a.enabled else p.text_muted)))
            tx = r.left() + 12
            if a.checkable:
                if a.checked:
                    painter.drawText(QRectF(r.left(), r.top(), 12, r.height()),
                                     Qt.AlignCenter, "✓")
                tx = r.left() + 24
            painter.drawText(QRectF(tx, r.top(), r.right() - tx, r.height()),
                             Qt.AlignVCenter | Qt.AlignLeft, a.text)
        painter.end()

    # ---------- 交互 ----------
    def mouseMoveEvent(self, e):
        idx = self._index_at(e.position().toPoint())
        if idx != self._hover:
            self._hover = idx
            self.update()          # 全量重绘，旧高亮立即消失
        self.setCursor(Qt.PointingHandCursor if self._hover >= 0 else Qt.ArrowCursor)

    def leaveEvent(self, e):
        if self._hover != -1:
            self._hover = -1
            self.update()

    def mousePressEvent(self, e):
        act = self._action_at(e.position().toPoint())
        if act is None:
            # Popup 会抓取鼠标：点击菜单外的按下事件也会投递到这里（坐标在边界外），
            # 必须交给基类处理，它负责把点在弹层外面的情况关闭（否则菜单永远不消失）
            super().mousePressEvent(e)
            return
        if not act.enabled:
            return                 # 禁用项：不关闭、不触发
        if act.checkable:
            act.setChecked(not act.checked)
        self._result = act
        act.triggered.emit()
        self.close()

    def hideEvent(self, e):
        if self._loop is not None:
            self._loop.quit()
        super().hideEvent(e)

    # ---------- 弹出 ----------
    def exec(self, global_pos) -> _MenuAction | None:
        """弹出并阻塞，返回被点中的动作（未选择返回 None），同 QMenu.exec。"""
        self._result = None
        self._place(global_pos)
        BACKDROP.snapshot(self.screen())   # 先取样，才模糊「背后真实内容」
        self.show()
        self._loop = QEventLoop()
        self._loop.exec()
        self._loop = None
        return self._result

    def popup_at(self, global_pos) -> None:
        """直接弹出，不阻塞（托盘菜单用，走动作信号）。"""
        self._place(global_pos)
        BACKDROP.snapshot(self.screen())
        self.show()

    def _place(self, global_pos) -> None:
        self._compute_size()
        self._hover = -1
        pos = QPointF(global_pos).toPoint()
        screen = QApplication.screenAt(pos + QPoint(8, 8)) or QApplication.primaryScreen()
        if screen is not None:
            g = screen.availableGeometry()
            pos.setX(max(g.left(), min(pos.x(), g.right() - self._w - 4)))
            pos.setY(max(g.top(), min(pos.y(), g.bottom() - self._h - 4)))
        self.move(pos)


class Glyph(QLabel):
    """Segoe Fluent 图标。"""

    def __init__(self, name: str, size: int = 16, color: str | None = None, parent=None):
        super().__init__(parent)
        self._size = size
        self._color = color
        self.setText(GLYPH.get(name, name))
        f = glyph_font(size)
        self.setFont(f)
        self.setFixedSize(size + 6, size + 6)
        self.setAlignment(Qt.AlignCenter)
        tint(self, color or THEME.p.text_dim)

    def set_color(self, color: str) -> None:
        self._color = color
        tint(self, color)


class WrapLabel(QLabel):
    """会自动换行并按宽度撑高的 QLabel。

    Qt 里 QLabel 虽然实现了 heightForWidth，但布局经常只按单行高度分配空间，
    窗口一窄、文字折成两行，第二行就被上下裁掉（只剩半截字）。这里在每次改宽度时
    把 minimumHeight 同步成 heightForWidth，布局就压不扁它了。
    """

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        sp = self.sizePolicy()
        sp.setHeightForWidth(True)
        sp.setVerticalPolicy(QSizePolicy.Minimum)
        self.setSizePolicy(sp)

    def _sync_height(self) -> None:
        w = self.width()
        if w <= 0:
            return
        # 必须先在「无最小高度约束」的干净状态下求 heightForWidth：QLabel 会把
        # 已设的 minimumHeight 算进文本可用高度，导致算出来的值只增不减（窄窗口
        # 折行撑到 24 后，拉宽也缩不回 11，行距就一直偏大）。
        self.setMinimumHeight(0)
        h = self.heightForWidth(w)
        if h > 0:
            self.setMinimumHeight(h)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._sync_height()

    def showEvent(self, e):
        # 从隐藏变可见（set_desc / set_summary 常这么做）时宽度刚定下，重算一次
        super().showEvent(e)
        self._sync_height()

    def setText(self, text: str) -> None:
        # 动态改字（set_desc / set_summary）时控件往往还隐藏、宽度还是默认的 100px，
        # 此刻 heightForWidth 会把短句误判成「折好几行」返回偏大的值并锁进
        # minimumHeight；等真正显示、宽度变宽后 QLabel 换行缓存又不会自动缩回，
        # 结果标题与说明之间被撑出一大块空白（行间距不对）。
        # 所以这里只把旧高度清零并让布局重算，绝不在宽度未定时锁定高度；
        # 真正的同步交给随后的 showEvent / resizeEvent（那时宽度才是可信的）。
        super().setText(text)
        self.setMinimumHeight(0)
        self.updateGeometry()


class Card(QFrame):
    """圆角面板。hover=True 时鼠标悬停会轻微提亮。"""

    clicked = Signal()

    def __init__(self, parent=None, padding: int = 16, hover: bool = False, radius: int | None = None):
        super().__init__(parent)
        self.padding = padding
        self._hover = False
        self._hoverable = hover
        self._radius = radius
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setMouseTracking(True)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(padding, padding, padding, padding)
        self.lay.setSpacing(10)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

    def radius(self) -> int:
        return self._radius if self._radius is not None else THEME.p.radius_card

    def enterEvent(self, e):
        if self._hoverable:
            self._hover = True
            self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        if self._hoverable:
            self._hover = False
            self.update()
        super().leaveEvent(e)

    def mouseReleaseEvent(self, e):
        if self._hoverable and e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = self.radius()

        base = card_color(p, hover=self._hover)

        path = QPainterPath()
        path.addRoundedRect(rect, rad, rad)
        painter.fillPath(path, base)

        # 深色玻璃加一层顶部高光，避免平
        if p.dark:
            g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            g.setColorAt(0.0, QColor(255, 255, 255, 16))
            g.setColorAt(0.35, QColor(255, 255, 255, 4))
            g.setColorAt(1.0, QColor(255, 255, 255, 0))
            painter.fillPath(path, g)

        painter.setPen(QPen(qc(p.border, p.border_alpha), 1))
        painter.drawPath(path)
        painter.end()


class NavButton(QWidget):
    """侧边栏导航项。"""

    clicked = Signal(str)

    def __init__(self, key: str, icon: str, text: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.icon_name = icon
        self.text = text
        self.active = False
        self._hover = False
        self.setFixedHeight(42)
        self.setCursor(Qt.PointingHandCursor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName(text)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.clicked.emit(self.key)
            return
        super().keyPressEvent(e)

    def set_active(self, on: bool) -> None:
        if self.active != on:
            self.active = on
            self.update()

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit(self.key)

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(8, 2, -8, -2)

        if self.active:
            accent = qc(p.accent, 0.16 if p.dark else 0.13)
            painter.setPen(Qt.NoPen)
            painter.setBrush(accent)
            painter.drawRoundedRect(rect, p.radius_btn + 2, p.radius_btn + 2)
            bar = QRectF(rect.left() - 8, rect.center().y() - 9, 3, 18)
            painter.setBrush(qc(p.accent, 0.95))
            painter.drawRoundedRect(bar, 1.5, 1.5)
        elif self._hover:
            painter.setPen(Qt.NoPen)
            painter.setBrush(qc(p.text, 0.055))
            painter.drawRoundedRect(rect, p.radius_btn + 2, p.radius_btn + 2)

        color = p.text if self.active else (p.text if self._hover else p.text_dim)
        f = glyph_font(17)
        painter.setFont(f)
        painter.setPen(QPen(qc(color if self.active else p.text_muted)))
        painter.drawText(QRectF(rect.left() + 12, rect.top(), 24, rect.height()),
                         Qt.AlignLeft | Qt.AlignVCenter, GLYPH.get(self.icon_name, ""))

        f2 = QFont("Microsoft YaHei UI")
        f2.setPixelSize(14)
        f2.setWeight(QFont.DemiBold if self.active else QFont.Normal)
        painter.setFont(f2)
        painter.setPen(QPen(qc(color)))
        painter.drawText(rect.adjusted(44, 0, -8, 0), Qt.AlignLeft | Qt.AlignVCenter, self.text)
        painter.end()


class ToggleSwitch(QWidget):
    """iOS / 澎湃风格开关。"""

    toggled = Signal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self._checked = checked
        self._pos = 1.0 if checked else 0.0
        self.setFixedSize(44, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self._anim = QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.setChecked(not self._checked, emit=True)
            return
        super().keyPressEvent(e)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, on: bool, emit: bool = False) -> None:
        if self._checked == on:
            return
        self._checked = on
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()
        if emit:
            self.toggled.emit(on)

    def get_knob(self) -> float:
        return self._pos

    def set_knob(self, v: float) -> None:
        self._pos = v
        self.update()

    knob = Property(float, get_knob, set_knob)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.setChecked(not self._checked, emit=True)

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height())
        off = qc(p.text, 0.16 if p.dark else 0.14)
        on = qc(p.accent, 0.95)
        painter.setPen(Qt.NoPen)
        painter.setBrush(on if self._checked else off)
        painter.drawRoundedRect(rect, rect.height() / 2, rect.height() / 2)

        d = rect.height() - 6
        x = 3 + self._pos * (rect.width() - d - 6)
        painter.setBrush(qc("#FFFFFF", 0.98) if self._checked else qc(p.text, 0.75))
        painter.drawEllipse(QRectF(x, 3, d, d))
        painter.end()


class Badge(QWidget):
    """小标签：来源、状态。

    自绘而不是靠 QLabel 的 padding —— 布局挤压时 QLabel 的底色会被裁掉，
    出现「背景没包住文字」；这里按字体度量算宽度，底色必定精确包裹。
    """

    H = 20
    PAD_X = 8

    def __init__(self, text: str = "", color: str = "dim", parent=None):
        super().__init__(parent)
        self._text = ""
        self._color = color
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(11)
        f.setWeight(QFont.DemiBold)
        self.setFont(f)
        self.setFixedHeight(self.H)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._sync(text)

    # ---------- 属性 ----------
    def text(self) -> str:
        return self._text

    def setText(self, text: str) -> None:
        self._sync(text or "")

    def set_color(self, color: str) -> None:
        self._color = color
        self.update()

    def _resolved(self) -> str:
        p = THEME.p
        return {"dim": p.text_muted, "accent": p.accent, "success": p.success,
                "warning": p.warning, "danger": p.danger, "text": p.text_dim}.get(self._color,
                                                                                  self._color)

    def _sync(self, text: str) -> None:
        self._text = text
        fm = QFontMetrics(self.font())
        w = fm.horizontalAdvance(text) + self.PAD_X * 2 if text else 0
        self.setFixedWidth(w)
        if text and self.parentWidget() is None:
            # 还没挂进布局就想显示 —— 无父级 setVisible(True) 会让徽章变成独立顶层小窗，
            # 列表每次重建都闪一批；等收到 ParentChange 再显示。
            self.hide()
            self._pending_show = True
        else:
            self._pending_show = False
            self.setVisible(bool(text))
        self.updateGeometry()
        self.update()

    def event(self, e):
        if e.type() == QEvent.ParentChange and getattr(self, "_pending_show", False):
            self._pending_show = False
            self.setVisible(True)
        return super().event(e)

    def sizeHint(self) -> QSize:
        return QSize(self.width(), self.H)

    # ---------- 绘制 ----------
    def paintEvent(self, e):
        if not self._text:
            return
        col = QColor(self._resolved())
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 6, 6)
        bg = QColor(col)
        bg.setAlphaF(0.16)
        painter.fillPath(path, bg)
        painter.setFont(self.font())
        painter.setPen(qc(badge_fg(col.name(), THEME.p.dark), 0.95))
        painter.drawText(self.rect(), Qt.AlignCenter, self._text)
        painter.end()


class Chip(QPushButton):
    """筛选胶囊，带计数。"""

    def __init__(self, text: str, count: int | None = None, parent=None):
        super().__init__(parent)
        self._text = text
        self._count = count
        self._active = False
        self.setCursor(Qt.PointingHandCursor)
        self.setCheckable(False)
        self.setFixedHeight(32)          # 与搜索框、下拉框统一高度
        self._apply()

    def set_active(self, on: bool) -> None:
        self._active = on
        self._apply()

    def is_active(self) -> bool:
        return self._active

    def set_count(self, n: int) -> None:
        self._count = n
        self._apply()

    def refresh_theme(self) -> None:
        """主题切换后重刷配色（与 SearchBox/ThemedCombo 保持同名协议）。"""
        self._apply()

    def _apply(self) -> None:
        p = THEME.p
        label = self._text
        if self._count is not None:
            label += f"  {self._count}"
        self.setText(label)
        # 圆角超过高度一半时 Qt 会画成直角，这里统一夹住
        r = max(8, min(p.radius_chip, 16))
        if self._active:
            scoped(self,
                   f"background: {rgba(p.accent, 0.95)}; color: {p.accent_on};"
                   f"border: none; border-radius: {r}px; padding: 0 14px;"
                   f"font-size: 12px; font-weight: 600;")
        else:
            scoped(self,
                   f"background: {rgba(p.text, 0.07)}; color: {p.text_dim};"
                   f"border: none; border-radius: {r}px; padding: 0 14px;"
                   f"font-size: 12px;")
            self.setCursor(Qt.PointingHandCursor)


class SearchBox(QLineEdit):
    """搜索框：图标 + 自绘占位文字（保证占位文字和图标同一深浅、同一中线）。"""

    def __init__(self, placeholder: str = "搜索程序…", parent=None):
        super().__init__(parent)
        self.setObjectName("search")
        self._ph = placeholder
        self.setPlaceholderText("")
        self.setFixedHeight(32)
        self.setFont(self._font())
        self._icon = Glyph("search", 14, THEME.p.text_muted, self)
        self._icon.setFixedSize(16, 16)
        self._icon.move(12, 8)
        self._icon.setAttribute(Qt.WA_TransparentForMouseEvents)

    @staticmethod
    def _font() -> QFont:
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(13)
        return f

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.text() or not self._ph:
            return
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.TextAntialiasing)
        painter.setFont(self.font())
        painter.setPen(QPen(qc(p.text_muted, 0.95)))
        painter.drawText(self.rect().adjusted(34, 0, -12, 0),
                         Qt.AlignLeft | Qt.AlignVCenter, self._ph)
        painter.end()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._icon.setFixedSize(16, 16)
        self._icon.move(12, (self.height() - 16) // 2)

    def refresh_theme(self) -> None:
        self._icon.set_color(THEME.p.text_muted)
        self.update()


class StatTile(QWidget):
    """数字 + 说明的小块。"""

    def __init__(self, value: str, label: str, color_key: str = "text", parent=None):
        super().__init__(parent)
        self.value_text = value
        self.label = label
        self.color_key = color_key
        self.setMinimumHeight(62)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        self.v = QLabel(value)
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(22)
        f.setWeight(QFont.DemiBold)
        self.v.setFont(f)
        self.l = QLabel(label)
        f2 = QFont("Microsoft YaHei UI")
        f2.setPixelSize(11)
        self.l.setFont(f2)
        lay.addWidget(self.v)
        lay.addWidget(self.l)
        self.refresh()

    def set_values(self, value: str, label: str | None = None) -> None:
        self.value_text = value
        if label is not None:
            self.label = label
        self.refresh()

    def refresh(self) -> None:
        p = THEME.p
        col = getattr(p, self.color_key, p.text)
        self.v.setText(self.value_text)
        tint(self.v, col)
        self.l.setText(self.label)
        tint(self.l, p.text_dim)

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5),
                            p.radius_card - 4, p.radius_card - 4)
        painter.fillPath(path, card_color(p))
        painter.setPen(QPen(qc(p.border, p.border_alpha), 1))
        painter.drawPath(path)
        painter.end()


class ProgressLine(QWidget):
    """细进度条，带流光。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._v = 0.0
        self.setFixedHeight(6)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._phase = 0.0
        self._indeterminate = False

    def set_value(self, v: float) -> None:
        self._v = max(0.0, min(1.0, v))
        self.update()

    def set_indeterminate(self, on: bool) -> None:
        self._indeterminate = on
        if on:
            self._timer.start(40)
        else:
            self._timer.stop()
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.02) % 1.0
        self.update()

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect())
        painter.setPen(Qt.NoPen)
        painter.setBrush(qc(p.text, 0.10))
        painter.drawRoundedRect(r, 3, 3)
        if self._indeterminate:
            w = r.width() * 0.3
            x = r.width() * self._phase * 1.4 - w
            g = QLinearGradient(x, 0, x + w, 0)
            g.setColorAt(0, qc(p.accent, 0.0))
            g.setColorAt(0.5, qc(p.accent, 0.95))
            g.setColorAt(1, qc(p.accent, 0.0))
            painter.setBrush(g)
            painter.drawRoundedRect(QRectF(max(x, 0), 0, min(w, r.width()), r.height()), 3, 3)
        else:
            painter.setBrush(qc(p.accent, 0.95))
            painter.drawRoundedRect(QRectF(0, 0, r.width() * self._v, r.height()), 3, 3)
        painter.end()


class ProgramRow(QFrame):
    """程序列表中的一行，可展开更新日志。"""

    update_requested = Signal(str)
    open_requested = Signal(str)
    bind_requested = Signal(str)
    repo_requested = Signal(str)
    exclude_requested = Signal(str)
    mark_requested = Signal(str)
    undo_requested = Signal(str)
    preserve_requested = Signal(str)
    asset_requested = Signal(str)

    def __init__(self, item, parent=None):
        super().__init__(parent)
        self.item = item
        self._expanded = False
        self._hover = False
        self.setMouseTracking(True)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.body = QWidget(self)
        lay = QHBoxLayout(self.body)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(12)

        # 左侧：首字母方块
        self.avatar = QLabel((item.name or "?")[:1].upper())
        self.avatar.setFixedSize(38, 38)
        self.avatar.setAlignment(Qt.AlignCenter)
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(16)
        f.setWeight(QFont.Bold)
        self.avatar.setFont(f)

        # 中间：名称 + 徽章 + 路径
        mid = QVBoxLayout()
        mid.setSpacing(4)
        title_row = QHBoxLayout()
        title_row.setSpacing(7)
        self.name_lbl = QLabel(item.name)
        fn = QFont("Microsoft YaHei UI")
        fn.setPixelSize(14)
        fn.setWeight(QFont.DemiBold)
        self.name_lbl.setFont(fn)
        title_row.addWidget(self.name_lbl)

        self.kind_badge = Badge("GIT" if item.kind == "git" else "Release",
                               "accent" if item.kind == "git" else "text")
        title_row.addWidget(self.kind_badge)
        self.status_badge = Badge("")
        title_row.addWidget(self.status_badge)
        title_row.addStretch(1)
        mid.addLayout(title_row)

        self.detail_lbl = QLabel()
        fd = QFont("Microsoft YaHei UI")
        fd.setPixelSize(11)
        self.detail_lbl.setFont(fd)
        self.detail_lbl.setMaximumWidth(430)
        mid.addWidget(self.detail_lbl)

        # 右侧：版本变化
        self.ver_lbl = QLabel()
        self.ver_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.ver_lbl.setMinimumWidth(104)       # 避免被挤压成只剩一两个字符
        fv = QFont("Consolas")
        fv.setPixelSize(12)
        self.ver_lbl.setFont(fv)

        # 按钮
        btns = QHBoxLayout()
        btns.setSpacing(6)
        self.log_btn = QPushButton("日志", self.body)
        self.log_btn.setObjectName("ghost")
        self.log_btn.setFixedHeight(30)
        self.log_btn.setCursor(Qt.PointingHandCursor)
        self.log_btn.clicked.connect(self.toggle_expand)

        self.action_btn = QPushButton("更新", self.body)
        self.action_btn.setObjectName("primary")
        self.action_btn.setFixedHeight(30)
        self.action_btn.setCursor(Qt.PointingHandCursor)
        self.action_btn.clicked.connect(lambda: self.update_requested.emit(self.item.key))

        btns.addWidget(self.log_btn)
        btns.addWidget(self.action_btn)

        lay.addWidget(self.avatar)
        lay.addLayout(mid, 1)
        lay.addWidget(self.ver_lbl)
        lay.addLayout(btns)
        root.addWidget(self.body)

        # 展开区
        self.notes = QWidget(self)
        nl = QVBoxLayout(self.notes)
        # 展开区几乎占满卡片宽度：以前左缩进 64px 把可用宽度压掉一大截，
        # 长日志每行都折行，看起来又小又挤
        nl.setContentsMargins(22, 2, 18, 16)
        nl.setSpacing(8)
        self.notes_title = QLabel("更新日志")
        ft = QFont("Microsoft YaHei UI")
        ft.setPixelSize(12)
        ft.setWeight(QFont.DemiBold)
        self.notes_title.setFont(ft)
        # 用可滚动的只读文本框：长 release notes 直接塞 QLabel 会把列表行撑成巨型卡片
        self.notes_text = QPlainTextEdit()
        self.notes_text.setObjectName("notesText")
        self.notes_text.setReadOnly(True)
        # 高度按内容行数自适应（150~520px），超长日志仍可在框内滚动
        self.notes_text.setMinimumHeight(150)
        self.notes_text.setMaximumHeight(520)
        self.notes_text.setFrameShape(QFrame.NoFrame)
        fnt = QFont("Microsoft YaHei UI")
        fnt.setPixelSize(13)
        self.notes_text.setFont(fnt)
        nl.addWidget(self.notes_title)
        nl.addWidget(self.notes_text)
        self.notes.setVisible(False)
        root.addWidget(self.notes)

        self.refresh()

    # ---------- 状态刷新 ----------
    def set_item(self, item) -> None:
        self.item = item
        self.refresh()

    def refresh(self) -> None:
        p = THEME.p
        it = self.item
        # 作用域样式：别让 QPlainTextEdit 的白色视口在深色主题里变成一块白板
        self.notes_text.setStyleSheet(
            f"#notesText{{background: {rgba(p.text, 0.04)}; border: none;"
            f"color: {p.text}; padding: 8px 10px;}}")
        self.name_lbl.setText(it.name)
        tint(self.name_lbl, p.text)
        if it.state == "running":
            pct = int(max(0.0, min(1.0, it.progress)) * 100)
            self.detail_lbl.setText(f"{it.message or '更新中'}… {pct}%")
        elif getattr(it, "scanning", False):
            self.detail_lbl.setText("正在检查新版本…")
        else:
            self.detail_lbl.setText(it.detail or it.path)
        self.detail_lbl.setToolTip(it.path)
        tint(self.detail_lbl, p.text_muted)
        scoped(self.avatar,
               f"background: {rgba(p.accent, 0.16)}; color: {p.accent};"
               f"border-radius: {p.radius_btn}px;")

        status_map = {
            "update": ("可更新", "accent"),
            "latest": ("已最新", "success"),
            "error": ("失败", "danger"),
            "skip": ("跳过", "warning"),
            "unbound": ("未绑定", "dim"),
            "choose": ("待选择", "warning"),
            "unknown": ("待确认", "warning"),
            "running": ("更新中", "accent"),
        }
        text, color = status_map.get(it.status, ("—", "dim"))
        if getattr(it, "scanning", False) and it.state == "idle":
            # 重启会自动触发检查；并发扫描期间每一行都应明确显示「检查中」
            text, color = "检查中", "accent"
        elif not it.scanned and it.state == "idle":
            text, color = "待检查", "dim"
        if it.state == "running":
            text, color = "更新中", "accent"
        elif it.state == "done":
            text, color = "已更新", "success"
        elif it.state == "failed":
            text, color = "失败", "danger"
        self.status_badge.setText(text)
        self.status_badge.set_color(color)

        # 版本变化
        if it.kind == "git":
            if it.local and it.remote and it.status == "update":
                self.ver_lbl.setText(
                    f"<span style='color:{p.text_muted}'>{it.local}</span>"
                    f"<span style='color:{p.text_muted}'>&nbsp;→&nbsp;</span>"
                    f"<span style='color:{p.accent};font-weight:600'>{it.remote}</span>")
            elif it.local:
                self.ver_lbl.setText(f"<span style='color:{p.text_muted}'>{it.local}</span>")
            else:
                self.ver_lbl.setText("")
        else:
            if it.status == "update" and it.local and it.remote:
                self.ver_lbl.setText(
                    f"<span style='color:{p.text_muted}'>{it.local}</span>"
                    f"<span style='color:{p.text_muted}'>&nbsp;→&nbsp;</span>"
                    f"<span style='color:{p.accent};font-weight:600'>{it.remote}</span>")
            elif it.remote:
                self.ver_lbl.setText(f"<span style='color:{p.text_dim}'>{it.remote}</span>")
            else:
                self.ver_lbl.setText("")

        has_notes = bool(it.notes)
        self.log_btn.setVisible(has_notes)
        if not has_notes and self._expanded:
            self.toggle_expand()
        if has_notes:
            head = it.notes.splitlines()[0].strip() if it.kind == "git" else ""
            self.notes_title.setText(
                f"更新日志 · {it.published}" if it.published and it.kind != "git"
                else ("提交记录" if it.kind == "git" else "更新日志"))
            self.notes_text.setPlainText(it.notes or "")
            # 按行数给高度：短日志不再挤成两三行，长日志最多 520px 后内部滚动
            lines = len((it.notes or "").splitlines())
            h = min(520, max(150, lines * 18 + 30))
            self.notes_text.setMinimumHeight(h)
            self.notes_text.setMaximumHeight(h)

        # 按钮状态
        if it.status == "unbound":
            self.action_btn.setText("绑定仓库")
            self.action_btn.setEnabled(True)
            try:
                self.action_btn.clicked.disconnect()
            except Exception:
                pass
            self.action_btn.clicked.connect(lambda: self.bind_requested.emit(self.item.key))
        else:
            if it.state == "failed":
                self.action_btn.setText("重试")
                self.action_btn.setObjectName("soft")
                self.action_btn.setEnabled(it.status != "skip")
            elif it.status == "skip":
                self.action_btn.setText("跳过")
                self.action_btn.setObjectName("ghost")
                self.action_btn.setEnabled(False)
            elif it.status == "latest" or it.state == "done":
                self.action_btn.setText("已最新")
                self.action_btn.setObjectName("ghost")
                self.action_btn.setEnabled(False)
            else:
                self.action_btn.setText("更新")
                self.action_btn.setObjectName("primary")
                self.action_btn.setEnabled(it.scanned and
                                           it.status in ("update", "unknown", "error")
                                           and it.state != "running")
            try:
                self.action_btn.clicked.disconnect()
            except Exception:
                pass
            self.action_btn.clicked.connect(lambda: self.update_requested.emit(self.item.key))
        self.action_btn.style().unpolish(self.action_btn)
        self.action_btn.style().polish(self.action_btn)
        self.update()

    def toggle_expand(self) -> None:
        self._expanded = not self._expanded
        self.notes.setVisible(self._expanded)
        self.log_btn.setText("收起" if self._expanded else "日志")
        self.update()

    # ---------- 绘制 ----------
    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.open_requested.emit(self.item.key)
        super().mouseDoubleClickEvent(e)

    # ---------- 右键菜单 ----------
    def contextMenuEvent(self, e):
        it = self.item
        m = ThemedMenu(self)
        busy = it.state == "running"
        act_update = m.addAction("立即更新")
        act_update.setEnabled(not busy and it.status in ("update", "unknown", "error")
                              and it.status != "unbound")
        act_undo = m.addAction("撤销上次更新")
        act_undo.setEnabled(not busy and it.kind != "git")   # git 仓库无备份，无法还原
        act_bind = None
        if it.status == "unbound" or not it.repo:
            act_bind = m.addAction("绑定 GitHub 仓库…")
        act_log = None
        if it.notes:
            act_log = m.addAction("收起更新日志" if self._expanded else "查看更新日志")
        act_repo = m.addAction("在浏览器里打开 GitHub 仓库" if it.repo
                               else "打开 GitHub 仓库（未绑定）")
        act_repo.setEnabled(bool(it.repo))
        act_mark = m.addAction("手动标记本地版本…")
        act_asset = m.addAction("下载文件筛选…")
        act_asset.setEnabled(it.kind != "git")
        act_preserve = m.addAction("更新时保留的文件夹…")
        act_preserve.setEnabled(it.kind != "git")
        m.addSeparator()
        act_open = m.addAction("打开所在文件夹")
        act_copy = m.addAction("复制文件夹路径")
        m.addSeparator()
        act_excl = m.addAction("排除此文件夹（不再检查）")

        act = m.exec(e.globalPos())
        if act is None:
            return
        if act is act_update:
            self.update_requested.emit(it.key)
        elif act is act_undo:
            self.undo_requested.emit(it.key)
        elif act_bind is not None and act is act_bind:
            self.bind_requested.emit(it.key)
        elif act_log is not None and act is act_log:
            self.toggle_expand()
        elif act is act_repo:
            self.repo_requested.emit(it.key)
        elif act is act_mark:
            self.mark_requested.emit(it.key)
        elif act is act_asset:
            self.asset_requested.emit(it.key)
        elif act is act_preserve:
            self.preserve_requested.emit(it.key)
        elif act is act_open:
            self.open_requested.emit(it.key)
        elif act is act_copy:
            QApplication.clipboard().setText(it.path)
        elif act is act_excl:
            self.exclude_requested.emit(it.key)

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path.addRoundedRect(rect, p.radius_card, p.radius_card)
        painter.fillPath(path, card_color(p, hover=self._hover))
        border_col = qc(p.accent, 0.35) if self.item.status == "update" else qc(p.border, p.border_alpha)
        painter.setPen(QPen(border_col, 1))
        painter.drawPath(path)
        painter.end()


class Toast(QWidget):
    """底部浮层提示。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_StyledBackground, True)   # 否则样式表背景不生效，露出深色调色板
        self.setObjectName("toast")
        self.setVisible(False)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 10)
        self.lbl = QLabel()
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(12)
        self.lbl.setFont(f)
        lay.addWidget(self.lbl)
        self.effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.effect)
        self._anim = QPropertyAnimation(self.effect, b"opacity", self)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._fade_out)
        # 可取消的隐藏定时器，避免残留回调把下一条 Toast 一起隐藏
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(lambda: self.setVisible(False))

    def show_text(self, text: str, ok: bool = True) -> None:
        p = THEME.p
        self.lbl.setText(text)
        tint(self.lbl, p.text)
        border = rgba(p.danger, 0.45) if not ok else rgba(p.border, 0.22)
        # 带 #toast 选择器：无选择器的规则会级联到子 QLabel，给文字画出一圈多余边框
        self.setStyleSheet(
            f"#toast{{background: {rgba(p.surface(), 0.97)};"
            f"border: 1px solid {border}; border-radius: {p.radius_btn + 4}px;}}")
        self.adjustSize()
        self.move((self.parentWidget().width() - self.width()) // 2,
                  self.parentWidget().height() - self.height() - 26)
        self.raise_()                 # 页面重建后可能盖住 Toast，置顶再显示
        self.effect.setOpacity(0.0)
        self.setVisible(True)
        self._hide_timer.stop()
        self._anim.stop()
        self._anim.setDuration(160)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        self._timer.start(2600)

    def _fade_out(self) -> None:
        self._anim.stop()
        self._anim.setDuration(260)
        self._anim.setStartValue(1.0)
        self._anim.setEndValue(0.0)
        self._anim.start()
        self._hide_timer.start(280)




class SegmentedControl(QFrame):
    """分段选择器：选中项整块填满（无内圈底色），仅整组外侧四角圆角。"""

    changed = Signal(str)
    HEIGHT = 32

    def __init__(self, items, current: str = "", parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.buttons: list[QPushButton] = []
        self.keys: list[str] = []
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)      # 不留内边距，选中色才能填满
        lay.setSpacing(0)
        for key, text in items:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setChecked(key == current)
            b.setProperty("segKey", key)
            b.clicked.connect(lambda _=False, k=key: self._pick(k))
            lay.addWidget(b)
            self.buttons.append(b)
            self.keys.append(key)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.restyle()

    def _pick(self, key: str) -> None:
        for k, b in zip(self.keys, self.buttons):
            b.setChecked(k == key)
        self.restyle()
        self.changed.emit(key)

    def value(self) -> str:
        for k, b in zip(self.keys, self.buttons):
            if b.isChecked():
                return k
        return self.keys[0] if self.keys else ""

    def set_value(self, key: str) -> None:
        for k, b in zip(self.keys, self.buttons):
            b.setChecked(k == key)
        self.restyle()

    def set_tooltip(self, key: str, text: str) -> None:
        for k, b in zip(self.keys, self.buttons):
            if k == key:
                b.setToolTip(text)

    def _btn_font(self, bold: bool) -> QFont:
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(12)
        f.setWeight(QFont.DemiBold if bold else QFont.Normal)
        return f

    def restyle(self) -> None:
        p = THEME.p
        r = max(8, min(p.radius_btn + 2, 14))
        n = len(self.buttons)
        total = 0
        for b in self.buttons:
            b.setFont(self._btn_font(False))
            fm = QFontMetrics(b.font())
            w = max(60, fm.horizontalAdvance(b.text()) + 34)
            b.setFixedSize(w, self.HEIGHT)   # 尺寸固定，切换皮肤不会跳动
            total += w
        self.setFixedSize(total, self.HEIGHT)
        for i, b in enumerate(self.buttons):
            active = b.isChecked()
            left = (f"border-top-left-radius:{r}px;border-bottom-left-radius:{r}px;"
                    if i == 0 else "border-top-left-radius:0;border-bottom-left-radius:0;")
            right = (f"border-top-right-radius:{r}px;border-bottom-right-radius:{r}px;"
                     if i == n - 1 else "border-top-right-radius:0;border-bottom-right-radius:0;")
            if active:
                bg, fg, fw = rgba(p.accent, 0.98), p.accent_on, "600"
            else:
                bg, fg, fw = "transparent", p.text_dim, "400"
            hover = rgba(p.text, 0.10) if not active else rgba(p.accent_hover, 1.0)
            b.setStyleSheet(
                f"QPushButton{{background:{bg};color:{fg};border:none;{left}{right}"
                f"padding:0;font-size:12px;font-weight:{fw};}}"
                f"QPushButton:hover{{background:{hover};}}")
        self.update()

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        r = max(8, min(p.radius_btn + 2, 14))
        path = QPainterPath()
        path.addRoundedRect(rect, r, r)
        painter.fillPath(path, qc(p.text, 0.06))
        painter.setPen(QPen(qc(p.border, 0.12 if p.dark else 0.09), 1))
        painter.drawPath(path)
        painter.end()


class _MenuPanel(QFrame):
    """弹出面板的圆角底板（玻璃质感，跟随主题）。"""

    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = max(8, min(p.radius_btn + 2, 14))
        path = paint_glass(painter, rect, radius, self.mapToGlobal(QPoint(0, 0)), p,
                           THEME.blur_active)
        painter.setPen(QPen(qc(p.border, 0.20), 1))
        painter.drawPath(path)
        painter.end()


class _PopupCard(QWidget):
    """主题化下拉弹层：圆角卡片 + 悬停高亮 + 选中打勾。"""

    picked = Signal(str)

    def __init__(self, items, current: str, width: int, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFixedWidth(max(width, 132))
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(0)
        panel = _MenuPanel(self)
        outer.addWidget(panel)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(5, 5, 5, 5)
        lay.setSpacing(1)
        for key, text in items:
            b = QPushButton(("✓   " if key == current else "      ") + text)
            b.setCursor(Qt.PointingHandCursor)
            b.setFixedHeight(30)
            b.setStyleSheet(self._btn_qss(key == current))
            b.clicked.connect(lambda _=False, k=key: self._pick(k))
            lay.addWidget(b)

    def _btn_qss(self, active: bool) -> str:
        p = THEME.p
        fg = p.accent if active else p.text
        return (f"QPushButton{{background:transparent;border:none;border-radius:7px;"
                f"text-align:left;padding:0 10px;font-family:'Microsoft YaHei UI';"
                f"font-size:12px;color:{fg};}}"
                f"QPushButton:hover{{background:{rgba(p.accent, 0.16)};color:{p.accent};}}")

    def _pick(self, key: str) -> None:
        self.picked.emit(key)
        self.close()

    def popup_at(self, anchor: QWidget) -> None:
        gp = anchor.mapToGlobal(QPoint(0, anchor.height() + 6))
        self.adjustSize()
        screen = anchor.screen().availableGeometry() if anchor.screen() else None
        x, y = gp.x(), gp.y()
        if screen is not None:
            if x + self.width() > screen.right():
                x = screen.right() - self.width() - 8
            if y + self.height() > screen.bottom():
                y = anchor.mapToGlobal(QPoint(0, 0)).y() - self.height() - 6
        BACKDROP.snapshot(anchor.screen())      # 先取样，弹层才透出背后画面
        self.move(x, y)
        self.show()


class ThemedCombo(QPushButton):
    """主题化下拉选择（替代原生 QComboBox，避免白底系统菜单）。"""

    changed = Signal(str)

    def __init__(self, items, current: str = "", width: int = 132, parent=None):
        super().__init__(parent)
        self._items = list(items)
        self._map = dict(self._items)
        self._key = current if current in self._map else (self._items[0][0] if self._items else "")
        self._w = width
        self._popup = None
        self.setFixedSize(width, 32)
        self.setCursor(Qt.PointingHandCursor)
        self.clicked.connect(self._open)
        self._restyle()

    def value(self) -> str:
        return self._key

    def set_value(self, key: str, emit: bool = False) -> None:
        if key not in self._map:
            return
        self._key = key
        self._restyle()
        if emit:
            self.changed.emit(key)

    def set_items(self, items) -> None:
        self._items = list(items)
        self._map = dict(self._items)
        if self._key not in self._map and self._items:
            self._key = self._items[0][0]
        self._restyle()

    def _restyle(self) -> None:
        p = THEME.p
        self.setText(self._map.get(self._key, ""))
        self.setStyleSheet(
            f"QPushButton{{background:{rgba(p.text, 0.07)};border:1px solid {rgba(p.border, 0.13)};"
            f"border-radius:8px;color:{p.text_dim};font-family:'Microsoft YaHei UI';"
            f"font-size:12px;text-align:left;padding-left:11px;padding-right:24px;}}"
            f"QPushButton:hover{{background:{rgba(p.text, 0.11)};color:{p.text};}}")

    def refresh_theme(self) -> None:
        """主题切换后重刷配色（与 Chip/SearchBox 保持同名协议）。"""
        self._restyle()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        x = self.width() - 15
        y = self.height() / 2 - 1
        painter.setPen(QPen(qc(p.text_dim, 0.9), 1.4, Qt.SolidLine, Qt.RoundCap))
        painter.drawLine(QPointF(x - 4, y - 1), QPointF(x, y + 3))
        painter.drawLine(QPointF(x, y + 3), QPointF(x + 4, y - 1))
        painter.end()

    def _open(self) -> None:
        self._popup = _PopupCard(self._items, self._key, self.width() + 26, self)
        self._popup.picked.connect(self._on_pick)
        self._popup.popup_at(self)

    def _on_pick(self, key: str) -> None:
        if key == self._key:
            return
        self._key = key
        self._restyle()
        self.changed.emit(key)


class NumberStepper(QWidget):
    """主题化数值步进器：右侧上下两个小箭头（竖向排列），替代原生 QSpinBox。"""

    valueChanged = Signal(int)
    BTN_W = 24

    def __init__(self, value: int = 0, minimum: int = 0, maximum: int = 100,
                 step: int = 1, suffix: str = "", width: int = 104, parent=None):
        super().__init__(parent)
        self._min, self._max, self._step = int(minimum), int(maximum), max(1, int(step))
        self._suffix = suffix
        self._value = max(self._min, min(self._max, int(value)))
        self._zone = 0                      # 0 无 / 1 加 / -1 减
        self.setFixedSize(width, 32)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)

        # 数值与单位共用一套字体，编辑框文字 / 单位绘制才能对齐
        self._num_font = QFont("Microsoft YaHei UI")
        self._num_font.setPixelSize(12)
        self._suffix_w = (QFontMetrics(self._num_font).horizontalAdvance(suffix) + 4
                          if suffix else 0)
        self._suffix_x = 0.0

        # 键入编辑框：点数值区 / 直接打数字弹出，回车或失焦提交，Esc 取消。
        # 背景透明、占满数值区，单位由 paintEvent 紧跟输入文字绘制（不会被盖住），
        # 编辑态的高亮框也由 paintEvent 画。
        self.editor = QLineEdit(self)
        self.editor.setFont(self._num_font)
        self.editor.setFrame(False)
        self.editor.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.editor.setVisible(False)
        self.editor.installEventFilter(self)
        self.editor.editingFinished.connect(self._commit_editor)
        self.editor.textChanged.connect(self._layout_editor)
        scoped(self.editor, "background: transparent;"
                            f"color: {rgba(THEME.p.text, 1.0)};"
                            "border: none; padding: 0 2px 0 0;")

    # ---------- 值 ----------
    def value(self) -> int:
        return self._value

    def setValue(self, v: int, emit: bool = False) -> None:
        v = max(self._min, min(self._max, int(v)))
        if v == self._value:
            return
        self._value = v
        self.update()
        if emit:
            self.valueChanged.emit(v)

    def setRange(self, lo: int, hi: int) -> None:
        self._min, self._max = int(lo), int(hi)
        self.setValue(self._value)

    # ---------- 几何 ----------
    def _rects(self):
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        bw = self.BTN_W
        up = QRectF(r.right() - bw, r.top(), bw, r.height() / 2)
        down = QRectF(r.right() - bw, r.center().y(), bw, r.height() / 2)
        return r, up, down

    def _content_rect(self) -> QRectF:
        """数值 / 单位可用区域（扣掉右侧箭头与内边距）。"""
        r, _, _ = self._rects()
        return QRectF(r.left() + 6, r.top() + 2,
                      r.width() - self.BTN_W - 10, r.height() - 4)

    def _layout_editor(self, *_):
        """编辑框占满数值区，单位位置紧跟输入文字（最右不超出数值区）。"""
        if not self.editor.isVisible():
            return
        c = self._content_rect()
        self.editor.setGeometry(int(c.left()), int(c.top()),
                                int(c.width() - 2), int(c.height()))
        tw = QFontMetrics(self._num_font).horizontalAdvance(self.editor.text() or "0")
        self._suffix_x = min(c.left() + 4 + tw + 3,
                             c.right() - self._suffix_w - 2)
        self.update()

    def _hit(self, pos) -> int:
        _, up, down = self._rects()
        if up.contains(pos):
            return 1
        if down.contains(pos):
            return -1
        return 0

    # ---------- 交互 ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            z = self._hit(e.position())
            if z:
                self._close_editor()
                self.setValue(self._value + z * self._step, emit=True)
                return
            self._open_editor()          # 点数值区直接键入
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        z = self._hit(e.position())
        if z != self._zone:
            self._zone = z
            self.update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self._zone = 0
        self.update()
        super().leaveEvent(e)

    def wheelEvent(self, e):
        self.setValue(self._value + (self._step if e.angleDelta().y() > 0 else -self._step),
                      emit=True)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Up, Qt.Key_Right):
            self.setValue(self._value + self._step, emit=True)
        elif e.key() in (Qt.Key_Down, Qt.Key_Left):
            self.setValue(self._value - self._step, emit=True)
        elif e.text() and e.text().isdigit():
            self._open_editor(initial=e.text())
        else:
            super().keyPressEvent(e)

    # ---------- 键入编辑 ----------
    def _open_editor(self, initial: str | None = None) -> None:
        self.editor.setVisible(True)
        self.editor.setText(initial if initial else str(self._value))
        self._layout_editor()
        self.editor.setFocus()
        if initial:
            # 不用 QLineEdit.end()：PySide6 里它要求显式 bool 参数
            self.editor.setCursorPosition(len(self.editor.text()))
        else:
            self.editor.selectAll()

    def _commit_editor(self) -> None:
        if not self.editor.isVisible():
            return
        txt = self.editor.text().strip()
        self.editor.setVisible(False)
        self.update()
        try:
            v = int(float(txt)) if txt else self._value
        except ValueError:
            v = self._value
        if self._min <= v <= self._max and v != self._value:
            self.setValue(v, emit=True)

    def _close_editor(self) -> None:
        if self.editor.isVisible():
            self.editor.setVisible(False)
            self.update()

    def eventFilter(self, obj, ev) -> bool:
        if obj is self.editor and ev.type() == QEvent.KeyPress \
                and ev.key() == Qt.Key_Escape:
            self._close_editor()
            return True
        return super().eventFilter(obj, ev)

    # ---------- 绘制 ----------
    def paintEvent(self, e):
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        r, up, down = self._rects()
        radius = 8
        path = QPainterPath()
        path.addRoundedRect(r, radius, radius)
        painter.fillPath(path, qc(p.text, 0.07))
        painter.setPen(QPen(qc(p.border, 0.13), 1))
        painter.drawPath(path)

        # 两个箭头的悬停底色
        painter.setPen(Qt.NoPen)
        for zone, rect in ((1, up), (-1, down)):
            if self._zone == zone:
                hl = QPainterPath()
                hl.addRoundedRect(rect.adjusted(1, 1, -1, -1), radius - 3, radius - 3)
                painter.fillPath(hl, qc(p.accent, 0.16))

        # 分隔线
        painter.setPen(QPen(qc(p.border, 0.12), 1))
        painter.drawLine(QPointF(r.right() - self.BTN_W, r.top() + 4),
                         QPointF(r.right() - self.BTN_W, r.bottom() - 4))

        # 数值：编辑时交给透明的 QLineEdit 画（避免叠影），单位由这里紧跟输入文字绘制，
        # 所以键入过程中「分钟 / MB」等后缀不会消失，也不会被编辑框背景盖住。
        painter.setFont(self._num_font)
        painter.setPen(QPen(qc(p.text)))
        if self.editor.isVisible():
            hl = QPainterPath()
            hl.addRoundedRect(self._content_rect().adjusted(0, 0, -2, 0), 5, 5)
            painter.fillPath(hl, qc(p.accent, 0.10))
            if self._suffix:
                painter.drawText(
                    QRectF(self._suffix_x, r.top(), self._suffix_w + 2, r.height()),
                    Qt.AlignLeft | Qt.AlignVCenter, self._suffix)
        else:
            painter.drawText(
                QRectF(r.left() + 11, r.top(), r.width() - self.BTN_W - 14, r.height()),
                Qt.AlignLeft | Qt.AlignVCenter, f"{self._value}{self._suffix}")

        # 上下箭头
        cx = up.center().x()
        for sign, rect in ((1, up), (-1, down)):
            col = qc(p.accent if self._zone == sign else p.text_dim, 0.95)
            painter.setPen(QPen(col, 1.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            cy = rect.center().y()
            tip = cy - 3.4 * sign
            painter.drawLine(QPointF(cx - 3.6, cy + 1.6 * sign), QPointF(cx, tip))
            painter.drawLine(QPointF(cx, tip), QPointF(cx + 3.6, cy + 1.6 * sign))
        painter.end()


class ChevronButton(QPushButton):
    """自绘展开箭头（不依赖图标字体）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._up = False
        self.setFixedSize(26, 26)
        self.setCursor(Qt.PointingHandCursor)
        scoped(self, "background: transparent; border: none;")

    def set_up(self, on: bool) -> None:
        self._up = on
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        p = THEME.p
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        if self.underMouse():
            painter.setPen(Qt.NoPen)
            painter.setBrush(qc(p.text, 0.08))
            painter.drawEllipse(c, 11, 11)
        painter.setPen(QPen(qc(p.text_muted if not self.underMouse() else p.text, 0.95), 1.6,
                            Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        d = 2.0 if self._up else -2.0
        painter.drawLine(QPointF(c.x() - 4, c.y() + d), QPointF(c.x(), c.y() - d))
        painter.drawLine(QPointF(c.x(), c.y() - d), QPointF(c.x() + 4, c.y() + d))
        painter.end()


class _ClickRow(QWidget):
    """可点击的行容器（点哪都算）。"""

    clicked = Signal()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class ExpandSetting(Card):
    """可展开的设置卡片：标题行常显，点箭头/整行展开详细设置。"""

    toggled = Signal(bool)

    def __init__(self, icon: str, title: str, desc: str = "", control=None,
                 body=None, parent=None):
        super().__init__(parent, padding=0)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._open = False

        wrap = QVBoxLayout()
        wrap.setContentsMargins(0, 0, 0, 0)
        wrap.setSpacing(0)
        self.lay.addLayout(wrap)

        head = _ClickRow()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 13, 12, 13)
        hl.setSpacing(13)

        self.icon = Glyph(icon, 18, THEME.p.text_dim)
        self.icon.setFixedSize(26, 26)
        hl.addWidget(self.icon, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(2)
        self.title_lbl = QLabel(title)
        ft = QFont("Microsoft YaHei UI")
        ft.setPixelSize(13)
        ft.setWeight(QFont.DemiBold)
        self.title_lbl.setFont(ft)
        tint(self.title_lbl, THEME.p.text)
        col.addWidget(self.title_lbl)
        # WrapLabel：窗口变窄折行时不会被裁掉半行（普通 QLabel 只按单行高度分配）
        self.desc_lbl = WrapLabel(desc, self)
        fd = QFont("Microsoft YaHei UI")
        fd.setPixelSize(11)
        self.desc_lbl.setFont(fd)
        tint(self.desc_lbl, THEME.p.text_muted)
        col.addWidget(self.desc_lbl)
        self.desc_lbl.setVisible(bool(desc))    # 有父级之后再显示，否则会变成顶层小窗
        hl.addLayout(col, 1)

        self.control = control
        if control is not None:
            hl.addWidget(control, 0, Qt.AlignVCenter)

        self.chevron = ChevronButton()
        self.chevron.clicked.connect(self.toggle)
        hl.addWidget(self.chevron, 0, Qt.AlignVCenter)

        head.clicked.connect(self.toggle)
        wrap.addWidget(head)

        self.body = body or QWidget()
        flat(self.body)
        self.body_wrap = QWidget()
        flat(self.body_wrap)
        bl = QVBoxLayout(self.body_wrap)
        bl.setContentsMargins(16, 2, 16, 14)
        bl.setSpacing(0)
        bl.addWidget(self.body)
        self.body_wrap.setVisible(False)
        self.body_wrap.setMaximumHeight(0)
        wrap.addWidget(self.body_wrap)

    def _restyle_chevron(self) -> None:
        self.chevron.set_up(self._open)

    def is_open(self) -> bool:
        return self._open

    def toggle(self) -> None:
        self.set_open(not self._open)
        self.toggled.emit(self._open)

    def set_open(self, on: bool) -> None:
        self._open = on
        self._restyle_chevron()
        self.body_wrap.setVisible(on)
        if on:
            self.body_wrap.setMaximumHeight(self.body.sizeHint().height() + 24)
        else:
            self.body_wrap.setMaximumHeight(0)

    def set_summary(self, text: str) -> None:
        if self.desc_lbl is not None:
            self.desc_lbl.setText(text)
            self.desc_lbl.setVisible(bool(text))

    def refresh_theme(self) -> None:
        """主题/配色变化后重刷标题、说明与图标（chevron 自绘读动态色，无需刷）。"""
        p = THEME.p
        self.icon.set_color(p.text_dim)
        tint(self.title_lbl, p.text)
        tint(self.desc_lbl, p.text_muted)
        self.chevron.update()


class SettingRow(Card):
    """设置项行：左图标 + 标题/说明 + 右侧控件，可选下方详细内容区。"""

    def __init__(self, icon: str = "", title: str = "", desc: str = "",
                 control=None, content=None, parent=None):
        super().__init__(parent, padding=0)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        body = QVBoxLayout()
        body.setContentsMargins(16, 13, 16, 13)
        body.setSpacing(10)
        self.lay.addLayout(body)

        line = QHBoxLayout()
        line.setSpacing(13)
        self.icon = None
        if icon:
            self.icon = Glyph(icon, 18, THEME.p.text_dim)
            self.icon.setFixedSize(26, 26)
            line.addWidget(self.icon, 0, Qt.AlignVCenter)

        col = QVBoxLayout()
        col.setSpacing(2)
        self.title_lbl = QLabel(title)
        ft = QFont("Microsoft YaHei UI")
        ft.setPixelSize(13)
        ft.setWeight(QFont.DemiBold)
        self.title_lbl.setFont(ft)
        tint(self.title_lbl, THEME.p.text)
        col.addWidget(self.title_lbl)
        # WrapLabel：窗口变窄折行时不会被裁掉半行（普通 QLabel 只按单行高度分配）
        self.desc_lbl = WrapLabel(desc, self)
        fd = QFont("Microsoft YaHei UI")
        fd.setPixelSize(11)
        self.desc_lbl.setFont(fd)
        tint(self.desc_lbl, THEME.p.text_muted)
        col.addWidget(self.desc_lbl)
        self.desc_lbl.setVisible(bool(desc))    # 有父级之后再显示，否则会变成顶层小窗
        line.addLayout(col, 1)

        if control is not None:
            line.addWidget(control, 0, Qt.AlignVCenter)
        body.addLayout(line)

        if content is not None:
            body.addWidget(content)
        self.setMinimumHeight(58)

    def set_desc(self, text: str) -> None:
        self.desc_lbl.setText(text)
        self.desc_lbl.setVisible(bool(text))

    def refresh_theme(self) -> None:
        """主题/配色变化后重刷标题、说明与图标的文字颜色。"""
        p = THEME.p
        if self.icon is not None:
            self.icon.set_color(p.text_dim)
        tint(self.title_lbl, p.text)
        tint(self.desc_lbl, p.text_muted)


class SettingGroup(QLabel):
    """设置分组标题（行列表上方的小标题）。"""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        f = QFont("Microsoft YaHei UI")
        f.setPixelSize(12)
        f.setWeight(QFont.DemiBold)
        self.setFont(f)
        self.setContentsMargins(4, 8, 0, 0)
        tint(self, THEME.p.text_dim)

    def refresh_theme(self) -> None:
        tint(self, THEME.p.text_dim)


class TabButton(QPushButton):
    """设置页顶部分页标签（带下划线指示）。"""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlat(True)
        self.setFixedHeight(34)
        self._apply()

    def _apply(self) -> None:
        p = THEME.p
        on = self.isChecked()
        self.setStyleSheet(
            f"QPushButton{{background: transparent; border: none;"
            f" border-bottom: 2px solid {p.accent if on else 'transparent'};"
            f" color: {p.text if on else p.text_muted};"
            f" font-size: 13px; font-weight: {'600' if on else '400'};"
            f" padding: 0 2px 0 2px;}}"
            f"QPushButton:hover{{color: {p.text};}}")

    def set_checked(self, on: bool) -> None:
        self.setChecked(on)
        self._apply()


def hline(p=None) -> QFrame:
    p = p or THEME.p
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"QFrame{{background: {rgba(p.border, p.border_alpha)}; border: none;}}")
    return line


def vspace(h: int) -> QWidget:
    w = QWidget()
    w.setFixedHeight(h)
    return w
