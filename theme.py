# -*- coding: utf-8 -*-
"""三套皮肤：亚克力玻璃 / 莫奈(Material You) / Miuix(澎湃OS)，各含浅色与深色。"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor


def rgba(hex_color: str, alpha: float) -> str:
    c = QColor(hex_color)
    return f"rgba({c.red()},{c.green()},{c.blue()},{alpha:.3f})"


def qc(hex_color: str, alpha: float = 1.0) -> QColor:
    c = QColor(hex_color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


@dataclass(frozen=True)
class Palette:
    id: str
    label: str
    dark: bool
    bg: str
    card: str
    card_alt: str
    sidebar: str
    border: str
    text: str
    text_dim: str
    text_muted: str
    accent: str
    accent_hover: str
    accent_on: str
    accent_soft: str
    success: str
    warning: str
    danger: str
    radius_card: int
    radius_btn: int
    radius_chip: int
    card_alpha: float
    border_alpha: float
    window_alpha: float
    support_blur: bool

    # --- 便捷访问 ---
    def color(self, key: str, alpha: float = 1.0) -> QColor:
        return qc(getattr(self, key), alpha)

    def soft(self, key: str, alpha: float = 0.16) -> QColor:
        return qc(getattr(self, key), alpha)

    def surface(self) -> QColor:
        """不透明表面色（菜单、弹层用，不能是半透明的 card）。"""
        return QColor(self.bg).lighter(122) if self.dark else QColor("#FFFFFF")


# ------------------------------------------------------------------ 调色板

ACRYLIC_DARK = Palette(
    id="acrylic", label="亚克力", dark=True,
    bg="#15171C", card="#FFFFFF", card_alt="#FFFFFF", sidebar="#FFFFFF",
    border="#FFFFFF", text="#EEF1F7", text_dim="#BCC4D2", text_muted="#929AA9",
    accent="#5B8CFF", accent_hover="#7AA2FF", accent_on="#08101F",
    accent_soft="#5B8CFF", success="#43C58A", warning="#F2B23E", danger="#F0655A",
    radius_card=14, radius_btn=9, radius_chip=9,
    card_alpha=0.085, border_alpha=0.10, window_alpha=0.72, support_blur=True,
)

ACRYLIC_LIGHT = Palette(
    id="acrylic", label="亚克力", dark=False,
    bg="#F5F7FB", card="#FFFFFF", card_alt="#FFFFFF", sidebar="#FFFFFF",
    border="#0D1B2A", text="#14181F", text_dim="#49515F", text_muted="#6B7381",
    accent="#2F6BFF", accent_hover="#1E5AF0", accent_on="#FFFFFF",
    accent_soft="#2F6BFF", success="#12996A", warning="#C77C10", danger="#D8453A",
    radius_card=14, radius_btn=9, radius_chip=9,
    card_alpha=0.66, border_alpha=0.075, window_alpha=0.80, support_blur=True,
)

MONET_DARK = Palette(
    id="monet", label="莫奈", dark=True,
    bg="#141218", card="#211F26", card_alt="#2B2830", sidebar="#1B1920",
    border="#CAC4D0", text="#E9E3EC", text_dim="#D2CCDB", text_muted="#A8A2B1",
    accent="#D0BCFF", accent_hover="#E4D6FF", accent_on="#2A1B4E",
    accent_soft="#D0BCFF", success="#8DD6A3", warning="#F2C86B", danger="#FFB4A9",
    radius_card=26, radius_btn=22, radius_chip=18,
    card_alpha=0.90, border_alpha=0.16, window_alpha=0.86, support_blur=True,
)

MONET_LIGHT = Palette(
    id="monet", label="莫奈", dark=False,
    bg="#FDF7FF", card="#FFFFFF", card_alt="#F3EDF7", sidebar="#F7F1FB",
    border="#79747E", text="#1D1B20", text_dim="#443F49", text_muted="#605A64",
    accent="#6750A4", accent_hover="#7B62BD", accent_on="#FFFFFF",
    accent_soft="#E8DEF8", success="#2E7D46", warning="#8A6100", danger="#B3261E",
    radius_card=26, radius_btn=22, radius_chip=18,
    card_alpha=0.92, border_alpha=0.13, window_alpha=0.90, support_blur=True,
)

MIUIX_DARK = Palette(
    id="miuix", label="Miuix", dark=True,
    bg="#101012", card="#1D1D20", card_alt="#26262A", sidebar="#17171A",
    border="#FFFFFF", text="#F0F0F2", text_dim="#BCBCC4", text_muted="#95959D",
    accent="#3482FF", accent_hover="#4E93FF", accent_on="#FFFFFF",
    accent_soft="#3482FF", success="#37C46B", warning="#FFB020", danger="#FF5B4E",
    radius_card=20, radius_btn=16, radius_chip=14,
    card_alpha=0.96, border_alpha=0.06, window_alpha=0.97, support_blur=False,
)

MIUIX_LIGHT = Palette(
    id="miuix", label="Miuix", dark=False,
    bg="#F1F1F5", card="#FFFFFF", card_alt="#F7F7FA", sidebar="#F7F7FA",
    border="#000000", text="#0D0D0F", text_dim="#54545C", text_muted="#6C6C75",
    accent="#0B6CFF", accent_hover="#0A5FD8", accent_on="#FFFFFF",
    accent_soft="#0B6CFF", success="#15A854", warning="#C77A00", danger="#E23B2E",
    radius_card=20, radius_btn=16, radius_chip=14,
    card_alpha=0.98, border_alpha=0.055, window_alpha=0.99, support_blur=False,
)

PALETTES = {
    ("acrylic", True): ACRYLIC_DARK, ("acrylic", False): ACRYLIC_LIGHT,
    ("monet", True): MONET_DARK, ("monet", False): MONET_LIGHT,
    ("miuix", True): MIUIX_DARK, ("miuix", False): MIUIX_LIGHT,
}

PRESET_ORDER = ["acrylic", "monet", "miuix"]
PRESET_LABELS = {"acrylic": "亚克力", "monet": "莫奈", "miuix": "Miuix"}
PRESET_HINTS = {
    "acrylic": "半透明玻璃，毛玻璃模糊",
    "monet": "Material You 取色，大圆角柔和",
    "miuix": "澎湃 OS 风格，分组卡片与胶囊按钮",
}


# ------------------------------------------------------------------ 当前主题

class ThemeState:
    """全局主题状态：皮肤 / 明暗 / 模糊开关。"""

    def __init__(self) -> None:
        self.preset = "acrylic"
        self.mode = "auto"          # auto | light | dark
        self.blur = True
        self.system_dark = True
        self._p = PALETTES[("acrylic", True)]

    # --- 解析 ---
    @property
    def is_dark(self) -> bool:
        if self.mode == "auto":
            return self.system_dark
        return self.mode == "dark"

    @property
    def p(self) -> Palette:
        return self._p

    def refresh(self) -> None:
        self._p = PALETTES[(self.preset, self.is_dark)]

    # --- 设置 ---
    def set_preset(self, preset: str) -> None:
        self.preset = preset if preset in PRESET_ORDER else "acrylic"
        self.refresh()

    def set_mode(self, mode: str) -> None:
        self.mode = mode if mode in ("auto", "light", "dark") else "auto"
        self.refresh()

    def set_system_dark(self, dark: bool) -> None:
        self.system_dark = dark
        if self.mode == "auto":
            self.refresh()

    def set_blur(self, on: bool) -> None:
        self.blur = on

    @property
    def blur_active(self) -> bool:
        return self.blur and self.p.support_blur


THEME = ThemeState()


def _px_radius(n: int) -> str:
    return f"{n}px"


def btn_radius(p) -> int:
    """按钮圆角上限：超过控件高度一半时 Qt 会退化成直角，统一夹到 14。"""
    return max(6, min(p.radius_btn, 14))


def field_radius(p) -> int:
    return max(7, min(p.radius_btn - 2, 12))


def build_qss(p: Palette) -> str:
    """根据调色板生成全局样式表。"""
    return f"""
    QWidget {{
        color: {p.text};
        font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    }}
    QLabel {{ background: transparent; }}
    QToolTip {{
        background: {p.surface().name()};
        color: {p.text};
        border: 1px solid {rgba(p.border, 0.18)};
        border-radius: 8px;
        padding: 6px 9px;
    }}

    /* ---------- 按钮 ---------- */
    QPushButton {{
        background: transparent;
        border: none;
        padding: 7px 14px;
        border-radius: {_px_radius(btn_radius(p))};
        color: {p.text};
        font-size: 13px;
    }}
    QPushButton:hover {{ background: {rgba(p.text, 0.07)}; }}
    QPushButton:pressed {{ background: {rgba(p.text, 0.12)}; }}
    QPushButton:disabled {{ color: {p.text_muted}; }}

    QPushButton#primary {{
        background: {p.accent};
        color: {p.accent_on};
        font-weight: 600;
        padding: 9px 18px;
    }}
    QPushButton#primary:hover {{ background: {p.accent_hover}; }}
    QPushButton#primary:disabled {{ background: {rgba(p.text, 0.09)}; color: {p.text_muted}; }}

    QPushButton#soft {{
        background: {rgba(p.accent, 0.14)};
        color: {p.accent if not p.dark else p.accent};
        padding: 8px 16px;
    }}
    QPushButton#soft:hover {{ background: {rgba(p.accent, 0.22)}; }}
    QPushButton#soft:disabled {{ background: {rgba(p.text, 0.05)}; color: {p.text_muted}; }}

    QPushButton#ghost {{
        background: {rgba(p.text, 0.055)};
        padding: 8px 16px;
    }}
    QPushButton#ghost:hover {{ background: {rgba(p.text, 0.10)}; }}

    QPushButton#danger {{
        background: {rgba(p.danger, 0.16)};
        color: {p.danger};
        padding: 8px 16px;
    }}
    QPushButton#danger:hover {{ background: {rgba(p.danger, 0.26)}; }}

    QPushButton#link {{
        color: {p.accent};
        padding: 2px 4px;
        text-decoration: none;
    }}
    QPushButton#link:hover {{ color: {p.accent_hover}; }}

    /* ---------- 输入框 ---------- */
    QLineEdit, QSpinBox {{
        background: {rgba(p.text, 0.05) if p.dark else rgba(p.text, 0.035)};
        border: 1px solid {rgba(p.border, 0.14)};
        border-radius: {_px_radius(field_radius(p))};
        padding: 7px 11px;
        selection-background-color: {rgba(p.accent, 0.45)};
        selection-color: {p.text};
    }}
    QLineEdit:focus, QSpinBox:focus {{ border: 1px solid {rgba(p.accent, 0.65)}; }}
    QLineEdit#search {{
        background: {rgba(p.text, 0.05) if p.dark else rgba(p.text, 0.04)};
        border-radius: {_px_radius(field_radius(p))};
        /* 上下内边距必须为 0，否则文字会被顶到偏下的位置，和左侧图标不居中 */
        padding: 0px 12px 0px 34px;
        font-size: 13px;
    }}

    QComboBox {{
        background: {rgba(p.text, 0.05) if p.dark else rgba(p.text, 0.035)};
        border: 1px solid {rgba(p.border, 0.14)};
        border-radius: {_px_radius(field_radius(p))};
        padding: 6px 11px;
        min-width: 92px;
    }}
    QComboBox:hover {{ background: {rgba(p.text, 0.08)}; }}
    QComboBox::drop-down {{ border: none; width: 22px; }}
    QComboBox QAbstractItemView {{
        background: {p.card_alt};
        border: 1px solid {rgba(p.border, 0.18)};
        border-radius: 10px;
        padding: 4px;
        outline: none;
        selection-background-color: {rgba(p.accent, 0.22)};
    }}

    /* ---------- 滚动区 ---------- */
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 2px 2px 2px 0;
    }}
    QScrollBar::handle:vertical {{
        background: {rgba(p.text, 0.18)};
        border-radius: 4px; min-height: 32px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {rgba(p.text, 0.30)}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; }}
    QScrollBar::handle:horizontal {{ background: {rgba(p.text, 0.18)}; border-radius: 4px; min-width: 32px; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    /* ---------- 其它 ---------- */
    QCheckBox {{ spacing: 8px; }}
    QCheckBox::indicator {{
        width: 18px; height: 18px; border-radius: 6px;
        border: 1px solid {rgba(p.border, 0.28)};
        background: transparent;
    }}
    QCheckBox::indicator:checked {{
        background: {p.accent}; border: 1px solid {p.accent};
    }}
    QSlider::groove:horizontal {{ height: 4px; background: {rgba(p.text, 0.14)}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {p.accent}; border-radius: 2px; }}
    QSlider::handle:horizontal {{
        width: 14px; height: 14px; margin: -6px 0; border-radius: 7px;
        background: #FFFFFF; border: 1px solid {rgba(p.border, 0.25)};
    }}
    QDialog {{ background: {p.bg}; }}
    """


def card_color(p: Palette, hover: bool = False, alpha_scale: float = 1.0):
    """卡片底色：玻璃皮肤=白色薄层；实色皮肤=卡片本色。

    card_alpha 很低（<=0.15）说明是「玻璃叠层」风格，用白色薄层提亮；
    否则是实心卡片（莫奈 / Miuix），必须用 palettes 里定义的卡片色。
    """
    alpha = (p.card_alpha if THEME.blur_active else 1.0) * alpha_scale
    if p.dark and p.card_alpha <= 0.15:
        if hover:
            alpha = min(1.0, alpha + 0.02)
        return qc("#FFFFFF", alpha)
    return qc(p.card_alt if hover else p.card, min(1.0, alpha))


def contrast_text(hex_color: str) -> str:
    c = QColor(hex_color)
    lum = (0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()) / 255
    return "#111318" if lum > 0.6 else "#FFFFFF"


GLYPH = {
    "home": "\uE80F",
    "apps": "\uE71D",
    "settings": "\uE713",
    "refresh": "\uE72C",
    "download": "\uE896",
    "check": "\uE73E",
    "warning": "\uE7BA",
    "error": "\uEA39",
    "chevron_down": "\uE70D",
    "chevron_up": "\uE70E",
    "close": "\uE711",
    "min": "\uE921",
    "max": "\uE922",
    "restore": "\uE923",
    "folder": "\uE8B7",
    "filter": "\uE71C",
    "add": "\uE710",
    "delete": "\uE74D",
    "info": "\uE946",
    "link": "\uE71B",
    "stop": "\uE71A",
    "search": "\uE721",
    "brush": "\uE790",
    "bright": "\uE706",
    "moon": "\uE708",
    "history": "\uE81C",
    "palette": "\uE790",
    "funnel": "\uE71C",
    "clock": "\uE823",
    "timer": "\uE916",
    "save": "\uE74E",
    "notify": "\uE8BD",
    "rocket": "\uE7E6",
    "chat": "\uE9A1",
    "cloud": "\uE753",
}
