# -*- coding: utf-8 -*-
"""三个极简窗口：设置面板 / 迷你状态面板 / AI 对话窗。"""
import os
import shutil
import tempfile
import time

from PyQt6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QGridLayout,
    QCheckBox, QComboBox, QSlider, QLabel, QLineEdit, QPushButton, QTabWidget,
    QProgressBar, QTextEdit, QKeySequenceEdit, QGroupBox, QScrollArea,
    QColorDialog, QMessageBox, QFrame, QFileDialog, QInputDialog,
    QPlainTextEdit, QStackedWidget, QSpinBox, QApplication,
)
from PyQt6.QtCore import Qt, pyqtSignal, QUrl, QTimer, QSize, QThread
from PyQt6.QtGui import QKeySequence, QColor, QPixmap, QDesktopServices, QPainter
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest

from . import ai as ai_mod
from . import config as cfgmod
from . import diary as diary_mod
from . import winapi
from .link import PAWLIVEWALL_MUTEX
from .assets import ACTIONS, SOUNDS_DIR, CURSOR_DIR, AssetIndex
from .drops import DROP_LIBRARY

# PawLiveWall（动态壁纸）发布页地址：发布后把链接填到这里，
# 「联动视效」的下载提示与「下载 PawLiveWall」按钮会自动打开它。
PAWLIVEWALL_URL = ""


class ToggleSwitch(QCheckBox):
    """滑动开关样式（点击切换），兼容 QCheckBox 的 isChecked/setChecked/toggled。"""

    def __init__(self, text=""):
        super().__init__("")
        self._w, self._h = 56, 28
        self.setFixedSize(self._w, self._h)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def hitButton(self, pos):
        return self.contentsRect().contains(pos)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._h / 2
        on = self.isChecked()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#ff96bb") if on else QColor("#c8c8c8"))
        p.drawRoundedRect(0, 0, self._w, self._h, r, r)
        x = (self._w - self._h) if on else 0
        p.setBrush(QColor("white"))
        p.drawEllipse(int(x) + 2, 2, self._h - 4, self._h - 4)
        p.end()

    def sizeHint(self):
        return QSize(self._w, self._h)

_ITEM_EMOJI = {name: emoji for name, emoji in DROP_LIBRARY}

def _color_family(base, dark=False):
    """从主题色 HSV 派生一整套配色，返回 dict。

    dark=False 浅色：白底深字；dark=True 深色：深底浅字。
    """
    c = QColor(base)
    if not c.isValid():
        c = QColor("#ff96bb")
    h, s, v = c.getHsv()[:3]

    def hsv(hh, ss, vv):
        return QColor.fromHsv(int(hh) % 360, max(0, min(255, int(ss))),
                              max(0, min(255, int(vv)))).name()
    if dark:
        # —— 深色模式：深底浅字，控件边框提亮、文字用浅色 ——
        return {
            "main":       hsv(h, max(35, s), min(255, v + 20)),
            "dark":       hsv(h, min(255, s + 60), 30),
            "darker":     hsv(h, min(255, s + 40), 20),
            "bg":         hsv(h, max(5, s // 6), 17),
            "bg2":        hsv(h, max(6, s // 7), 22),
            "input":      hsv(h, max(6, s // 7), 27),
            "border":     hsv(h, max(18, s // 2), 52),
            "border2":    hsv(h, max(15, s // 2), 60),
            "hover":      hsv(h, min(255, s + 30), min(255, v + 60)),
            "pressed":    hsv(h, s, max(30, v - 30)),
            "disabled":   hsv(h, 10, 42),
            "disabled_t": hsv(h, 10, 118),
            "flat":       hsv(h, max(12, s // 4), 30),
            "flat_h":     hsv(h, max(22, s // 3), 42),
            "flat_text":  hsv(h, 25, 212),
            "text":       hsv(h, 18, 236),
            "text2":      hsv(h, 10, 200),
            "tab_sel":    hsv(h, min(255, s + 40), 238),
            "tab_text":   hsv(h, min(255, s + 20), 185),
            "hint":       hsv(h, 28, 190),
            "slider_g":   hsv(h, max(10, s // 3), 52),
            "slider_s":   hsv(h, min(255, s + 20), min(255, v + 30)),
            "scroll":     hsv(h, 15, 58),
            "scroll_h":   hsv(h, 25, 82),
            "selection":  hsv(h, min(255, s + 20), min(255, v + 30)),
            "group_t":    hsv(h, min(255, s + 50), 214),
        }
    return {
        "main":       hsv(h, s, v),
        "dark":       hsv(h, min(255, s + 50), max(70, v - 50)),
        "darker":     hsv(h, min(255, s + 80), max(50, v - 80)),
        "bg":         hsv(h, max(8, s // 5), 250),
        "bg2":        hsv(h, max(5, s // 6), 252),
        "input":      hsv(h, 0, 255),
        "border":     hsv(h, max(20, s // 2), 225),
        "border2":    hsv(h, max(15, s // 2), 235),
        "hover":      hsv(h, min(255, s + 10), max(100, v - 10)),
        "pressed":    hsv(h, min(255, s + 40), max(70, v - 40)),
        "disabled":   hsv(h, max(10, s // 4), 235),
        "disabled_t": hsv(h, max(20, s // 3), 200),
        "flat":       hsv(h, max(15, s // 4), 245),
        "flat_h":     hsv(h, max(20, s // 3), 235),
        "flat_text":  hsv(h, min(255, s + 30), max(80, v - 40)),
        "text":       hsv(h, min(255, s + 60), max(40, v - 70)),
        "text2":      hsv(h, min(255, s + 70), max(30, v - 80)),
        "tab_sel":    hsv(h, min(255, s + 40), max(80, v - 40)),
        "tab_text":   hsv(h, min(255, s + 30), max(90, v - 50)),
        "hint":       hsv(h, min(255, s + 20), max(100, v - 30)),
        "slider_g":   hsv(h, max(30, s // 2), 230),
        "slider_s":   hsv(h, min(255, s + 10), max(100, v - 10)),
        "scroll":     hsv(h, max(30, s // 2), 220),
        "scroll_h":   hsv(h, max(40, s // 2), 200),
        "selection":  hsv(h, min(255, s + 20), max(120, v - 20)),
        "group_t":    hsv(h, min(255, s + 50), max(70, v - 50)),
    }


def _arrow_png(color):
    """生成一个小三角 PNG 供 QComboBox::down-arrow 使用（无 QApplication 时返回空）。"""
    try:
        from PyQt6.QtWidgets import QApplication
        if QApplication.instance() is None:
            return ""
        from PyQt6.QtGui import QImage, QPainter, QColor
        from PyQt6.QtCore import QPoint
        key = (color or "").lstrip("#") or "hover"
        p = os.path.join(tempfile.gettempdir(), "smartpet_arrow_%s.png" % key)
        if not os.path.exists(p):
            img = QImage(14, 14, QImage.Format.Format_ARGB32)
            img.fill(0)
            qp = QPainter(img)
            qp.setRenderHint(QPainter.RenderHint.Antialiasing)
            qp.setBrush(QColor(color))
            qp.setPen(Qt.PenStyle.NoPen)
            qp.drawPolygon([QPoint(3, 5), QPoint(11, 5), QPoint(7, 10)])
            qp.end()
            img.save(p, "PNG")
        return p.replace("\\", "/")
    except Exception:
        return ""


def generate_style(theme_color="#ff96bb", radius=8, opacity=1.0, dark=False):
    """根据主题色/圆角/透明度/深浅色动态生成 QSS。"""
    c = _color_family(theme_color, dark=dark)
    r = int(radius)
    arrow = _arrow_png(c['hover'])
    arrow_css = f'image:url("{arrow}"); ' if arrow else ''
    # 悬停底色在两模式下都是亮粉：浅色用白字，深色用近黑字才有对比。
    hover_text = c['darker'] if dark else '#fff'
    return f"""
/* ===== 动态主题：{theme_color} {'深色' if dark else '浅色'} ===== */
QDialog, QWidget#root {{ background:{c['bg']}; }}
QGroupBox {{ font: bold 9pt 'Microsoft YaHei'; border:1px solid {c['border']};
            border-radius:{r+2}px; margin-top:12px; padding:10px; color:{c['group_t']};
            background:{c['bg2']}; }}
QGroupBox::title {{ left:12px; padding:0 5px; background:{c['bg']}; }}
QPushButton {{ background:{c['main']}; border:none; border-radius:{r}px; padding:7px 16px;
              font:bold 9pt 'Microsoft YaHei'; color:{c['darker']}; }}
QPushButton:hover {{ background:{c['hover']}; color:{hover_text}; }}
QPushButton:pressed {{ background:{c['pressed']}; }}
QPushButton:disabled {{ background:{c['disabled']}; color:{c['disabled_t']}; }}
QPushButton#flat {{ background:{c['flat']}; color:{c['flat_text']}; }}
QPushButton#flat:hover {{ background:{c['flat_h']}; color:{c['darker']}; }}
QLabel {{ font:9pt 'Microsoft YaHei'; color:{c['text']}; }}
QComboBox, QLineEdit, QKeySequenceEdit, QTextEdit, QPlainTextEdit {{
    background:{c['input']}; border:1px solid {c['border2']}; border-radius:{r-1}px; padding:4px 6px;
    font:9pt 'Microsoft YaHei'; color:{c['text2']}; selection-background-color:{c['selection']};}}
QComboBox:hover, QLineEdit:hover, QTextEdit:hover {{ border-color:{c['main']}; }}
QComboBox:focus, QLineEdit:focus, QTextEdit:focus {{ border:1px solid {c['hover']}; }}
QComboBox::drop-down {{ border:none; width:20px; }}
QComboBox::down-arrow {{ {arrow_css}width:10px; height:8px; }}
QComboBox QAbstractItemView {{ background:{c['input']}; color:{c['text']};
    selection-background-color:{c['main']}; selection-color:{c['darker']};
    border:1px solid {c['border2']}; }}
QCheckBox {{ font:9pt 'Microsoft YaHei'; color:{c['text']}; spacing:7px; }}
QTabWidget::pane {{ border:1px solid {c['border']}; border-radius:{r}px; top:-1px;
    background:{c['bg2']}; }}
QTabBar {{ background:transparent; }}
QTabBar::tab {{ font:10.5pt 'Microsoft YaHei'; padding:9px 12px; margin-right:3px;
    min-height:16px;
    background:{c['flat']}; color:{c['tab_text']};
    border-top-left-radius:{r}px; border-top-right-radius:{r}px;
    border:1px solid {c['border']}; border-bottom:none; }}
QTabBar::tab:selected {{ background:{c['bg2']}; color:{c['tab_sel']}; font-weight:bold; }}
QTabBar::tab:!selected:hover {{ background:{c['flat_h']}; }}
QProgressBar {{ border:1px solid {c['border2']}; border-radius:{r-2}px; background:{c['input']};
    text-align:center; height:16px; color:{c['darker']}; font:9pt 'Microsoft YaHei'; }}
QProgressBar::chunk {{ background:{c['main']}; border-radius:{r-3}px; }}
QSlider::groove:horizontal {{ height:5px; background:{c['slider_g']}; border-radius:2px; }}
QSlider::sub-page:horizontal {{ background:{c['slider_s']}; border-radius:2px; }}
QSlider::handle:horizontal {{ width:14px; height:14px; margin:-6px 0;
    background:{c['input']}; border:2px solid {c['hover']}; border-radius:8px; }}
QSlider::handle:horizontal:hover {{ background:{c['bg']}; }}
QScrollBar:vertical {{ background:transparent; width:10px; margin:2px; }}
QScrollBar::handle:vertical {{ background:{c['scroll']}; border-radius:5px; min-height:24px; }}
QScrollBar::handle:vertical:hover {{ background:{c['scroll_h']}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page,
QScrollBar::sub-page {{ height:0; background:transparent; }}
QScrollBar:horizontal {{ background:transparent; height:10px; }}
QScrollBar::handle:horizontal {{ background:{c['scroll']}; border-radius:5px; min-width:24px; }}
QToolTip {{ background:{c['bg2']}; color:{c['text']}; border:1px solid {c['border']};
    border-radius:5px; padding:3px 6px; }}
QScrollArea {{ background:{c['bg2']}; border:none; }}
QScrollArea > QWidget > QWidget {{ background:{c['bg2']}; }}
QMenu {{ background:{c['bg2']}; border:1px solid {c['border']}; border-radius:{r}px;
    padding:4px; }}
QMenu::item {{ padding:6px 24px; border-radius:{r-2}px; color:{c['text']}; }}
QMenu::item:selected {{ background:{c['main']}; color:{c['darker']}; }}
QMenu::separator {{ height:1px; background:{c['border']}; margin:4px 8px; }}
QMessageBox QLabel {{ color:{c['text']}; }}
QMessageBox QPushButton {{ min-width:64px; }}
/* ===== 可折叠分组卡片（设置页）===== */
QFrame#collSection {{ background:{c['bg2']}; border:1px solid {c['border']};
    border-radius:{r+3}px; }}
QPushButton#collHeader {{ background:transparent; border:none;
    border-radius:{r+2}px; padding:11px 14px; text-align:left;
    font:bold 10pt 'Microsoft YaHei'; color:{c['tab_sel']}; }}
QPushButton#collHeader:hover {{ background:{c['flat_h']}; }}
QWidget#collBody {{ background:transparent; }}
QLabel#collRowLabel {{ color:{c['text2']}; font:9pt 'Microsoft YaHei'; }}
QLabel#collHint {{ color:{c['hint']}; font:8.5pt 'Microsoft YaHei'; }}
"""


def apply_theme(widget, cfg):
    """给对话框/菜单应用主题样式 + 透明度。"""
    s = generate_style(
        cfg.get("theme_color", "#ff96bb"),
        cfg.get("theme_radius", 8),
        cfg.get("panel_opacity", 1.0),
        dark=bool(cfg.get("dark_mode", False)))
    widget.setStyleSheet(s)
    try:
        widget.setWindowOpacity(float(cfg.get("panel_opacity", 1.0)))
    except Exception:
        pass


STYLE = generate_style()
PINK_HINT = _color_family("#ff96bb")["hint"]
PINK_PET_MSG = "#d6497a"
PINK_USER_MSG = "#8a5a8a"
PINK_SYS_MSG = "#b08a9c"


def hint_color(cfg=None):
    """提示文字颜色：浅色模式＝淡粉，深色模式＝亮粉（浅底/深底都可读）。"""
    dark = bool((cfg or {}).get("dark_mode", False))
    return _color_family("#ff96bb", dark=dark)["hint"]

FPS_ITEMS = [("smooth", "流畅（最生动，耗电略高）"),
             ("balanced", "均衡（推荐）"),
             ("normal", "普通"),
             ("saver", "省电（最低帧率）")]
FOLLOW_ITEMS = [("off", "关闭（不跟随）"),
                ("sticky", "粘人模式（慢慢跟着你，离远会小跑过来）"),
                ("timid", "胆小模式（快挪鼠标会躲开，停下会凑过来）")]
CORNER_ITEMS = [("", "不锁定（自由活动+智能避让）"),
                ("tl", "锁定左上角"), ("tr", "锁定右上角"),
                ("bl", "锁定左下角"), ("br", "锁定右下角")]
# 动作 key -> 中文名（与 pets/<角色>/<动作>/ 文件夹一一对应，用于自定义角色上传）
ACTION_CN = {
    "idle": "待机", "happy": "开心", "pet": "摸头", "chin": "挠下巴",
    "pickup": "抱起来", "spin": "转圈", "hop": "蹦跳", "sleep": "睡觉",
    "deep": "深睡", "walk": "走路", "chase": "追球", "scared": "受惊",
    "nervous": "紧张", "relax": "放松", "groom": "舔毛", "eat": "吃东西",
    "paper": "收信息", "work": "陪写代码", "peek": "探头", "play": "玩耍",
    "hide": "躲藏",
}
ACTION_ITEMS = [(a, ACTION_CN.get(a, a) + f"（{a}）") for a in ACTIONS]


def _safe_folder(name):
    """把用户输入的角色名转成合法文件夹名。"""
    bad = set('\\/:*?"<>|')
    out = "".join("_" if ch in bad else ch for ch in (name or "").strip())
    return out.strip().rstrip(".") or ""


class CollapsibleSection(QFrame):
    """可折叠分组卡片：点标题展开/收起。

    用于把设置页里同类选项合并成一组，默认收起次要组，让界面清爽不臃肿。
    只提供布局/外观，不改变内部控件的信号连接。
    """

    toggled = pyqtSignal(bool)   # 展开/收起变化（供窗口重算尺寸）

    def __init__(self, title, icon="", expanded=False, parent=None):
        super().__init__(parent)
        self.setObjectName("collSection")
        self._title = title
        self._icon = icon
        self._open = False
        v = QVBoxLayout(self)
        v.setContentsMargins(2, 2, 2, 2)
        v.setSpacing(0)
        self._header = QPushButton()
        self._header.setObjectName("collHeader")
        self._header.setCheckable(True)
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.clicked.connect(self._on_click)
        v.addWidget(self._header)
        self._body = QWidget()
        self._body.setObjectName("collBody")
        self._bl = QVBoxLayout(self._body)
        self._bl.setContentsMargins(16, 2, 16, 14)
        self._bl.setSpacing(9)
        v.addWidget(self._body)
        self._sync_header()
        self.set_expanded(expanded)

    def _sync_header(self):
        arrow = "▾" if self._open else "▸"
        ic = (self._icon + " ") if self._icon else ""
        self._header.setText("%s   %s%s" % (arrow, ic, self._title))

    def _on_click(self, checked):
        self.set_expanded(checked)

    def set_expanded(self, on):
        self._open = bool(on)
        self._header.setChecked(self._open)
        self._body.setVisible(self._open)
        self._sync_header()
        self.toggled.emit(self._open)

    def is_expanded(self):
        return self._open

    # ---- 内容装配 ----
    def add_row(self, label, field):
        """一行：左侧固定宽标签 + 右侧控件（拉伸占满）。返回整行控件，便于整体隐藏。"""
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(10)
        lab = QLabel(label)
        lab.setObjectName("collRowLabel")
        lab.setMinimumWidth(84)
        h.addWidget(lab)
        h.addWidget(field, 1)
        self._bl.addWidget(row)
        return row

    def add_field(self, field):
        """无标签，控件占满整行（复选框/提示等）。"""
        self._bl.addWidget(field)
        return field

    def add_hint(self, text):
        lab = QLabel(text)
        lab.setObjectName("collHint")
        lab.setWordWrap(True)
        self._bl.addWidget(lab)
        return lab


class _SpinNoWheel(QSpinBox):
    """忽略鼠标滚轮的 QSpinBox：避免在滚动区里滚页面时被误 increment 数值。"""

    def wheelEvent(self, e):
        e.ignore()   # 交给父级滚动区处理，而不是改数字


class _PluginTask(QThread):
    """后台跑一个可调用（如从网址下载/安装插件），避免卡住设置界面。"""
    done = pyqtSignal(object)   # 成功：传回结果
    failed = pyqtSignal(str)    # 失败：传回错误文本

    def __init__(self, fn, *args):
        super().__init__()
        self._fn = fn
        self._args = args

    def run(self):
        try:
            self.done.emit(self._fn(*self._args))
        except Exception as e:
            self.failed.emit(str(e) or e.__class__.__name__)


class SettingsDialog(QDialog):
    preview_size_changed = pyqtSignal(int)
    preview_character_changed = pyqtSignal(str)
    preview_cursor_size_changed = pyqtSignal(int)
    milestone_unlocked = pyqtSignal()
    dark_mode_changed = pyqtSignal(bool)
    bongo_size_changed = pyqtSignal(int)
    bongo_opacity_changed = pyqtSignal(int)
    bongo_click_through_changed = pyqtSignal(bool)
    classic_enabled_changed = pyqtSignal(bool)   # 桌宠模式开关
    bongo_enabled_changed = pyqtSignal(bool)     # Bongo 模式开关
    pet_count_changed = pyqtSignal(int)          # 小狗数量（1~15）
    launch_plugin_requested = pyqtSignal(str)    # 请求启动某插件（交回本体处理）
    _coexist_warned = False   # 本次运行已弹过共存警告，不再重复打扰
    _count_warned = False     # 本次运行已弹过数量警告，不再重复打扰

    def __init__(self, cfg, assets=None, parent=None, plugins=None, focus_tab=None):
        super().__init__(parent)
        self.setObjectName("root")
        self.setWindowTitle("LineDogPet · 设置")
        apply_theme(self, cfg)
        # 不再设固定最小宽度：宽度跟随内容自然尺寸（原 500px 下限会在
        # 内容右侧留出空隙）；标签栏放不下时由 _build 末尾的动态逻辑兜底。
        self.cfg = cfg
        self.assets = assets or AssetIndex()
        # 插件管理器：由本体传入；若单独打开设置则自建一个（仅用于扫描/安装）。
        if plugins is not None:
            self.plugins = plugins
        else:
            try:
                from .pluginapi import PluginManager
                self.plugins = PluginManager(cfg)
            except Exception:
                self.plugins = None
        self._focus_tab = focus_tab
        self._orig_snapshot = {}
        self._persona_edits = {}   # 各形象在设置面板里编辑中的 AI 人设缓存
        self._build()
        # 角色下拉框按 pets/ 目录动态重建（内置 + 自定义）
        self._rebuild_char_combo(cfg.get("character", "xiaobai"))
        self._load()
        self._refit_soon()
        self._refresh_sound_status()
        self._refresh_cursor_gif_status()
        self._net = None
        self._update_reply = None
        self._update_silent = True
        self._auto_checked = False
        self._update_urls = []
        self._update_idx = 0
        # 隐藏彩蛋：连续点击【关于】5 次解锁里程碑入口
        self._about_clicks = 0
        self._last_about_click = 0.0
        self._milestone_emitted = False
        QTimer.singleShot(1500, self._auto_check_update)

    def _build(self):
        root = QVBoxLayout(self)

        # 顶部：左边标题，右上角深色模式切换（太阳/月亮）
        header = QHBoxLayout()
        title_lbl = QLabel("<b style='color:#d6497a;'>LineDogPet</b> 设置")
        title_lbl.setStyleSheet("font:bold 11pt 'Microsoft YaHei'; border:none;")
        self.dark_btn = QPushButton()
        self.dark_btn.setObjectName("flat")
        # 全局 QPushButton 的 padding(7px 16px) 会把 30x30 按钮内容区挤没，
        # 导致 🌙/☀️ 不显示；局部样式清零 padding 并改用 emoji 字体。
        self.dark_btn.setStyleSheet(
            "QPushButton#flat { padding:0px; border-radius:15px;\n"
            "    font:13pt 'Segoe UI Emoji'; }", )
        self.dark_btn.setFixedSize(30, 30)
        self.dark_btn.setToolTip("切换软件深色模式")
        self.dark_btn.clicked.connect(self._toggle_dark)
        self._update_dark_btn()
        header.addWidget(title_lbl)
        header.addStretch(1)
        header.addWidget(self.dark_btn)
        root.addLayout(header)

        tabs = QTabWidget()
        self.tabs = tabs
        self._hidden_tab_idx = set()   # 不在顶部标签栏占位的页签（关于/插件）
        # 标签摊开填满整行：窗口宽度由内容决定时，标签右侧不会留白
        tabs.tabBar().setExpanding(True)
        root.addWidget(tabs, 1)

        # ---------- Tab1 形象与外观 ----------
        t1 = QWidget()
        v1 = QVBoxLayout(t1)
        v1.setContentsMargins(14, 12, 14, 12)
        v1.setSpacing(10)
        # 宠物形象：内置角色 + 用户自定义角色（pets/ 文件夹动态发现）
        self.char = QComboBox()
        self.btn_add_char = QPushButton("＋ 添加")
        self.btn_add_char.setObjectName("flat")
        self.btn_add_char.clicked.connect(self._add_character)
        self.btn_del_char = QPushButton("－ 删除")
        self.btn_del_char.setObjectName("flat")
        self.btn_del_char.clicked.connect(self._del_character)
        char_row = QWidget()
        self.char_row = char_row
        chr_ly = QHBoxLayout(char_row)
        chr_ly.setContentsMargins(0, 0, 0, 0)
        chr_ly.addWidget(self.char, 1)
        chr_ly.addWidget(self.btn_add_char)
        chr_ly.addWidget(self.btn_del_char)
        self.char_status = QLabel("")
        self.char_status.setStyleSheet("color:%s; font-size:8pt;" % hint_color(self.cfg))
        self.char_status.setWordWrap(True)
        self.char.currentIndexChanged.connect(
            lambda _i: self._refresh_char_status())
        # 上传表情：选动作 -> 选 gif，复制进该角色对应动作文件夹
        self.act_combo = QComboBox()
        for val, label in ACTION_ITEMS:
            self.act_combo.addItem(label, val)
        self.btn_upload = QPushButton("上传 GIF…")
        self.btn_upload.setObjectName("flat")
        self.btn_upload.clicked.connect(self._upload_gifs)
        up_row = QWidget()
        self.up_row = up_row
        up_ly = QHBoxLayout(up_row)
        up_ly.setContentsMargins(0, 0, 0, 0)
        up_ly.addWidget(self.act_combo, 1)
        up_ly.addWidget(self.btn_upload)
        # 自定义叫声：sounds/bark.wav 优先于内置合成叫声
        self.snd_status = QLabel("")
        self.snd_status.setStyleSheet("color:%s; font-size:8pt;" % hint_color(self.cfg))
        self.btn_snd_pick = QPushButton("选择音频…")
        self.btn_snd_pick.setObjectName("flat")
        self.btn_snd_pick.clicked.connect(self._pick_sound)
        self.btn_snd_reset = QPushButton("恢复默认")
        self.btn_snd_reset.setObjectName("flat")
        self.btn_snd_reset.clicked.connect(self._reset_sound)
        snd_row = QWidget()
        snd_ly = QHBoxLayout(snd_row)
        snd_ly.setContentsMargins(0, 0, 0, 0)
        snd_ly.addWidget(self.btn_snd_pick)
        snd_ly.addWidget(self.btn_snd_reset)
        snd_ly.addStretch(1)
        self.size = QSlider(Qt.Orientation.Horizontal)
        self.size.setRange(80, 500)
        self.size_label = QLabel()
        szrow = QWidget()
        szl = QHBoxLayout(szrow)
        szl.setContentsMargins(0, 0, 0, 0)
        szl.addWidget(self.size)
        szl.addWidget(self.size_label)
        self.size.valueChanged.connect(lambda v: self.size_label.setText(f"{v}px"))
        self.size.valueChanged.connect(lambda v: self.preview_size_changed.emit(v))
        self.char.currentIndexChanged.connect(
            lambda _i: self.preview_character_changed.emit(
                self.char.currentData() or "xiaobai"))
        self.fps = QComboBox()
        for val, label in FPS_ITEMS:
            self.fps.addItem(label, val)
        self.sound_on = QCheckBox("开启音效（叫声/哼唧，都是极短的小声音）")
        self.hourly = QCheckBox("整点轻轻叫一声")
        self.bubble_on = QCheckBox("显示字幕/气泡（关闭后所有文字气泡都不再弹出）")
        # 气泡显示时长
        self.bubble_dur = QSlider(Qt.Orientation.Horizontal)
        self.bubble_dur.setRange(1500, 12000)
        self.bubble_dur.setSingleStep(500)
        self.bubble_dur_label = QLabel()
        bdrow = QWidget()
        bdl = QHBoxLayout(bdrow)
        bdl.setContentsMargins(0, 0, 0, 0)
        bdl.addWidget(self.bubble_dur)
        bdl.addWidget(self.bubble_dur_label)
        self.bubble_dur.valueChanged.connect(
            lambda v: self.bubble_dur_label.setText(f"{v/1000:.1f}秒"))
        # 气泡文字颜色：色块预览 + 更改按钮
        self._bubble_color = "#222222"
        self.bubble_swatch = QLabel()
        self.bubble_swatch.setFixedSize(44, 20)
        bubble_btn = QPushButton("更改…")
        bubble_btn.clicked.connect(self._pick_bubble_color)
        bubble_row = QWidget()
        bl = QHBoxLayout(bubble_row)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.addWidget(self.bubble_swatch)
        bl.addWidget(bubble_btn)
        bl.addStretch(1)
        # ---- 桌宠模式 / Bongo 模式开关 + 小狗数量（第一栏顶部，小巧；两者可共存）----
        self.classic_mode = ToggleSwitch()
        self.classic_mode.toggled.connect(self._on_classic_toggled)
        self.bongo_mode = ToggleSwitch()
        self.bongo_mode.toggled.connect(self._on_bongo_toggled)
        mode_tip = QLabel("桌宠模式与 Bongo 模式可同时开启（会加大 CPU 和内存占用）。"
                          "小狗数量：第 1 只为主小狗，其余为陪玩小狗。")
        mode_tip.setWordWrap(True)
        mode_tip.setStyleSheet("color:%s; font-size:8pt;" % hint_color(self.cfg))
        self.pet_count = _SpinNoWheel()
        self.pet_count.setRange(1, 15)
        # 宽度需容纳「两位数字(10~15) + 右侧上下箭头」，写死 56 会把两位数挤成一位；
        # 以控件自身 sizeHint 兜底，避免不同字体/主题/DPI 下再次裁切。
        self.pet_count.setFixedWidth(max(76, self.pet_count.sizeHint().width()))
        self.pet_count.setToolTip("同时显示的小狗数量（1~15）")
        self.pet_count.valueChanged.connect(self._on_pet_count_changed)
        self.bongo_mirror = QCheckBox("镜像键盘（与真实键盘同向）")
        self.bongo_click_through = QCheckBox("鼠标穿透（点击穿过窗口到桌面，不可拖动）")
        self.bongo_click_through.toggled.connect(self.bongo_click_through_changed.emit)
        self.bongo_size = QSlider(Qt.Orientation.Horizontal)
        self.bongo_size.setRange(120, 700)
        self.bongo_size_label = QLabel()
        bongo_sz_row = QWidget()
        bongo_szl = QHBoxLayout(bongo_sz_row)
        bongo_szl.setContentsMargins(0, 0, 0, 0)
        bongo_szl.addWidget(self.bongo_size)
        bongo_szl.addWidget(self.bongo_size_label)
        self.bongo_size.valueChanged.connect(lambda v: self.bongo_size_label.setText(f"{v}px"))
        self.bongo_size.valueChanged.connect(self.bongo_size_changed.emit)
        self.bongo_opacity = QSlider(Qt.Orientation.Horizontal)
        self.bongo_opacity.setRange(30, 100)
        self.bongo_opacity_label = QLabel()
        bongo_op_row = QWidget()
        bongo_opl = QHBoxLayout(bongo_op_row)
        bongo_opl.setContentsMargins(0, 0, 0, 0)
        bongo_opl.addWidget(self.bongo_opacity)
        bongo_opl.addWidget(self.bongo_opacity_label)
        self.bongo_opacity.valueChanged.connect(lambda v: self.bongo_opacity_label.setText(f"{v}%"))
        self.bongo_opacity.valueChanged.connect(self.bongo_opacity_changed.emit)
        self._bongo_group = QWidget()
        bgl = QVBoxLayout(self._bongo_group)
        bgl.setContentsMargins(0, 0, 0, 0)
        bgl.addWidget(self.bongo_mirror)
        bgl.addWidget(self.bongo_click_through)
        bgl.addWidget(QLabel("显示大小："))
        bgl.addWidget(bongo_sz_row)
        bgl.addWidget(QLabel("透明度："))
        bgl.addWidget(bongo_op_row)
        mode_row = QWidget()
        mrl = QHBoxLayout(mode_row)
        mrl.setContentsMargins(0, 0, 0, 0)
        mrl.setSpacing(10)
        mrl.addWidget(QLabel("桌宠"))
        mrl.addWidget(self.classic_mode)
        mrl.addSpacing(8)
        mrl.addWidget(QLabel("Bongo"))
        mrl.addWidget(self.bongo_mode)
        mrl.addStretch(1)
        # ---- 折叠分组：同类合并，默认只展开“形态与数量” ----
        s1a = CollapsibleSection("形态与数量", "🐾", expanded=True)
        s1a.add_row("模式：", mode_row)
        self._pet_count_row = s1a.add_row("小狗数量：", self.pet_count)
        s1a.add_field(mode_tip)
        s1a.add_field(self._bongo_group)
        v1.addWidget(s1a)

        s1b = CollapsibleSection("宠物形象与素材", "🎨")
        s1b.add_row("宠物形象：", char_row)
        s1b.add_field(self.char_status)
        self._upload_row = s1b.add_row("上传表情：", up_row)
        s1b.add_row("自定义叫声：", snd_row)
        s1b.add_field(self.snd_status)
        v1.addWidget(s1b)

        s1c = CollapsibleSection("显示与动画", "📏")
        s1c.add_row("显示大小：", szrow)
        s1c.add_row("动画帧率：", self.fps)
        v1.addWidget(s1c)

        s1d = CollapsibleSection("气泡与音效", "💬")
        s1d.add_field(self.bubble_on)
        s1d.add_row("气泡时长：", bdrow)
        s1d.add_row("气泡颜色：", bubble_row)
        s1d.add_field(self.sound_on)
        s1d.add_field(self.hourly)
        v1.addWidget(s1d)

        # 主题色 / 外观微调并入本页（顶部标签合并，减少页签数量）
        self._build_theme_sections(v1)

        reset_btn = QPushButton("恢复默认设置")
        reset_btn.setObjectName("flat")
        reset_btn.clicked.connect(self._reset_defaults)
        rr = QHBoxLayout(); rr.addWidget(reset_btn); rr.addStretch(1)
        v1.addLayout(rr)
        v1.addStretch(1)
        tabs.addTab(self._wrap_scroll(t1), "形象与外观")

        # ---------- Tab2 交互与移动（鼠标 + 桌面 + �710脚7印）----------
        t2 = QWidget()
        v2 = QVBoxLayout(t2)
        v2.setContentsMargins(14, 12, 14, 12)
        v2.setSpacing(10)
        self.mouse_on = QCheckBox("开启鼠标交互（摸头/抱起/手势/跟随）")
        self.mouse_through_tip = QLabel("关闭后为「鼠标穿透」")
        self.mouse_through_tip.setWordWrap(True)
        self.mouse_through_tip.setStyleSheet("color:%s; font-size:8pt;" % hint_color(self.cfg))
        self.click_squash = QCheckBox("开启点击 Q 弹动画（单击压扁回弹 / 双击弹跳）")
        click_squash_tip = QLabel("单击原地 Q 弹，双击向上弹跳落地回弹；关闭后仅保留对话。")
        click_squash_tip.setWordWrap(True)
        click_squash_tip.setStyleSheet("color:%s; font-size:8pt;" % hint_color(self.cfg))
        self.follow = QComboBox()
        for val, label in FOLLOW_ITEMS:
            self.follow.addItem(label, val)
        tip2 = QLabel("左键轻点＝摸头　长按拖拽＝抱起　右键＝挠下巴　双击＝呼唤\n"
                      "按住拖一个小圆圈＝转圈撒娇　快速甩动＝小狗追\n"
                      "滚轮中键点击＝和它说话")
        tip2.setStyleSheet("color:%s;" % hint_color(self.cfg))
        self.cursor_follow = QCheckBox(
            "鼠标指针皮肤（可自定义鼠标皮肤）")
        cursor_tip = QLabel("开启后会和光标一起移动，不影响点击操作。")
        cursor_tip.setWordWrap(True)
        cursor_tip.setStyleSheet("color:%s; font-size:8pt;" % hint_color(self.cfg))

        # 鼠标跟随宠自定义表情
        self.cursor_gif_status = QLabel("")
        self.cursor_gif_status.setStyleSheet("color:%s; font-size:8pt;" % hint_color(self.cfg))
        self.cursor_gif_status.setWordWrap(True)
        
        cursor_gif_row = QWidget()
        cursor_gif_layout = QHBoxLayout(cursor_gif_row)
        cursor_gif_layout.setContentsMargins(0, 0, 0, 0)
        
        self.btn_upload_cursor = QPushButton("上传自定义表情…")
        self.btn_upload_cursor.setObjectName("flat")
        self.btn_upload_cursor.clicked.connect(self._upload_cursor_gif)
        
        self.btn_reset_cursor = QPushButton("恢复默认")
        self.btn_reset_cursor.setObjectName("flat")
        self.btn_reset_cursor.clicked.connect(self._reset_cursor_gif)
        
        cursor_gif_layout.addWidget(self.btn_upload_cursor)
        cursor_gif_layout.addWidget(self.btn_reset_cursor)
        cursor_gif_layout.addStretch(1)
        
        # 鼠标跟随宠大小调节
        self.cursor_size = QSlider(Qt.Orientation.Horizontal)
        self.cursor_size.setRange(30, 150)
        self.cursor_size_label = QLabel()
        cursor_sz_row = QWidget()
        cursor_szl = QHBoxLayout(cursor_sz_row)
        cursor_szl.setContentsMargins(0, 0, 0, 0)
        cursor_szl.addWidget(self.cursor_size)
        cursor_szl.addWidget(self.cursor_size_label)
        self.cursor_size.valueChanged.connect(
            lambda v: self.cursor_size_label.setText(f"{v}px"))
        self.cursor_size.valueChanged.connect(
            lambda v: self.preview_cursor_size_changed.emit(v))
        
        # 移动/避让/快捷键/自启/脚印 控件创建（原内联，提前创建以便分组装配）
        self.avoid = QCheckBox("智能避让窗口（自动跑到没被窗口挡住的空白桌面）")
        self.roam = QCheckBox("随机闲逛（空闲时在桌面空白处自然走动）")
        self.corner = QComboBox()
        for val, label in CORNER_ITEMS:
            self.corner.addItem(label, val)
        self.hk_on = QCheckBox("启用全局快捷键（一键隐藏/显示）")
        self.hotkey = QKeySequenceEdit()
        self.hotkey.setToolTip("必须包含 Ctrl/Alt/Shift 中的至少一个")
        self.autostart = QCheckBox("开机自启动（登录 Windows 时自动运行）")
        self.paw_on = QCheckBox("移动时在桌面留淡脚印（渐隐消失）")
        self.paw_fade = QSlider(Qt.Orientation.Horizontal)
        self.paw_fade.setRange(2, 30)
        self.paw_fade_label = QLabel()
        pfrow = QWidget()
        pfl = QHBoxLayout(pfrow)
        pfl.setContentsMargins(0, 0, 0, 0)
        pfl.addWidget(self.paw_fade)
        pfl.addWidget(self.paw_fade_label)
        self.paw_fade.valueChanged.connect(
            lambda v: self.paw_fade_label.setText(f"{v} 秒"))

        s2a = CollapsibleSection("鼠标交互", "🖱", expanded=True)
        s2a.add_field(self.mouse_on)
        s2a.add_field(self.mouse_through_tip)
        s2a.add_field(self.click_squash)
        s2a.add_field(click_squash_tip)
        s2a.add_row("跟随模式：", self.follow)
        s2a.add_field(tip2)
        v2.addWidget(s2a)

        s2b = CollapsibleSection("指针皮肤", "🖐")
        s2b.add_field(self.cursor_follow)
        s2b.add_field(cursor_tip)
        s2b.add_row("自定义表情：", cursor_gif_row)
        s2b.add_field(self.cursor_gif_status)
        s2b.add_row("皮肤大小：", cursor_sz_row)
        v2.addWidget(s2b)

        s2c = CollapsibleSection("移动与避让", "🚶")
        s2c.add_field(self.avoid)
        s2c.add_field(self.roam)
        s2c.add_row("固定位置：", self.corner)
        v2.addWidget(s2c)

        s2d = CollapsibleSection("快捷键与启动", "⌨")
        s2d.add_field(self.hk_on)
        s2d.add_row("快捷键：", self.hotkey)
        s2d.add_field(self.autostart)
        v2.addWidget(s2d)

        s2e = CollapsibleSection("脚印", "🐾")
        s2e.add_field(self.paw_on)
        s2e.add_row("消失时长：", pfrow)
        v2.addWidget(s2e)
        v2.addStretch(1)
        tabs.addTab(self._wrap_scroll(t2), "交互与移动")

        # ---------- Tab3 感知与陪伴（系统感知 + 夜间 + 观察 + 听歌 + 截图 + 恶作剧）----------
        t3 = QWidget()
        v3 = QVBoxLayout(t3)
        v3.setContentsMargins(14, 12, 14, 12)
        v3.setSpacing(10)
        self.sense_all = QCheckBox("总开关：系统感知")
        self.s_idle = QCheckBox("键鼠闲置（3分钟睡着，久了蜷成一团，一动就醒）")
        self.s_window = QCheckBox("窗口感知（全屏缩小躲边角；写代码安静陪伴）")
        self.s_cpu = QCheckBox("负载感知（CPU 高时自动降帧省CPU，不影响小狗活动）")
        self.s_clip = QCheckBox("剪贴板感知（只读取你刚复制的文本，弹小纸片；绝不上传）")
        self.s_time = QCheckBox("时间感知（白天活泼/傍晚慵懒/夜里犯困）")
        self.fullscreen_shrink = QCheckBox("全屏游戏/演示时自动缩小躲到屏幕角落")
        self.night_on = QCheckBox("夜间陪伴（23点后按时段说陪伴台词，凌晨2点趴睡）")
        self.observe_on = QCheckBox("偷偷观察你（按窗口/进程规则吐槽陪伴，纯本地）")
        self.music_on = QCheckBox("听歌互动（检测播放器，说正在听的歌，统计循环次数）")
        self.shot_on = QCheckBox("截图联动（剪贴板出现截图时弹小气泡）")
        self.prank_on = QCheckBox("小恶作剧（偶尔画几个小爪印，慢慢消失）")
        self.prank_mouse = QCheckBox("恶作剧：偷偷挪鼠标一点点（轻微，建议关闭）")
        
        s3a = CollapsibleSection("系统自感知（本地，不联网）", "📡", expanded=True)
        s3a.add_field(self.sense_all)
        for w in (self.s_idle, self.s_window, self.s_cpu, self.s_clip,
                  self.s_time, self.fullscreen_shrink):
            s3a.add_field(w)
        v3.addWidget(s3a)
        
        s3b = CollapsibleSection("陪伴与互动", "🌙", expanded=True)
        for w in (self.night_on, self.observe_on, self.music_on, self.shot_on):
            s3b.add_field(w)
        v3.addWidget(s3b)
        
        s3c = CollapsibleSection("小恶作剧", "😈")
        for w in (self.prank_on, self.prank_mouse):
            s3c.add_field(w)
        v3.addWidget(s3c)
        v3.addStretch(1)
        tabs.addTab(self._wrap_scroll(t3), "感知与陪伴")

        # ---------- Tab4 AI 对话（独立一栏）----------
        t4 = QWidget()
        v4 = QVBoxLayout(t4)
        v4.setContentsMargins(14, 12, 14, 12)
        v4.setSpacing(10)
        self.ai_on = QCheckBox("开启 AI 对话（默认关闭；开启后也只在你中键叫它时才联网）")
        self.provider = QComboBox()
        for val, (_b, _m, label) in ai_mod.PROVIDERS.items():
            self.provider.addItem(label, val)
        self.ai_base = QLineEdit()
        self.ai_base.setPlaceholderText("https://api.deepseek.com/v1")
        self.ai_key = QLineEdit()
        self.ai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.ai_key.setPlaceholderText("粘贴你自己的 API Key")
        self.ai_model = QLineEdit()
        self.ai_model.setPlaceholderText("deepseek-chat")
        self.ai_ctx = QCheckBox("把本地状态（时段/CPU/在写代码等）作为对话上下文")
        self.ai_persona_box = QPlainTextEdit()
        self.ai_persona_box.setFixedHeight(100)
        self.ai_persona_box.setPlaceholderText(
            "在这里写清楚 AI 该以什么身份、什么语气回复……\n"
            "人设跟着宠物形象走：切换到哪个形象就回复哪个形象的人设。\n"
            "留空则使用该形象的内置默认人设。")
        self.ai_persona_hint = QLabel("")
        self.ai_persona_hint.setWordWrap(True)
        self.ai_persona_hint.setStyleSheet("color:%s; font-size:8pt;" % hint_color(self.cfg))
        privacy = QLabel("隐私：请求从你的电脑直接发送到你填写的服务商，不经过任何中间服务器，\n"
                         "程序不保存聊天记录。Key 仅保存在本机 config.json。")
        privacy.setStyleSheet("color:%s;" % hint_color(self.cfg))
        s4a = CollapsibleSection("接入与开关", "🤖", expanded=True)
        s4a.add_field(self.ai_on)
        s4a.add_row("服务商：", self.provider)
        s4a.add_row("API 地址：", self.ai_base)
        s4a.add_row("API Key：", self.ai_key)
        s4a.add_row("模型名称：", self.ai_model)
        s4a.add_field(self.ai_ctx)
        v4.addWidget(s4a)

        s4b = CollapsibleSection("回复人设", "🎭")
        s4b.add_field(self.ai_persona_box)
        s4b.add_field(self.ai_persona_hint)
        s4b.add_field(privacy)
        v4.addWidget(s4b)
        v4.addStretch(1)
        self.provider.currentIndexChanged.connect(self._on_provider)
        tabs.addTab(self._wrap_scroll(t4), "AI 对话")

        # ---------- Tab5 外观主题（已并入“形象与外观”页）----------

        # ---------- Tab5.5 插件（可单独下载的小游戏/增值功能）----------
        self._build_plugin_tab(tabs)

        # ---------- Tab6 关于 ----------
        self._build_about_tab(tabs)

        # ---------- Tab7 联动视效（最后一栏：双开联动才解锁）----------
        self._build_link_tab(tabs)

        # ---------- 底部：关于 / 插件（左下） + 取消/保存（右下） ----------
        bottom_row = QHBoxLayout()
        btn_about = QPushButton("关于")
        btn_about.setObjectName("flat")
        btn_about.clicked.connect(self._show_about)
        bottom_row.addWidget(btn_about)
        btn_plugins = QPushButton("插件")
        btn_plugins.setObjectName("flat")
        btn_plugins.clicked.connect(self._show_plugins)
        bottom_row.addWidget(btn_plugins)
        bottom_row.addStretch(1)

        btn_box = QHBoxLayout()
        btn_box.setSpacing(6)
        cancel = QPushButton("取消")
        cancel.setObjectName("flat")
        cancel.clicked.connect(self._reject_restore)
        ok = QPushButton("保存")
        ok.clicked.connect(self._save_and_accept)
        btn_box.addWidget(cancel)
        btn_box.addWidget(ok)
        bottom_row.addLayout(btn_box)

        root.addLayout(bottom_row)

        # 顶部标签一排放得下且不放大窗口：窗口保持自然自适应尺寸，
        # 仅当标签栏比内容更宽时，把最小宽度收到"刚好放下标签"——
        # QTabWidget 自己不会为标签栏加宽，缺了就会出现左右滑动箭头。
        self._fit_width_to_tabs()

        # 从托盘“管理/安装插件…”进入时直接定位到插件页
        if getattr(self, "_focus_tab", None) == "plugin" and \
                getattr(self, "_plugin_tab_index", None) is not None:
            self.tabs.setCurrentIndex(self._plugin_tab_index)

        # 窗口尺寸已在上面按“各分组内容体最宽”一次性算定并 setFixedSize；
        # 展开/收起分组不再触发重算（否则会在布局切换瞬间量到过渡值导致宽度跳动）。
        # 宽度取的是所有主配置页所有分组展开时的最大值，故展开也不会裁切。

    def _wrap_scroll(self, page):
        """把页面包进滚动区：横向锁定不滚动（宽度足够显示全部内容），
        内容超高时只允许上下滑动。"""
        s = QScrollArea()
        s.setWidgetResizable(True)
        s.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        s.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        s.setFrameShape(QFrame.Shape.NoFrame)
        s.setWidget(page)
        return s

    def _fit_width_to_tabs(self):
        """固定窗口宽/高：宽度按最宽页内容（横向不出滑动条），高度固定，
        内容超高时竖向滚动；并钳制到不超过可用屏幕。"""
        bar = self.tabs.tabBar()
        hidden = getattr(self, "_hidden_tab_idx", set())
        tab_w = sum(bar.tabSizeHint(i).width() for i in range(bar.count())
                    if i not in hidden)
        margins = self.contentsMargins()
        tab_w += margins.left() + margins.right() + 8
        # 横向不滚动的那几页（主配置页）：宽度必须容纳其内容。
        # 直接按每个分组“内容体”的最小宽度度量（与展开/收起、行显隐无关），
        # 否则收起某个宽分组时页面 minimumSizeHint 会变小→导致窗口宽度跳动。
        content_w = 0
        for i in range(self.tabs.count()):
            pg = self.tabs.widget(i)
            try:
                if isinstance(pg, QScrollArea) and \
                        pg.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff:
                    inner = pg.widget()
                    im = inner.contentsMargins()
                    page_w = inner.minimumSizeHint().width()
                    for sec in inner.findChildren(CollapsibleSection):
                        bw = sec._body.minimumSizeHint().width() + 6  # 卡片内边距
                        page_w = max(page_w, bw + im.left() + im.right())
                    content_w = max(content_w, page_w)
            except Exception:
                pass
        w = max(tab_w, content_w + 24, 430)
        h = 620
        try:
            scr = (self.screen() or QApplication.primaryScreen()).availableGeometry()
            w = min(w, scr.width() - 40)
            h = min(h, scr.height() - 40)
        except Exception:
            pass
        self.setFixedSize(w, h)

    def _refit_soon(self):
        # 延迟到事件循环末尾重算，确保布局已根据展开/收起完成重新排列
        if not getattr(self, "_refit_pending", False):
            self._refit_pending = True
            QTimer.singleShot(60, self._do_refit)

    def _do_refit(self):
        self._refit_pending = False
        self._fit_width_to_tabs()

    def _update_swatch(self):
        c = QColor(self._bubble_color)
        if not c.isValid():
            c = QColor("#222222")
        self.bubble_swatch.setStyleSheet(
            "background:%s; border:1px solid #eea2bd; border-radius:3px;"
            % c.name())
        self.bubble_swatch.setToolTip("当前：%s" % c.name())

    def _pick_bubble_color(self):
        c = QColorDialog.getColor(
            QColor(self._bubble_color), self, "选择气泡文字颜色")
        if c.isValid():
            self._bubble_color = c.name()
            self._update_swatch()

    def _build_theme_sections(self, v):
        """主题与外观（主题色 + 圆角 + 透明度）合并为一个分组，加入给定布局。"""
        sec = CollapsibleSection("主题与外观", "🎨", expanded=True)
        self._theme_color = "#ff96bb"
        self.theme_swatch = QLabel()
        self.theme_swatch.setFixedSize(60, 24)
        theme_btn = QPushButton("更改主题色…")
        theme_btn.setObjectName("flat")
        theme_btn.clicked.connect(self._pick_theme_color)
        swatch_row = QWidget()
        sr = QHBoxLayout(swatch_row)
        sr.setContentsMargins(0, 0, 0, 0)
        sr.addWidget(self.theme_swatch)
        sr.addWidget(theme_btn)
        sr.addStretch(1)
        sec.add_row("主题颜色：", swatch_row)
        preset_row = QWidget()
        pr = QHBoxLayout(preset_row)
        pr.setContentsMargins(0, 0, 0, 0)
        pr.setSpacing(6)
        for c, name in (("#ff96bb", "淡粉"), ("#7bc4ff", "天蓝"),
                        ("#9be89b", "嫩绿"), ("#ffd97b", "暖黄"),
                        ("#c89bff", "薰衣草"), ("#ff8b8b", "珊瑚红")):
            b = QPushButton(name)
            b.setObjectName("flat")
            b.clicked.connect(lambda _=None, cc=c: self._set_theme_color(cc))
            pr.addWidget(b)
        pr.addStretch(1)
        sec.add_row("快捷预设：", preset_row)

        self.theme_radius = QSlider(Qt.Orientation.Horizontal)
        self.theme_radius.setRange(0, 20)
        self.theme_radius_label = QLabel()
        self.theme_radius.valueChanged.connect(
            lambda v: self.theme_radius_label.setText(f"{v}px"))
        r_row = QWidget(); rl = QHBoxLayout(r_row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.theme_radius); rl.addWidget(self.theme_radius_label)
        sec.add_row("控件圆角：", r_row)

        self.panel_opacity = QSlider(Qt.Orientation.Horizontal)
        self.panel_opacity.setRange(50, 100)
        self.panel_opacity_label = QLabel()
        self.panel_opacity.valueChanged.connect(
            lambda v: self.panel_opacity_label.setText(f"{v/100:.0%}"))
        p_row = QWidget(); pl = QHBoxLayout(p_row)
        pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(self.panel_opacity); pl.addWidget(self.panel_opacity_label)
        sec.add_row("面板透明度：", p_row)

        self.pet_opacity = QSlider(Qt.Orientation.Horizontal)
        self.pet_opacity.setRange(30, 100)
        self.pet_opacity_label = QLabel()
        self.pet_opacity.valueChanged.connect(
            lambda v: self.pet_opacity_label.setText(f"{v/100:.0%}"))
        e_row = QWidget(); el = QHBoxLayout(e_row)
        el.setContentsMargins(0, 0, 0, 0)
        el.addWidget(self.pet_opacity); el.addWidget(self.pet_opacity_label)
        sec.add_row("宠物透明度：", e_row)
        sec.add_hint("主题色影响所有面板/菜单/按钮配色；透明度可让宠物和面板半透明~")
        v.addWidget(sec)

    def _update_theme_swatch(self):
        c = QColor(self._theme_color)
        if not c.isValid():
            c = QColor("#ff96bb")
        self.theme_swatch.setStyleSheet(
            "background:%s; border:1px solid #ccc; border-radius:4px;" % c.name())

    def _pick_theme_color(self):
        c = QColorDialog.getColor(QColor(self._theme_color), self, "选择主题色")
        if c.isValid():
            self._set_theme_color(c.name())

    def _set_theme_color(self, color):
        self._theme_color = color
        self._update_theme_swatch()
        # 实时预览
        apply_theme(self, {**self.cfg, "theme_color": color,
                           "theme_radius": self.theme_radius.value(),
                           "panel_opacity": self.panel_opacity.value() / 100.0})

    def _build_about_tab(self, tabs):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        page = QWidget()
        scroll.setWidget(page)
        self.about_tab = scroll

        v = QVBoxLayout(page)
        v.setAlignment(Qt.AlignmentFlag.AlignTop)
        v.setSpacing(8)

        icon = cfgmod.app_icon_path()
        pix = QPixmap(icon).scaled(
            80, 80, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation) if icon else QPixmap()
        logo = QLabel()
        if not pix.isNull():
            logo.setPixmap(pix)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(logo)

        name = QLabel(
            f"<b style='color:#d6497a; font-size:16pt;'>{cfgmod.APP_NAME}</b>")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(name)

        cn = QLabel(
            f"<span style='color:#c07a93; font-size:11pt;'>{cfgmod.APP_CN_NAME}</span>")
        cn.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(cn)

        ver = QLabel(
            f"<span style='color:#8a5a72;'>版本 v{cfgmod.APP_VERSION}</span>")
        ver.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(ver)

        v.addWidget(self._hline())

        info = QLabel(
            f"<table cellpadding='4'>"
            f"<tr><td style='color:#8a5a72;'>作者：</td>"
            f"<td style='color:{hint_color(self.cfg)};'>{cfgmod.APP_AUTHOR}</td></tr>"
            f"<tr><td style='color:#8a5a72;'>邮箱：</td>"
            f"<td><a href='mailto:{cfgmod.APP_EMAIL}' style='color:#d6497a;'>"
            f"{cfgmod.APP_EMAIL}</a></td></tr>"
            f"<tr><td style='color:#8a5a72;'>官网：</td>"
            f"<td><a href='{cfgmod.APP_WEBSITE}' style='color:#d6497a;'>"
            f"{cfgmod.APP_WEBSITE}</a></td></tr>"
            f"</table>")
        info.setOpenExternalLinks(True)
        info.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        v.addWidget(info)

        v.addWidget(self._hline())

        repo_title = QLabel("<b style='color:#8a5a72;'>项目地址</b>")
        v.addWidget(repo_title)
        repos = QLabel(
            f"<table cellpadding='4'>"
            f"<tr><td style='color:#8a5a72;'>GitHub：</td>"
            f"<td><a href='{cfgmod.RELEASE_GITHUB}' style='color:#d6497a;'>"
            f"{cfgmod.RELEASE_GITHUB}</a></td></tr>"
            f"<tr><td style='color:#8a5a72;'>Gitee：</td>"
            f"<td><a href='{cfgmod.RELEASE_GITEE}' style='color:#d6497a;'>"
            f"{cfgmod.RELEASE_GITEE}</a></td></tr>"
            f"<tr><td style='color:#8a5a72;'>GitCode：</td>"
            f"<td><a href='{cfgmod.RELEASE_GITCODE}' style='color:#d6497a;'>"
            f"{cfgmod.RELEASE_GITCODE}</a></td></tr>"
            f"</table>")
        repos.setOpenExternalLinks(True)
        repos.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        v.addWidget(repos)

        v.addWidget(self._hline())

        self.update_status = QLabel(
            "<span style='color:#b08a9c;'>正在检查更新…</span>")
        self.update_status.setWordWrap(True)
        v.addWidget(self.update_status)

        btn_row = QHBoxLayout()
        self.btn_check_update = QPushButton("检查更新")
        self.btn_check_update.setObjectName("flat")
        self.btn_check_update.clicked.connect(self._manual_check_update)
        btn_row.addWidget(self.btn_check_update)
        btn_row.addStretch(1)
        v.addLayout(btn_row)

        v.addStretch(1)
        tabs.addTab(scroll, "关于")
        tabs.tabBar().setTabVisible(tabs.indexOf(scroll), False)
        self._hidden_tab_idx.add(tabs.indexOf(scroll))

    def _hline(self):
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color:#f7c9dc;")
        return line

    # ---------------- 插件 / 小游戏页 ----------------
    def _build_plugin_tab(self, tabs):
        pal = _color_family(self.cfg.get("theme_color", "#ff96bb"),
                            dark=bool(self.cfg.get("dark_mode", False)))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(10)

        title = QLabel("🧩 插件 / 小游戏")
        title.setStyleSheet(
            "font:bold 12pt 'Microsoft YaHei'; color:%s; border:none;" % pal["text"])
        v.addWidget(title)
        tip = QLabel(
            "插件是可选的“增量包”：本体只内置加载框架，不含任何插件代码与素材，"
            "所以不会让本体变大。想玩再下载/安装，不玩可随时删除。安装后，"
            "鼠标右键点小狗就能在“桌宠 ↔ 游戏”之间切换。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:%s; font-size:9pt; border:none;" % pal["hint"])
        v.addWidget(tip)
        guide = QLabel(
            "想自己做大/二创插件？先看 <a href='%s' style='color:%s;'>《插件开发规范》</a>"
            "，用仓库里的模板 10 分钟就能跑起来。" % (cfgmod.PLUGIN_GUIDE_URL, pal["main"]))
        guide.setOpenExternalLinks(True)
        guide.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        guide.setStyleSheet("font-size:9pt; border:none;")
        v.addWidget(guide)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        btn_online = QPushButton("🔄 刷新在线列表")
        btn_online.setObjectName("flat")
        btn_online.clicked.connect(self._plugin_refresh_remote)
        btn_add = QPushButton("⬇ 安装插件…")
        btn_add.setObjectName("flat")
        btn_add.clicked.connect(self._plugin_add_menu)
        btn_pub = QPushButton("📤 发布插件…")
        btn_pub.setObjectName("flat")
        btn_pub.setToolTip("把做好的插件打成 zip 并生成提交到仓库 index.json 的片段")
        btn_pub.clicked.connect(self._plugin_publish)
        btn_more = QPushButton("更多 ▾")
        btn_more.setObjectName("flat")
        btn_more.clicked.connect(self._plugin_more_menu)
        for b in (btn_online, btn_add, btn_pub, btn_more):
            bar.addWidget(b)
        bar.addStretch(1)
        v.addLayout(bar)

        self._plugin_remote_lbl = QLabel("在线仓库：未加载，点「刷新在线列表」获取社区插件")
        self._plugin_remote_lbl.setStyleSheet(
            "color:%s; font-size:8.5pt; border:none;" % pal["hint"])
        v.addWidget(self._plugin_remote_lbl)

        self._plugin_list = QVBoxLayout()
        self._plugin_list.setSpacing(10)
        v.addLayout(self._plugin_list)
        v.addStretch(1)

        scroll.setWidget(host)
        tabs.addTab(scroll, "插件")
        self._plugin_tab_index = tabs.indexOf(scroll)
        self.plugin_tab = scroll
        # 不在顶部标签栏占位：改由左下角“插件”按钮进入（与“关于”一致）
        tabs.tabBar().setTabVisible(self._plugin_tab_index, False)
        self._hidden_tab_idx.add(self._plugin_tab_index)
        self._refresh_plugin_list()

    def _refresh_plugin_list(self):
        self._clear_layout(self._plugin_list)
        if getattr(self, "plugins", None) is None:
            lab = QLabel("插件系统不可用。")
            lab.setStyleSheet("color:#c0392b; border:none;")
            self._plugin_list.addWidget(lab)
            return
        try:
            self._plugin_remote_lbl.setText(
                self.plugins.remote_note
                or "在线仓库：未加载，点「刷新在线列表」获取社区插件")
        except Exception:
            pass
        try:
            entries = self.plugins.catalog()
        except Exception as e:
            lab = QLabel("读取插件失败：%s" % e)
            lab.setStyleSheet("color:#c0392b; border:none;")
            self._plugin_list.addWidget(lab)
            return
        for ent in entries:
            self._plugin_list.addWidget(self._make_plugin_card(ent))

    def _clear_layout(self, lay):
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

    def _make_plugin_card(self, ent):
        pal = _color_family(self.cfg.get("theme_color", "#ff96bb"),
                            dark=bool(self.cfg.get("dark_mode", False)))
        card = QFrame()
        card.setObjectName("pluginCard")
        card.setStyleSheet(
            "#pluginCard { background:%s; border:1px solid %s; border-radius:12px; }"
            % (pal["flat"], pal["border2"]))
        h = QHBoxLayout(card)
        h.setContentsMargins(14, 12, 14, 12)
        h.setSpacing(12)

        icon = QLabel()
        icon.setFixedSize(52, 52)
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ip = ent.get("icon_path")
        shown = False
        if ip and os.path.isfile(ip):
            pm = QPixmap(ip)
            if not pm.isNull():
                icon.setPixmap(pm.scaled(
                    48, 48, Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation))
                shown = True
        if not shown:
            icon.setText("🧩")
            icon.setStyleSheet("font:24pt 'Segoe UI Emoji'; border:none;")
        h.addWidget(icon)

        mid = QVBoxLayout()
        mid.setSpacing(2)
        name = QLabel("<b style='font-size:11pt;color:%s;'>%s</b>"
                      "  <span style='color:%s;font-size:8.5pt;'>v%s</span>"
                      % (pal["text"], ent["name"], pal["hint"],
                         ent.get("version", "")))
        name.setStyleSheet("border:none;")
        mid.addWidget(name)
        src = {"remote": "🌐 社区插件", "builtin": "📦 官方内置",
               "local": "💻 本地安装"}.get(ent.get("source", ""), "")
        author = ent.get("author") or ""
        tag = " · ".join(x for x in (src, ("作者 " + author) if author else "") if x)
        if tag:
            tl = QLabel(tag)
            tl.setStyleSheet("color:%s; font-size:8pt; border:none;" % pal["hint"])
            mid.addWidget(tl)
        desc = QLabel(ent.get("description", ""))
        desc.setWordWrap(True)
        desc.setStyleSheet("color:%s; font-size:9pt; border:none;" % pal["hint"])
        mid.addWidget(desc)
        if not ent["installed"]:
            st, stc = "⚪ 未安装", pal["hint"]
        elif ent["enabled"]:
            st, stc = "🟢 已安装 · 已启用", pal["text2"]
        else:
            st, stc = "🟡 已安装 · 已禁用", pal["hint"]
        status = QLabel(st)
        status.setStyleSheet("color:%s; font-size:8.5pt; border:none;" % stc)
        mid.addWidget(status)
        h.addLayout(mid, 1)

        btns = QVBoxLayout()
        btns.setSpacing(6)
        if not ent["installed"]:
            if ent.get("download_url"):
                b = QPushButton("⬇ 下载安装")
                b.setObjectName("flat")
                b.clicked.connect(
                    lambda _=False, u=ent["download_url"]: self._plugin_install_url(u))
                btns.addWidget(b)
            b2 = QPushButton("从本地安装")
            b2.setObjectName("flat")
            b2.clicked.connect(self._plugin_install_local)
            btns.addWidget(b2)
        else:
            ob = QPushButton("▶ 打开")
            ob.setObjectName("flat")
            ob.setEnabled(bool(ent["enabled"]))
            ob.clicked.connect(lambda _=False, p=ent["id"]: self._plugin_open(p))
            btns.addWidget(ob)
            tb = QPushButton("禁用" if ent["enabled"] else "启用")
            tb.setObjectName("flat")
            tb.clicked.connect(
                lambda _=False, p=ent["id"], on=(not ent["enabled"]):
                    self._plugin_toggle(p, on))
            btns.addWidget(tb)
            ub = QPushButton("卸载")
            ub.setObjectName("flat")
            ub.clicked.connect(lambda _=False, p=ent["id"]: self._plugin_uninstall(p))
            btns.addWidget(ub)
            fb = QPushButton("📂 文件夹")
            fb.setObjectName("flat")
            fb.setToolTip("打开插件目录（二创改代码/素材后可「从文件夹重装」生效）")
            fb.clicked.connect(lambda _=False, p=ent["id"]: self._plugin_open_folder(p))
            btns.addWidget(fb)
        h.addLayout(btns)
        return card

    def _plugin_add_menu(self):
        from PyQt6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction("从 zip 包安装…", lambda checked=False: self._plugin_install_local())
        m.addAction("从已解压文件夹安装…", lambda checked=False: self._plugin_install_folder())
        btn = self.sender()
        if isinstance(btn, QPushButton):
            pos = btn.mapToGlobal(btn.rect().bottomLeft())
        else:
            pos = self.mapToGlobal(self.rect().center())
        m.exec(pos)

    def _plugin_install_local(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "选择插件包（zip）", "", "插件包 (*.zip);;所有文件 (*)")
        if path:
            self._do_install(path)

    def _plugin_install_folder(self):
        path = QFileDialog.getExistingDirectory(self, "选择已解压的插件文件夹")
        if path:
            self._do_install(path)

    def _do_install(self, path):
        try:
            info = self.plugins.install_from_path(path)
        except Exception as e:
            QMessageBox.warning(self, "安装失败", str(e))
            return
        self._refresh_plugin_list()
        QMessageBox.information(
            self, "安装成功",
            "已安装插件：%s\n\n现在可以右键小狗打开它了。"
            % (info.name if info else path))

    def _plugin_install_url(self, url):
        if not url:
            QMessageBox.information(self, "提示", "该插件未配置下载地址，请用“从本地安装”。")
            return
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        task = _PluginTask(self.plugins.install_from_url, url)
        self._plugin_tasks = getattr(self, "_plugin_tasks", set())
        self._plugin_tasks.add(task)

        def ok(_res):
            QApplication.restoreOverrideCursor()
            self._plugin_tasks.discard(task)
            self._refresh_plugin_list()
            QMessageBox.information(self, "安装成功", "插件已下载并安装。")

        def err(msg):
            QApplication.restoreOverrideCursor()
            self._plugin_tasks.discard(task)
            QMessageBox.warning(self, "下载/安装失败", msg)

        task.done.connect(ok)
        task.failed.connect(err)
        task.start()

    def _plugin_toggle(self, pid, on):
        self.plugins.set_enabled(pid, on)
        self._refresh_plugin_list()

    def _plugin_uninstall(self, pid):
        info = self.plugins.get(pid)
        name = info.name if info else pid
        ret = QMessageBox.question(
            self, "卸载插件",
            "确定卸载「%s」吗？\n（卸载不会删除游戏存档，除非勾选下方选项）"
            % name,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        purge = QMessageBox.question(
            self, "同时删除存档？",
            "要不要一并删除「%s」的本地存档数据？" % name,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        self.plugins.uninstall(pid, purge_data=(purge == QMessageBox.StandardButton.Yes))
        self._refresh_plugin_list()

    def _plugin_open(self, pid):
        # 关闭设置（解除模态）后由本体启动插件窗口
        self.launch_plugin_requested.emit(pid)
        self.accept()

    # ---------------- 在线仓库 / 发布 ----------------
    def _plugin_refresh_remote(self):
        if getattr(self, "plugins", None) is None:
            return
        self._plugin_remote_lbl.setText("在线仓库：正在加载…")
        task = _PluginTask(self.plugins.refresh_remote)
        self._plugin_tasks = getattr(self, "_plugin_tasks", set())
        self._plugin_tasks.add(task)

        def ok(_n):
            self._plugin_tasks.discard(task)
            self._refresh_plugin_list()

        def err(_msg):
            self._plugin_tasks.discard(task)
            try:
                self._plugin_remote_lbl.setText(
                    "在线仓库：加载失败（检查网络，或仓库是否已放 index.json）")
            except Exception:
                pass

        task.done.connect(ok)
        task.failed.connect(err)
        task.start()

    def _open_plugins_dir(self):
        from . import pluginapi
        d = pluginapi.plugins_root()
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(d))

    def _plugin_more_menu(self):
        from PyQt6.QtWidgets import QMenu
        m = QMenu(self)
        m.addAction("🌐 打开插件仓库（Gitee）",
                    lambda checked=False: QDesktopServices.openUrl(QUrl(cfgmod.PLUGIN_REPO_URL)))
        m.addAction("📂 打开本地插件目录",
                    lambda checked=False: self._open_plugins_dir())
        m.addAction("📘 插件开发规范",
                    lambda checked=False: QDesktopServices.openUrl(QUrl(cfgmod.PLUGIN_GUIDE_URL)))
        m.addSeparator()
        # 右键便捷显示插件入口（小狗右键菜单 + 托盘「小游戏/插件」子菜单），默认开
        act = m.addAction("右键菜单便捷显示")
        act.setCheckable(True)
        act.setChecked(bool(self.cfg.get("plugins_pet_menu", True)))
        act.setToolTip("控制小狗右键菜单与托盘「小游戏/插件」子菜单是否显示；关闭后仍可在本页「▶ 打开」插件")
        act.toggled.connect(
            lambda on: self.cfg.__setitem__("plugins_pet_menu", bool(on)))
        btn = self.sender()
        if isinstance(btn, QPushButton):
            pos = btn.mapToGlobal(btn.rect().bottomLeft())
        else:
            pos = self.mapToGlobal(self.rect().center())
        m.exec(pos)

    def _plugin_open_folder(self, pid):
        info = self.plugins.get(pid) if getattr(self, "plugins", None) else None
        if info and os.path.isdir(info.path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(info.path))

    def _plugin_publish(self):
        """发布助手：选插件 → 打 zip → 给出上传步骤 + index.json 片段。"""
        import json
        from . import pluginapi
        installed = self.plugins.installed() if getattr(self, "plugins", None) else []
        opts = [("已安装：%s（%s）" % (i.name, i.id), i.path) for i in installed]
        opts.append(("选择本地插件文件夹…", ""))
        labels = [o[0] for o in opts]
        item, ok = QInputDialog.getItem(
            self, "发布插件 · 选择来源", "要发布/打包哪个插件？", labels, 0, False)
        if not ok:
            return
        src_dir = ""
        for lbl, path in opts:
            if lbl == item:
                src_dir = path
                break
        if not src_dir:
            src_dir = QFileDialog.getExistingDirectory(
                self, "选择插件文件夹（内含 plugin.json）")
            if not src_dir:
                return
        mf = os.path.join(src_dir, "plugin.json")
        if not os.path.isfile(mf):
            QMessageBox.warning(self, "无法发布", "该文件夹缺少 plugin.json，不是合法插件目录。")
            return
        try:
            with open(mf, "r", encoding="utf-8") as f:
                meta = json.load(f) or {}
        except Exception as e:
            QMessageBox.warning(self, "无法发布", "plugin.json 读取失败：%s" % e)
            return
        pid = str(meta.get("id") or os.path.basename(src_dir))
        ver = str(meta.get("version") or "1.0.0")
        zip_name = "%s-v%s.zip" % (pid, ver)
        home = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(home):
            home = os.path.expanduser("~")
        dest, ok2 = QFileDialog.getSaveFileName(
            self, "保存插件发布包（zip）", os.path.join(home, zip_name),
            "Zip 包 (*.zip)")
        if not dest:
            return
        if not dest.lower().endswith(".zip"):
            dest += ".zip"
        try:
            pluginapi.make_release_zip(src_dir, dest)
        except Exception as e:
            QMessageBox.warning(self, "打包失败", str(e))
            return
        snippet = json.dumps({
            "id": pid,
            "name": meta.get("name", pid),
            "version": ver,
            "author": meta.get("author", ""),
            "description": meta.get("description", ""),
            "entry": meta.get("entry", "main.py"),
            "menu_label": meta.get("menu_label", "▶ " + str(meta.get("name", pid))),
            "file": "plugins/" + zip_name,
            "min_app": meta.get("min_app", cfgmod.APP_VERSION),
        }, ensure_ascii=False, indent=2)
        dlg = QDialog(self)
        dlg.setWindowTitle("打包完成 · 提交到插件仓库")
        apply_theme(dlg, self.cfg)
        dv = QVBoxLayout(dlg)
        lab = QLabel(
            "已生成发布包：<b>%s</b><br><br>"
            "① 把这个 zip 上传到仓库 <code>plugins/</code> 目录；<br>"
            "② 编辑仓库根目录 <code>index.json</code>，把下面的片段加入 "
            "<code>\"plugins\": [ … ]</code> 数组；<br>"
            "③ 提交后，所有人在「设置 → 插件 → 刷新在线列表」就能看到。<br><br>"
            "仓库：<a href='%s'>%s</a>" % (dest, cfgmod.PLUGIN_REPO_URL,
                                            cfgmod.PLUGIN_REPO_URL))
        lab.setTextFormat(Qt.TextFormat.RichText)
        lab.setOpenExternalLinks(True)
        lab.setWordWrap(True)
        dv.addWidget(lab)
        edit = QPlainTextEdit()
        edit.setPlainText(snippet)
        edit.setReadOnly(True)
        edit.setFixedHeight(210)
        dv.addWidget(edit)
        row = QHBoxLayout()
        cp = QPushButton("复制片段")
        cp.setObjectName("flat")
        cp.clicked.connect(lambda: QApplication.clipboard().setText(snippet))
        done = QPushButton("完成")
        done.clicked.connect(dlg.accept)
        row.addWidget(cp)
        row.addStretch(1)
        row.addWidget(done)
        dv.addLayout(row)
        dlg.resize(540, 430)
        dlg.exec()

    def _build_link_tab(self, tabs):
        """联动视效页（v2.2.3）：桌面特效独立成栏，固定放在最后一栏。

        未联动（PawLiveWall 未运行）：只显示一张柔和的引导卡片，安静地邀请
        用户下载 PawLiveWall；不再给标签加 🔒、也不再弹窗拦截点击，所以这一栏
        在没装壁纸软件时既不突兀、也不抢眼。已联动：卡片隐藏，正常显示特效开关。
        与 LinkMonitor 同一机制（1s 轮询互斥体），面板开着时也会实时切换。
        """
        pal = _color_family(self.cfg.get("theme_color", "#ff96bb"),
                            dark=bool(self.cfg.get("dark_mode", False)))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        host = QWidget()
        v = QVBoxLayout(host)
        v.setContentsMargins(8, 8, 8, 8)

        # 未联动时的柔和引导卡片（联动后自动隐藏）：淡底、圆角、居中，语气友好
        self._link_card = QFrame()
        self._link_card.setObjectName("linkCard")
        self._link_card.setStyleSheet(
            "#linkCard { background:%s; border:1px solid %s; border-radius:14px; }"
            % (pal['flat'], pal['border2']))
        cv = QVBoxLayout(self._link_card)
        cv.setContentsMargins(18, 22, 18, 22)
        cv.setSpacing(8)
        card_title = QLabel("🐾 想让小狗在桌面上也动起来？")
        card_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_title.setStyleSheet(
            "color:%s; font:bold 11pt 'Microsoft YaHei'; background:transparent; border:none;"
            % pal['text'])
        card_hint = QLabel(
            "打开 PawLiveWall 动态壁纸，小狗会自动与壁纸联动，\n"
            "这里就会解锁更多桌面特效。")
        card_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card_hint.setWordWrap(True)
        card_hint.setStyleSheet(
            "color:%s; font-size:9.5pt; background:transparent; border:none;" % pal['hint'])
        cv.addWidget(card_title)
        cv.addWidget(card_hint)
        self._btn_download = QPushButton("下载 PawLiveWall")
        self._btn_download.setObjectName("flat")
        self._btn_download.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_download.clicked.connect(self._open_pawlivewall_page)
        self._btn_download.setVisible(bool(PAWLIVEWALL_URL))
        dl_row = QHBoxLayout()
        dl_row.addStretch(1)
        dl_row.addWidget(self._btn_download)
        dl_row.addStretch(1)
        cv.addLayout(dl_row)
        # 卡片页：未联动时在整栏高度里垂直居中显示引导卡片。
        card_page = QWidget()
        cp = QVBoxLayout(card_page)
        cp.setContentsMargins(0, 0, 0, 0)
        cp.addStretch(1)
        cp.addWidget(self._link_card)
        cp.addStretch(1)

        # 已联动才显示的内容：说明 + 特效开关组（整体一个容器，方便一键显隐）
        self._link_body = QWidget()
        lb = QVBoxLayout(self._link_body)
        lb.setContentsMargins(0, 0, 0, 0)
        self._link_intro = QLabel(
            "小狗与 PawLiveWall 动态壁纸同时运行时，桌面上出现的联动特效在这里开关；"
            "只开桌宠时特效不渲染。")
        self._link_intro.setWordWrap(True)
        self._link_intro.setStyleSheet(
            "color:%s; font-size:8pt;" % hint_color(self.cfg))
        lb.addWidget(self._link_intro)
        self._fx_group = self._build_fx_group()
        lb.addWidget(self._fx_group)
        lb.addStretch(1)
        # 用堆叠页切换：未联动=卡片页(垂直居中)，已联动=正文页(顶部对齐)。
        # 关键修复：不再把卡片上下两个 addStretch 常驻宿主布局——联动后卡片虽隐藏，
        # 但两个 stretch 仍会撑开，把正文顶到底部，导致「联动视效」页顶部一大片空白。
        self._link_stack = QStackedWidget()
        self._link_stack.addWidget(card_page)        # index 0：引导卡片
        self._link_stack.addWidget(self._link_body)  # index 1：特效开关
        v.addWidget(self._link_stack, 1)
        scroll.setWidget(host)

        tabs.addTab(scroll, "联动视效")
        self._link_tab_idx = tabs.indexOf(scroll)
        self._link_online = None     # None = 尚未初始化
        self._refresh_link_tab()
        self._link_timer = QTimer(self)
        self._link_timer.setInterval(1000)
        self._link_timer.timeout.connect(self._refresh_link_tab)
        self._link_timer.start()

    def _refresh_link_tab(self, force=False):
        """按 PawLiveWall 在线状态切换「联动视效」栏的锁定/解锁外观。

        force=True 跳过变化检测直接应用（_load 会经 toggled 信号重新启用
        特效子开关，装载配置后必须强制恢复锁定外观）。
        """
        idx = getattr(self, "_link_tab_idx", -1)
        if idx < 0:
            return
        online = False
        try:
            online = bool(winapi.IS_WINDOWS
                          and winapi.mutex_exists(PAWLIVEWALL_MUTEX))
        except Exception:
            pass
        if online == self._link_online and not force:
            return
        self._link_online = online
        # 未联动：只显示柔和引导卡片；已联动：显示特效开关。
        # 标签文字始终为「联动视效」，不加 🔒、不弹窗拦截，安静待在最后一栏不突兀。
        self._link_stack.setCurrentIndex(1 if online else 0)
        self.fx_on.setEnabled(online)
        self._toggle_fx_children(online and self.fx_on.isChecked())

    @staticmethod
    def _open_pawlivewall_page():
        """打开 PawLiveWall 下载页（发布地址在 panels.PAWLIVEWALL_URL）。"""
        if PAWLIVEWALL_URL:
            QDesktopServices.openUrl(QUrl(PAWLIVEWALL_URL))

    def _build_fx_group(self):
        """桌面特效分组（v2.2.0）：渲染在桌宠透明层上，与动态壁纸视觉融合。"""
        box = QGroupBox("桌面特效（全屏时自动暂停）")
        grid = QGridLayout(box)
        grid.setContentsMargins(10, 14, 10, 10)
        grid.setSpacing(4)

        self.fx_on = QCheckBox("特效总开关")
        grid.addWidget(self.fx_on, 0, 0)


        specs = (
            ("fx_halo", "小狗光环"),
            ("fx_step", "脚步粒子"),
            ("fx_mouse_trail", "鼠标吸引粒子"),
            ("fx_click_burst", "点击爆炸"),
            ("fx_cursor_light", "鼠标光源（桌面层）"),
            ("fx_shadow", "脚下阴影"),
            ("fx_trail", "能量尾巴"),
            ("fx_petals", "花瓣互动"),
            ("fx_leaves", "落叶互动"),
            ("fx_fireflies", "萤火虫聚集"),
            ("fx_butterfly", "蝴蝶追踪"),
            ("fx_music", "音乐律动"),
        )
        self._fx_bindings = []
        for i, (key, label) in enumerate(specs):
            cb = QCheckBox(label)
            grid.addWidget(cb, 1 + i // 3, i % 3)
            self._fx_bindings.append((cb, key))

        fps_row = QWidget()
        fps_lay = QHBoxLayout(fps_row)
        fps_lay.setContentsMargins(0, 0, 0, 0)
        self.fx_fps = QComboBox()
        for v in (15, 24, 30, 60):
            self.fx_fps.addItem("%d FPS" % v, v)
        fps_lay.addWidget(self.fx_fps)
        fps_lay.addStretch(1)
        grid.addWidget(QLabel("特效帧率："), 5, 0)
        grid.addWidget(fps_row, 5, 1, 1, 2)

        tip = QLabel("特效只在与 PawLiveWall 双开联动时渲染；画在壁纸之上，不抢焦点也不挡点击，"
                     "全屏应用时自动暂停。鼠标光源画在桌面层：盖在壁纸上，"
                     "被打开的窗口遮挡时自动不可见。花瓣 / 落叶 / 萤火虫 / 蝴蝶较耗资源，"
                     "建议按需开启。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:%s; font-size:8pt;" % hint_color(self.cfg))
        grid.addWidget(tip, 6, 0, 1, 3)

        self.fx_on.toggled.connect(self._toggle_fx_children)
        return box

    def _toggle_fx_children(self, on):
        for cb, _ in getattr(self, "_fx_bindings", ()):
            cb.setEnabled(on)
        if hasattr(self, "fx_fps"):
            self.fx_fps.setEnabled(on)


    def _show_about(self):
        """关于：切换到设置对话框内的“关于”标签页。"""
        # 隐藏彩蛋：5 秒内连续点击 5 次算触发一次彩蛋（可重复触发计数）
        now = time.time()
        if now - self._last_about_click > 5.0:
            self._about_clicks = 0
        self._last_about_click = now
        self._about_clicks += 1
        if self._about_clicks % 5 == 0:
            diary_mod.get_db().bump_egg()
            if not self._milestone_emitted:
                self._milestone_emitted = True
                self.milestone_unlocked.emit()
        self.tabs.setCurrentWidget(self.about_tab)

    def _show_plugins(self):
        """插件：切到设置内的“插件”页（该页不在顶部标签栏，由左下角按钮进入）。"""
        self._refresh_plugin_list()
        if getattr(self, "plugin_tab", None) is not None:
            self.tabs.setCurrentWidget(self.plugin_tab)

    def _auto_check_update(self):
        if self._auto_checked:
            return
        self._auto_checked = True
        self._check_update_request(silent=True)

    def _manual_check_update(self):
        self.btn_check_update.setEnabled(False)
        self.update_status.setText(
            "<span style='color:#b08a9c;'>正在检查更新…</span>")
        self._check_update_request(silent=False)

    def _check_update_request(self, silent):
        self._update_silent = silent
        urls = list(getattr(cfgmod, "UPDATE_CHECK_URLS", []) or [])
        if not urls:
            single = (getattr(cfgmod, "UPDATE_CHECK_URL", "") or "").strip()
            if single:
                urls = [single]
        self._update_urls = [u for u in urls if u]
        self._update_idx = 0
        if not self._update_urls:
            if not silent:
                self.update_status.setText(
                    "<span style='color:#8a5a72;'>无法检查更新。</span>")
                self.btn_check_update.setEnabled(True)
            return
        self._check_next_update()

    def _check_next_update(self):
        if self._update_idx >= len(self._update_urls):
            # 全部源都失败
            self._on_update_failed(self._update_silent)
            self.btn_check_update.setEnabled(True)
            if self._net is not None:
                self._net.deleteLater()
                self._net = None
            return
        url = self._update_urls[self._update_idx]
        self._update_idx += 1
        if self._net is None:
            self._net = QNetworkAccessManager(self)
        req = QNetworkRequest(QUrl(url))
        req.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader,
                      "LineDogPet-Updater")
        self._update_reply = self._net.get(req)
        self._update_reply.finished.connect(self._on_update_reply)

    def _on_update_reply(self):
        reply = self._update_reply
        self._update_reply = None
        if reply is None:
            return
        silent = self._update_silent
        got_version = False
        try:
            if reply.error() == reply.NetworkError.NoError:
                import json
                data = json.loads(bytes(reply.readAll()).decode("utf-8", "ignore"))
                tag = (data.get("tag_name") or "").strip()
                latest = cfgmod.parse_version_tag(tag)
                if latest:
                    got_version = True
                    current = cfgmod.APP_VERSION
                    if self._compare_version(latest, current) > 0:
                        self.update_status.setText(
                            f"<b style='color:#d6497a;'>发现新版本 v{latest}！</b>"
                            f"<span style='color:#8a5a72;'>（当前 v{current}）</span>")
                        self._prompt_update(latest)
                    else:
                        self.update_status.setText(
                            f"<span style='color:#8a5a72;'>当前已是最新版本 v{current}。</span>")
        except Exception:
            pass
        finally:
            reply.deleteLater()
            if got_version:
                self.btn_check_update.setEnabled(True)
                if self._net is not None:
                    self._net.deleteLater()
                    self._net = None
            else:
                # 当前源失败：尝试下一个源
                self._check_next_update()

    def _on_update_failed(self, silent):
        if silent:
            self.update_status.setText(
                f"<span style='color:#b08a9c;'>版本 v{cfgmod.APP_VERSION}"
                "（自动检查更新失败，可稍后手动重试）</span>")
        else:
            self.update_status.setText(
                "<span style='color:#c0507a;'>检查更新失败，请检查网络后重试，"
                "或直接到项目主页下载最新版。</span>")

    @staticmethod
    def _compare_version(a, b):
        def parse(s):
            parts = []
            for p in s.split("."):
                try:
                    parts.append(int(p))
                except ValueError:
                    parts.append(0)
            return parts
        pa, pb = parse(a), parse(b)
        n = max(len(pa), len(pb))
        pa += [0] * (n - len(pa))
        pb += [0] * (n - len(pb))
        for x, y in zip(pa, pb):
            if x != y:
                return x - y
        return 0

    def _prompt_update(self, latest):
        msg = QMessageBox(self)
        msg.setWindowTitle("发现新版本")
        msg.setStyleSheet(STYLE)
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(
            f"<b style='color:#d6497a;'>LineDogPet v{latest} 已发布！</b><br><br>"
            f"<span style='color:#8a5a72;'>当前版本：v{cfgmod.APP_VERSION}</span><br>"
            "<span style='color:#8a5a72;'>点击下方按钮前往下载更新：</span>")
        btn_gitee = msg.addButton("去 Gitee 下载",
                                  QMessageBox.ButtonRole.AcceptRole)
        btn_github = msg.addButton("去 GitHub 下载",
                                   QMessageBox.ButtonRole.AcceptRole)
        msg.addButton("稍后再说", QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked is btn_gitee:
            QDesktopServices.openUrl(QUrl(cfgmod.RELEASE_GITEE))
        elif clicked is btn_github:
            QDesktopServices.openUrl(QUrl(cfgmod.RELEASE_GITHUB))


    def _on_provider(self, _idx):
        val = self.provider.currentData()
        base, model, _label = ai_mod.PROVIDERS.get(val, ("", "", ""))
        if val != "custom":
            self.ai_base.setText(base)
            self.ai_model.setPlaceholderText(model)
            if not self.ai_model.text().strip():
                self.ai_model.setText(model)

    # ---------------- 深色模式切换 ----------------
    def _update_dark_btn(self):
        """右上角太阳/月亮按钮跟随当前模式。"""
        if not hasattr(self, "dark_btn"):
            return
        on = bool(self.cfg.get("dark_mode", False))
        self.dark_btn.setText("🌙" if not on else "☀️")
        self.dark_btn.setToolTip(
            "当前浅色，点击切换到深色模式" if not on
            else "当前深色，点击切换到浅色模式")

    def _toggle_dark(self):
        """点一下切换软件深色模式，立即应用到整个设置窗口并广播。"""
        self.cfg["dark_mode"] = not bool(self.cfg.get("dark_mode", False))
        apply_theme(self, self.cfg)
        self._update_dark_btn()
        self.dark_mode_changed.emit(bool(self.cfg["dark_mode"]))

    def _set_combo(self, combo, value):
        for i in range(combo.count()):
            if combo.itemData(i) == value:
                combo.setCurrentIndex(i)
                return

    # ---------------- 自定义角色：添加 / 删除 / 上传表情 ----------------
    def _rebuild_char_combo(self, select=None):
        """按 pets/ 目录重建角色下拉框（内置 + 自定义），保留选中项。"""
        prev = select or self.char.currentData() or "xiaobai"
        self.char.blockSignals(True)
        self.char.clear()
        for disp, cid in self.assets.char_display_pairs():
            if cid in ("bongo", "shared"):
                continue
            self.char.addItem(disp, cid)
        self._set_combo(self.char, prev)
        self.char.blockSignals(False)
        self._refresh_char_status()

    def _refresh_char_status(self):
        # 切换形象时同步 AI 人设编辑框（先保存上一个形象的编辑内容）
        self._sync_persona_box()
        ch = self.char.currentData()
        custom = bool(ch) and self.assets.is_custom(ch)
        # 默认角色时隐藏上传表情/删除/素材状态等自定义栏；"添加"入口始终保留
        self.btn_del_char.setVisible(custom)
        self.char_status.setVisible(custom)
        self.up_row.setVisible(custom)
        _ur = getattr(self, "_upload_row", None)
        if _ur is not None:
            _ur.setVisible(custom)
        if not ch:
            self.char_status.setText("")
            return
        counts = self.assets.action_counts(ch)
        total = sum(counts.values())
        if not custom:
            self.char_status.setText("内置角色，素材完整，无需管理")
            return
        if total <= 0:
            self.char_status.setText(
                "新角色还没有表情素材：选好动作点「上传 GIF」添加动画，"
                "建议先传「待机 idle」。自定义角色与默认角色互不影响。")
            return
        parts = "  ".join(
            f"{ACTION_CN.get(a, a)}×{n}" for a in ACTIONS
            if (n := counts.get(a, 0)) > 0)
        self.char_status.setText(f"已有素材：{parts}")

    # ---------------- AI 人设：跟形象走，可分别自定义 ----------------
    def _sync_persona_box(self):
        """把 AI 人设编辑框切到当前形象的（已编辑/已保存/内置默认）人设。

        切换前先把上一个形象正在编辑的内容存进 _persona_edits 缓存，
        这样在设置里给每个形象各写各的人设互不覆盖。
        """
        if not hasattr(self, "ai_persona_box"):
            return
        old = getattr(self, "_active_persona_char", None)
        if old:
            self._persona_edits[old] = self.ai_persona_box.toPlainText()
        key = self.char.currentData() or "xiaobai"
        if key in self._persona_edits:
            txt = self._persona_edits[key]
        else:
            txt = (self.cfg.get("ai_personas") or {}).get(key, "")
        self.ai_persona_box.setPlainText(txt)
        self._active_persona_char = key
        name = ai_mod.character_display_name(key)
        self.ai_persona_hint.setText(
            "已为「%s」自定义人设，AI 回复会按它来。" % name
            if txt and txt.strip()
            else "「%s」使用内置默认人设，可直接在上面写自己的。" % name)

    def _collect_personas(self):
        """把设置里各形象的人设内容写回配置（空内容不保存＝用内置默认）。"""
        if hasattr(self, "ai_persona_box") and getattr(self, "_active_persona_char", None):
            self._persona_edits[self._active_persona_char] = self.ai_persona_box.toPlainText()
        out = {}
        for k, v in self._persona_edits.items():
            v = (v or "").strip()
            if v:
                out[k] = v
        return out

    def _add_character(self):
        name, ok = QInputDialog.getText(
            self, "添加自定义角色", "给新伙伴起个名字（会成为素材文件夹名）：")
        name = (name or "").strip()
        if not ok or not name:
            return
        folder = _safe_folder(name)
        if not folder:
            QMessageBox.warning(self, "添加失败", "名字不能是空的/全符号，请换个名字。")
            return
        if folder in ("xiaobai", "xiaojimao", "duo", "shared") or self.assets.has_character(folder):
            QMessageBox.warning(self, "添加失败", "已存在同名角色或不允许用这个名称，请换一个。")
            return
        base = os.path.join(cfgmod.EXE_DIR, "pets", folder)
        try:
            os.makedirs(base, exist_ok=True)
            for act in ACTIONS:          # 预建全部动作文件夹，方便直接传图
                os.makedirs(os.path.join(base, act), exist_ok=True)
        except Exception:
            QMessageBox.warning(self, "添加失败", "创建角色文件夹失败，请检查权限。")
            return
        self.assets.refresh()
        self._rebuild_char_combo(select=folder)
        self.preview_character_changed.emit(folder)
        QMessageBox.information(
            self, "添加成功",
            f"角色「{folder}」已创建。\n\n下一步：\n选中它 → 选一个动作 → 点「上传 GIF」\n"
            "建议先传「待机 idle」，再逐步补充走路、开心等动作。\n"
            "每个动作可以放多张 gif，播放时会随机挑选。")

    def _del_character(self):
        ch = self.char.currentData()
        if not ch or not self.assets.is_custom(ch):
            QMessageBox.information(self, "提示", "只有自定义角色可以删除，默认角色保留。")
            return
        ret = QMessageBox.question(
            self, "删除角色",
            f"确定删除自定义角色「{ch}」吗？\n其全部素材文件都会被移除，该操作不可恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        # 先切换预览到小白，释放该角色 GIF 文件占用（QMovie 持有文件锁）
        if self.cfg.get("character") == ch:
            self.cfg["character"] = "xiaobai"
        self.preview_character_changed.emit("xiaobai")
        # 延迟删除：等主宠物 QMovie 释放文件后再删，避免 rmtree 删不掉
        self._pending_delete_char = ch
        QTimer.singleShot(500, self._do_delete_character)

    def _do_delete_character(self):
        ch = getattr(self, "_pending_delete_char", "")
        self._pending_delete_char = ""
        if not ch:
            return
        base = os.path.join(cfgmod.EXE_DIR, "pets", ch)
        try:
            shutil.rmtree(base)
        except Exception:
            QMessageBox.warning(
                self, "删除失败",
                f"角色「{ch}」的素材文件正被占用，无法删除。\n"
                "请重启程序后再试，或手动删除文件夹：\n" + base)
            return
        self.assets.refresh()
        self._rebuild_char_combo(select=self.cfg.get("character", "xiaobai"))
        QMessageBox.information(self, "已删除", f"角色「{ch}」已删除。")

    def _upload_gifs(self):
        ch = self.char.currentData()
        if not ch:
            QMessageBox.information(self, "提示", "请先在上方选择一个角色。")
            return
        act = self.act_combo.currentData()
        files, _ = QFileDialog.getOpenFileNames(
            self, f"选择要上传的 GIF（会加入「{ch}」的「{ACTION_CN.get(act, act)}」）",
            "", "动画图片 (*.gif *.GIF)")
        if not files:
            return
        adir = os.path.join(cfgmod.EXE_DIR, "pets", ch, act)
        try:
            os.makedirs(adir, exist_ok=True)
        except Exception:
            QMessageBox.warning(self, "上传失败", "无法创建素材文件夹，请检查权限。")
            return
        n = 0
        for f in files:
            if not f.lower().endswith(".gif"):
                continue
            try:
                shutil.copy2(f, os.path.join(adir, os.path.basename(f)))
                n += 1
            except Exception:
                continue
        if n == 0:
            QMessageBox.information(self, "提示", "没有成功复制任何 GIF（请选 .gif 文件）。")
            return
        self.assets.refresh()
        self._refresh_char_status()
        QMessageBox.information(
            self, "上传完成", f"已为「{ch}」的「{ACTION_CN.get(act, act)}」上传 {n} 张 GIF。\n"
            "同动作可继续上传多张，播放时会随机挑选；传错可直接去 pets 文件夹删除。")

    # ---------------- 自定义叫声 ----------------
    def _refresh_sound_status(self):
        d = os.path.join(SOUNDS_DIR, "bark.wav")
        has = os.path.exists(d)
        self.snd_status.setText(
            "使用自定义叫声（bark.wav）" if has else "使用默认叫声（内置合成音效）")
        self.btn_snd_reset.setEnabled(has)

    def _pick_sound(self):
        f, _ = QFileDialog.getOpenFileName(
            self, "选择叫声音频（wav，建议 1 秒以内）", "", "音频文件 (*.wav)")
        if not f:
            return
        try:
            os.makedirs(SOUNDS_DIR, exist_ok=True)
            dst = os.path.join(SOUNDS_DIR, "bark.wav")
            shutil.copy2(f, dst)
            try:
                import winsound
                winsound.PlaySound(dst, winsound.SND_FILENAME
                                   | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
            except Exception:
                pass
        except Exception:
            QMessageBox.warning(self, "失败", "复制音频失败，请检查文件是否被占用。")
            return
        self._refresh_sound_status()
        QMessageBox.information(self, "设置成功", "叫声已切换为你选的音频，立即生效。")

    def _reset_sound(self):
        try:
            os.remove(os.path.join(SOUNDS_DIR, "bark.wav"))
        except OSError:
            pass
        self._refresh_sound_status()

    def _upload_cursor_gif(self):
        file, _ = QFileDialog.getOpenFileName(
            self, "选择鼠标宠物自定义表情", "", "动画图片 (*.gif *.GIF)")
        if not file:
            return
        try:
            os.makedirs(CURSOR_DIR, exist_ok=True)
            dest = os.path.join(CURSOR_DIR, "custom.gif")
            shutil.copy(file, dest)
            self.cfg["cursor_custom_gif"] = dest
            self._refresh_cursor_gif_status()
            self.preview_character_changed.emit(self.cfg.get("character", "xiaobai"))
            QMessageBox.information(self, "上传成功", "自定义表情已上传，立即生效。")
        except Exception:
            QMessageBox.warning(self, "失败", "复制文件失败，请检查文件是否被占用。")

    def _reset_cursor_gif(self):
        try:
            os.remove(os.path.join(CURSOR_DIR, "custom.gif"))
        except OSError:
            pass
        self.cfg["cursor_custom_gif"] = ""
        self._refresh_cursor_gif_status()
        self.preview_character_changed.emit(self.cfg.get("character", "xiaobai"))
        QMessageBox.information(self, "已恢复", "已恢复为跟随主宠物表情。")

    def _refresh_cursor_gif_status(self):
        custom = self.cfg.get("cursor_custom_gif", "")
        if custom and os.path.exists(custom):
            self.cursor_gif_status.setText("当前使用自定义表情")
        else:
            self.cursor_gif_status.setText("当前跟随主宠物表情")

    def _snapshot(self):
        c = self.cfg
        self._orig_snapshot = {
            "size": c.get("size", 150),
            "character": c.get("character", "xiaobai"),
            "cursor_size": c.get("cursor_size", 75),
        }

    def _set_pet_count_visible(self, on):
        """“小狗数量”整行（标签+数字框）只在桌宠模式开启时显示。
        单独开 Bongo（桌宠模式关）时整行隐藏。"""
        on = bool(on)
        row = getattr(self, "_pet_count_row", None)
        if row is not None:
            row.setVisible(on)
        self.pet_count.setVisible(on)

    def _on_classic_toggled(self, on):
        """桌宠模式开关。关时若 Bongo 没开则自动开 Bongo，避免狗全消失。"""
        if on and self.bongo_mode.isChecked():
            if not self._confirm_coexist("桌宠模式"):
                self.classic_mode.setChecked(False)
                return
        if not on and not self.bongo_mode.isChecked():
            self.bongo_mode.blockSignals(True)
            self.bongo_mode.setChecked(True)
            self.bongo_mode.blockSignals(False)
            self._bongo_group.setVisible(True)
            self.bongo_enabled_changed.emit(True)
        self._set_pet_count_visible(on)
        self.classic_enabled_changed.emit(bool(on))

    def _on_bongo_toggled(self, on):
        """Bongo 模式开关。关时若桌宠模式没开则自动开桌宠，避免狗全消失。"""
        if on and self.classic_mode.isChecked():
            if not self._confirm_coexist("Bongo 模式"):
                self.bongo_mode.setChecked(False)
                return
        if not on and not self.classic_mode.isChecked():
            self.classic_mode.blockSignals(True)
            self.classic_mode.setChecked(True)
            self.classic_mode.blockSignals(False)
            self._set_pet_count_visible(True)
            self.classic_enabled_changed.emit(True)
        self._bongo_group.setVisible(bool(on))
        self.bongo_enabled_changed.emit(bool(on))

    def _confirm_coexist(self, which):
        """两种模式同时运行会加大 CPU/内存；本次运行只提醒一次。"""
        if SettingsDialog._coexist_warned:
            return True
        ret = QMessageBox.question(
            self, "同时开启？",
            f"你正在开启“{which}”。\n"
            "桌宠模式与 Bongo 模式同时运行会加大 CPU 和内存占用。\n\n是否仍要同时开启？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ret == QMessageBox.StandardButton.Yes:
            SettingsDialog._coexist_warned = True
            return True
        return False

    def _on_pet_count_changed(self, n):
        """小狗数量：防抖处理。连续调节（滚轮/按住箭头）时只在停下后确认一次，
        绝不在 valueChanged 里同步弹模态框——否则模态的嵌套事件循环会让
        spinbox 的自动重复/滚轮继续把数值顶到上限(15)。"""
        self._pc_target = int(n)
        if not getattr(self, "_pc_pending", False):
            self._pc_pending = True
            QTimer.singleShot(200, self._commit_pet_count)

    def _commit_pet_count(self):
        self._pc_pending = False
        n = getattr(self, "_pc_target", self.pet_count.value())
        last = getattr(self, "_last_count", 1)
        if n == last:
            return
        if n > 1 and n > last and not SettingsDialog._count_warned:
            ret = QMessageBox.question(
                self, "增加小狗数量？",
                f"同时显示 {n} 只小狗会加大 CPU 和内存占用（每多一只开销都会增加）。\n\n"
                "是否继续？（关闭软件后数量会恢复为 1）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                self.pet_count.blockSignals(True)
                self.pet_count.setValue(last)
                self.pet_count.blockSignals(False)
                return
            SettingsDialog._count_warned = True
        self._last_count = n
        self.pet_count_changed.emit(int(n))

    def _load(self):
        self._snapshot()
        c = self.cfg
        self._set_combo(self.char, c.get("character", "xiaobai"))
        self.size.setValue(int(c.get("size", 150)))
        self._set_combo(self.fps, c.get("fps_mode", "balanced"))
        self._bubble_color = c.get("bubble_color", "#222222")
        self._update_swatch()
        self.bubble_on.setChecked(bool(c.get("bubble_on", True)))
        self.bubble_dur.setValue(int(c.get("bubble_duration", 4200)))
        self.sound_on.setChecked(bool(c.get("sound_on", True)))
        self.hourly.setChecked(bool(c.get("hourly_chime", False)))

        self.mouse_on.setChecked(bool(c.get("mouse_interaction", True)))
        self.click_squash.setChecked(bool(c.get("click_squash_on", True)))
        self.cursor_follow.setChecked(bool(c.get("cursor_follow", False)))
        self.cursor_size.setValue(int(c.get("cursor_size", 75)))
        # 兼容旧配置：只有 pet_mode 时迁移到两个独立开关
        if "classic_enabled" in c or "bongo_enabled" in c:
            classic_on = bool(c.get("classic_enabled", True))
            bongo_on = bool(c.get("bongo_enabled", False))
        else:
            bongo_on = (c.get("pet_mode", "classic") == "bongo")
            classic_on = not bongo_on
        for w, v in ((self.classic_mode, classic_on), (self.bongo_mode, bongo_on)):
            w.blockSignals(True)
            w.setChecked(v)
            w.blockSignals(False)
        self._bongo_group.setVisible(bongo_on)
        self._set_pet_count_visible(classic_on)
        self.pet_count.blockSignals(True)
        self.pet_count.setValue(max(1, min(15, int(c.get("pet_count", 1)))))
        self.pet_count.blockSignals(False)
        self._last_count = self.pet_count.value()
        self.bongo_mirror.setChecked(bool(c.get("bongo_mirror", True)))
        self.bongo_click_through.setChecked(bool(c.get("bongo_click_through", False)))
        self.bongo_size.setValue(int(c.get("bongo_size", 440)))
        self.bongo_size_label.setText(f"{self.bongo_size.value()}px")
        self.bongo_opacity.setValue(int(round(float(c.get("bongo_opacity", 1.0)) * 100)))
        self.bongo_opacity_label.setText(f"{self.bongo_opacity.value()}%")
        self._on_bongo_toggled(self.bongo_mode.isChecked())
        self._set_combo(self.follow, c.get("follow_mode", "sticky"))

        self.sense_all.setChecked(bool(c.get("sense_enabled", True)))
        self.s_idle.setChecked(bool(c.get("sense_idle", True)))
        self.s_window.setChecked(bool(c.get("sense_window", True)))
        self.s_cpu.setChecked(bool(c.get("sense_cpu", True)))
        self.s_clip.setChecked(bool(c.get("sense_clipboard", True)))
        self.s_time.setChecked(bool(c.get("sense_time", True)))
        self.fullscreen_shrink.setChecked(bool(c.get("fullscreen_shrink", True)))

        self.ai_on.setChecked(bool(c.get("ai_enabled", False)))
        self._set_combo(self.provider, c.get("ai_provider", "deepseek"))
        self.ai_base.setText(c.get("ai_base", ""))
        self.ai_key.setText(c.get("ai_key", ""))
        self.ai_model.setText(c.get("ai_model", ""))
        self.ai_ctx.setChecked(bool(c.get("ai_context", True)))
        # 人设缓存：先读已保存的，再同步编辑框到当前形象
        self._persona_edits = dict(c.get("ai_personas") or {})
        self._active_persona_char = None
        self._sync_persona_box()

        self.avoid.setChecked(bool(c.get("avoid_windows", True)))
        self.roam.setChecked(bool(c.get("roam_enabled", True)))
        self._set_combo(self.corner, c.get("lock_corner", ""))
        self.hk_on.setChecked(bool(c.get("hotkey_on", True)))
        seq = c.get("hotkey", "Ctrl+Alt+D")
        self.hotkey.setKeySequence(QKeySequence(seq))
        self.autostart.setChecked(winapi.is_autostart())

        self.paw_on.setChecked(bool(c.get("paw_enabled", True)))
        self.paw_fade.setValue(int(c.get("paw_fade_sec", 6)))
        # 桌面特效（v2.2.0）
        self.fx_on.setChecked(bool(c.get("fx_enabled", True)))
        for cb, key in self._fx_bindings:
            cb.setChecked(bool(c.get(key, cfgmod.DEFAULT_CONFIG.get(key))))
        self._set_combo(self.fx_fps, int(c.get("fx_fps", 30)))

        self._toggle_fx_children(self.fx_on.isChecked())
        self.night_on.setChecked(bool(c.get("night_enabled", True)))
        self.observe_on.setChecked(bool(c.get("observe_enabled", True)))
        self.music_on.setChecked(bool(c.get("music_enabled", True)))
        self.shot_on.setChecked(bool(c.get("shot_enabled", True)))
        self.prank_on.setChecked(bool(c.get("prank_enabled", False)))
        self.prank_mouse.setChecked(bool(c.get("prank_mouse", False)))

        # 外观主题
        self._theme_color = c.get("theme_color", "#ff96bb")
        self._update_theme_swatch()
        self.theme_radius.setValue(int(c.get("theme_radius", 8)))
        self.panel_opacity.setValue(int(c.get("panel_opacity", 1.0) * 100))
        self.pet_opacity.setValue(int(c.get("pet_opacity", 1.0) * 100))

        # _load 会经 toggled 信号重新启用特效子开关，这里按联动状态恢复锁定外观
        self._refresh_link_tab(force=True)

    def _reset_defaults(self):
        ret = QMessageBox.question(
            self, "恢复默认设置",
            "确定要把所有设置恢复为默认值吗？\n（形象/大小/音效/AI/快捷键等全部重置）",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        import copy
        d = copy.deepcopy(cfgmod.DEFAULT_CONFIG)
        for k, v in d.items():
            if k in ("pos", "mood", "energy"):
                continue
            self.cfg[k] = v
        self._load()
        # 触发实时预览回到默认大小/形象
        self.preview_size_changed.emit(int(self.cfg.get("size", 150)))
        self.preview_character_changed.emit(
            self.cfg.get("character", "xiaobai"))
        self.preview_cursor_size_changed.emit(int(self.cfg.get("cursor_size", 75)))
        # 深色模式重置为默认后，刷新本窗口主题 + 按钮 + 广播
        apply_theme(self, self.cfg)
        self._update_dark_btn()
        self.dark_mode_changed.emit(bool(self.cfg.get("dark_mode", False)))

    def _reject_restore(self):
        # 取消时：如果用户在预览期间改了 size/character，把 cfg 恢复到打开对话框前的快照
        for k, v in self._orig_snapshot.items():
            self.cfg[k] = v
        self.reject()

    def _save_and_accept(self):
        c = self.cfg
        c["character"] = self.char.currentData() or "xiaobai"
        c.pop("pet_kind", None)
        c.pop("skin", None)
        c["size"] = int(self.size.value())
        c["fps_mode"] = self.fps.currentData()
        c["bubble_color"] = self._bubble_color
        c["bubble_on"] = self.bubble_on.isChecked()
        c["bubble_duration"] = int(self.bubble_dur.value())
        c["sound_on"] = self.sound_on.isChecked()
        c["hourly_chime"] = self.hourly.isChecked()

        c["mouse_interaction"] = self.mouse_on.isChecked()
        c["click_squash_on"] = self.click_squash.isChecked()
        c["cursor_follow"] = self.cursor_follow.isChecked()
        c["cursor_size"] = int(self.cursor_size.value())
        c["classic_enabled"] = bool(self.classic_mode.isChecked())
        c["bongo_enabled"] = bool(self.bongo_mode.isChecked())
        c["pet_count"] = int(self.pet_count.value())
        # 兼容旧字段：仅当 bongo 独占（未开桌宠模式）时才记 bongo
        c["pet_mode"] = ("bongo" if (self.bongo_mode.isChecked()
                                     and not self.classic_mode.isChecked()) else "classic")
        c["bongo_mirror"] = bool(self.bongo_mirror.isChecked())
        c["bongo_click_through"] = bool(self.bongo_click_through.isChecked())
        c["bongo_size"] = int(self.bongo_size.value())
        c["bongo_opacity"] = round(self.bongo_opacity.value() / 100.0, 2)
        c["follow_mode"] = self.follow.currentData()

        c["sense_enabled"] = self.sense_all.isChecked()
        c["sense_idle"] = self.s_idle.isChecked()
        c["sense_window"] = self.s_window.isChecked()
        c["sense_cpu"] = self.s_cpu.isChecked()
        c["sense_clipboard"] = self.s_clip.isChecked()
        c["sense_time"] = self.s_time.isChecked()
        c["fullscreen_shrink"] = self.fullscreen_shrink.isChecked()

        c["ai_enabled"] = self.ai_on.isChecked()
        c["ai_provider"] = self.provider.currentData()
        c["ai_base"] = self.ai_base.text().strip()
        c["ai_key"] = self.ai_key.text().strip()
        c["ai_model"] = self.ai_model.text().strip()
        c["ai_context"] = self.ai_ctx.isChecked()
        c["ai_personas"] = self._collect_personas()

        c["avoid_windows"] = self.avoid.isChecked()
        c["roam_enabled"] = self.roam.isChecked()
        c["lock_corner"] = self.corner.currentData()
        c["hotkey_on"] = self.hk_on.isChecked()
        c["hotkey"] = self.hotkey.keySequence().toString() or "Ctrl+Alt+D"
        c["autostart"] = self.autostart.isChecked()
        winapi.set_autostart(self.autostart.isChecked())

        c["paw_enabled"] = self.paw_on.isChecked()
        c["paw_fade_sec"] = int(self.paw_fade.value())
        # 桌面特效（v2.2.0）
        c["fx_enabled"] = self.fx_on.isChecked()
        c["fx_fps"] = int(self.fx_fps.currentData() or 30)

        for cb, key in self._fx_bindings:
            c[key] = cb.isChecked()
        c["night_enabled"] = self.night_on.isChecked()
        c["observe_enabled"] = self.observe_on.isChecked()
        c["music_enabled"] = self.music_on.isChecked()
        c["shot_enabled"] = self.shot_on.isChecked()
        c["prank_enabled"] = self.prank_on.isChecked()
        c["prank_mouse"] = self.prank_mouse.isChecked()
        c["theme_color"] = self._theme_color
        c["theme_radius"] = int(self.theme_radius.value())
        c["panel_opacity"] = round(self.panel_opacity.value() / 100.0, 2)
        c["pet_opacity"] = round(self.pet_opacity.value() / 100.0, 2)
        # 立即保存配置到文件，避免用户关闭程序后丢失设置
        cfgmod.save_config(c)
        self.accept()


STATE_TEXT = {
    "idle": "悠闲地发呆", "walk": "正在走", "flee": "吓得躲开了",
    "happy": "很开心", "pet": "被摸头中", "chin": "在挠下巴",
    "picked": "被你抱起来了", "spin": "正在转圈圈", "hop": "蹦了一下",
    "sleep": "睡觉中", "deep": "蜷成一团睡熟了", "go_sleep": "走去趴下",
    "chase": "在追小球！", "hide": "躲起来了，去找它吧~",
    "nervous": "紧张地踱步（电脑有点忙）", "work": "安静陪着你写代码",
    "peek": "扒着窗口偷看", "eat": "吃东西中", "play": "玩耍中",
    "paper": "叼着小纸片", "relax": "趴着休息", "groom": "舔毛整理中",
    "stare": "正盯着你的光标看",
}


class StatusDialog(QDialog):
    feed = pyqtSignal()
    play = pyqtSignal()
    chase = pyqtSignal()
    hidegame = pyqtSignal()

    def __init__(self, status_provider, cfg=None, parent=None):
        super().__init__(parent)
        self.setObjectName("root")
        self.setWindowTitle("迷你状态面板")
        self.cfg = cfg or cfgmod.DEFAULT_CONFIG
        apply_theme(self, self.cfg)
        self.status_provider = status_provider
        lay = QVBoxLayout(self)

        self.state_label = QLabel("—")
        lay.addWidget(self.state_label)
        form = QFormLayout()
        self.mood = QProgressBar()
        self.mood.setRange(0, 100)
        self.mood.setFormat("%v")
        self.energy = QProgressBar()
        self.energy.setRange(0, 100)
        self.energy.setFormat("%v")
        form.addRow("心情：", self.mood)
        form.addRow("体力：", self.energy)
        lay.addLayout(form)

        grid = QGridLayout()
        b_feed = QPushButton("喂食")
        b_play = QPushButton("玩耍")
        b_chase = QPushButton("追小球")
        b_hide = QPushButton("躲猫猫")
        b_feed.clicked.connect(self.feed.emit)
        b_play.clicked.connect(self.play.emit)
        b_chase.clicked.connect(self.chase.emit)
        b_hide.clicked.connect(self.hidegame.emit)
        grid.addWidget(b_feed, 0, 0)
        grid.addWidget(b_play, 0, 1)
        grid.addWidget(b_chase, 1, 0)
        grid.addWidget(b_hide, 1, 1)
        lay.addLayout(grid)

        tip = QLabel("状态自动保存在本机小 JSON 里，下次打开还在。")
        tip.setStyleSheet("color:%s;" % hint_color(self.cfg))
        lay.addWidget(tip)

        self.timer = self.startTimer(1000)
        self.refresh()

    def timerEvent(self, e):
        self.refresh()

    def refresh(self):
        try:
            mood, energy, state = self.status_provider()
            self.mood.setValue(int(mood))
            self.energy.setValue(int(energy))
            self.state_label.setText("现在：" + STATE_TEXT.get(state, state))
        except Exception:
            pass


class ChatDialog(QDialog):
    submitted = pyqtSignal(str)
    message_added = pyqtSignal(str, str)  # (role, text) 给 app 层收集持久化

    def __init__(self, ai_enabled, cfg=None, parent=None):
        super().__init__(parent)
        self.setObjectName("root")
        self.setWindowTitle("和桌宠说句话（滚轮中键点它也能打开）")
        self.cfg = cfg or cfgmod.DEFAULT_CONFIG
        apply_theme(self, self.cfg)
        ch_name = ai_mod.character_display_name(
            (cfg or {}).get("character", "xiaobai"))
        self._char_name = ch_name
        self.resize(380, 300)
        lay = QVBoxLayout(self)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:%s;" % hint_color(self.cfg))
        lay.addWidget(self.hint)
        self.view = QTextEdit()
        self.view.setReadOnly(True)
        lay.addWidget(self.view)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("说点什么…（试试：你好/变小/陪我玩/安静）")
        self.input.returnPressed.connect(self._send)
        self.send_btn = QPushButton("发送")
        self.send_btn.clicked.connect(self._send)
        row.addWidget(self.input)
        row.addWidget(self.send_btn)
        lay.addLayout(row)
        self.set_ai_enabled(ai_enabled)

    def set_character_name(self, name):
        """切换形象后同步消息里的角色名（AI 人设也跟形象走）。"""
        self._char_name = name or "小白"

    def set_ai_enabled(self, on):
        self.hint.setText("AI 已开启，直接对话~" if on else
                          "AI 未开启：现在只能执行本地指令（变小/躲起来/出来/安静/陪我玩等），\n"
                          "想聊天请去 设置→AI填入你自己的API。")

    def _send(self):
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        self.submitted.emit(text)

    def add_msg(self, role, text):
        prefix = {"user": "你：", "pet": self._char_name + "：",
                  "sys": "—  "}.get(role, "")
        dark = bool(self.cfg.get("dark_mode", False))
        if dark:
            m_colors = {"user": "#c9a8d8", "pet": "#ff8ab8", "sys": "#d9b8c6"}
            fallback = "#d8d0d4"
        else:
            m_colors = {"user": PINK_USER_MSG, "pet": PINK_PET_MSG,
                        "sys": PINK_SYS_MSG}
            fallback = "#5e3345"
        color = m_colors.get(role, fallback)
        self.view.append(f'<span style="color:{color}">{prefix}{_escape(text)}</span>')
        self.message_added.emit(role, text)

    def set_busy(self, busy):
        self.input.setEnabled(not busy)
        self.send_btn.setEnabled(not busy)
        self.send_btn.setText("思考中…" if busy else "发送")


def _escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

class DiaryDialog(QDialog):
    """📖 小狗实时每日日记：打开时实时拼接生成当日日记，可翻阅历史。"""

    def __init__(self, cfg=None, parent=None):
        super().__init__(parent)
        self.setObjectName("root")
        self.setWindowTitle("📖 小狗日记")
        self.cfg = cfg or cfgmod.DEFAULT_CONFIG
        apply_theme(self, self.cfg)
        self.resize(430, 520)

        self.db = diary_mod.get_db()
        import datetime as _dt
        self._cur = _dt.date.today().isoformat()
        self._build()
        self._refresh()

    def _paper_colors(self):
        """日记/里程碑文本框配色：浅色=白底深字，深色=深底浅字。"""
        if bool(self.cfg.get("dark_mode", False)):
            return "#241a22", "#5a3d50", "#e8cfda"
        return "#fffafc", "#f2c2d6", "#5e3345"

    def _build(self):
        lay = QVBoxLayout(self)

        title = QLabel("<b style='color:#d6497a; font-size:14pt;'>📖 小狗日记</b>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        nav = QHBoxLayout()
        self.btn_prev = QPushButton("◀ 前一天")
        self.btn_prev.setObjectName("flat")
        self.btn_next = QPushButton("后一天 ▶")
        self.btn_next.setObjectName("flat")
        btn_today = QPushButton("今天")
        btn_today.setObjectName("flat")
        self.date_label = QLabel()
        self.date_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nav.addWidget(self.btn_prev)
        nav.addWidget(self.date_label, 1)
        nav.addWidget(self.btn_next)
        nav.addWidget(btn_today)
        lay.addLayout(nav)

        self.view = QTextEdit()
        self.view.setReadOnly(True)
        _pb, _pd, _pt = self._paper_colors()
        self.view.setStyleSheet(
            f"QTextEdit{{background:{_pb}; border:1px solid {_pd};"
            " border-radius:8px; font:10pt 'Microsoft YaHei';"
            f" color:{_pt}; padding:8px;}}")
        lay.addWidget(self.view, 1)

        tip = QLabel("每天打开时，我都会根据当天发生的事实时写一篇日记~")
        tip.setStyleSheet("color:%s;" % hint_color(self.cfg))
        lay.addWidget(tip)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn)

        self.btn_prev.clicked.connect(lambda: self._shift(-1))
        self.btn_next.clicked.connect(lambda: self._shift(1))
        btn_today.clicked.connect(self._goto_today)

    def _day_label(self):
        import datetime as _dt
        d = _dt.date.fromisoformat(self._cur)
        week = "一二三四五六日"[d.weekday()]
        day_n = self.db.day_index(self._cur)
        return f"{d.year}年{d.month}月{d.day}日 星期{week} · Day {day_n}"

    def _goto_today(self):
        import datetime as _dt
        self._cur = _dt.date.today().isoformat()
        self._refresh()

    def _shift(self, delta):
        import datetime as _dt
        today = _dt.date.today()
        first = self.db.first_day()
        nxt = _dt.date.fromisoformat(self._cur) + _dt.timedelta(days=delta)
        if nxt > today or nxt < first:  # 禁止超出 first_day~today
            return
        self._cur = nxt.isoformat()
        self._refresh()

    def _refresh(self):
        import datetime as _dt
        today = _dt.date.today()
        first = self.db.first_day()
        cur_d = _dt.date.fromisoformat(self._cur)
        self.date_label.setText(self._day_label())
        self.btn_prev.setEnabled(cur_d > first)
        self.btn_next.setEnabled(cur_d < today)
        txt = self.db.diary_text(self._cur)
        if not txt:
            self.view.setPlainText("今天还没有故事，多陪陪我吧~ 🐾")
        else:
            self.view.setPlainText(txt)


class MilestoneDialog(QDialog):
    """🌟 里程碑《第一次》：隐藏彩蛋时间线，集齐数量解锁福利称号。"""

    def __init__(self, cfg=None, parent=None):
        super().__init__(parent)
        self.setObjectName("root")
        self.setWindowTitle("🌟 里程碑 · 第一次")
        self.cfg = cfg or cfgmod.DEFAULT_CONFIG
        apply_theme(self, self.cfg)
        self.resize(840, 580)

        self.db = diary_mod.get_db()
        self._build()
        self._refresh()

    def _paper_colors(self):
        """日记/里程碑文本框配色：浅色=白底深字，深色=深底浅字。"""
        if bool(self.cfg.get("dark_mode", False)):
            return "#241a22", "#5a3d50", "#e8cfda"
        return "#fffafc", "#f2c2d6", "#5e3345"

    def _build(self):
        lay = QVBoxLayout(self)
        lay.setSpacing(8)

        # 主题配色（浅/深色自适应），成就徽章与收藏册共用
        _dark = bool(self.cfg.get("dark_mode", False))
        _c = _color_family(self.cfg.get("theme_color", "#ff96bb"), dark=_dark)
        self._pal = {
            "accent":      _c["dark"] if not _dark else _c["hover"],
            "pill_text":   "#ffffff" if not _dark else _c["darker"],
            "name":        _c["text2"],
            "card_border": _c["border"],
            "card_top":    _c["bg2"],
            "card_bot":    _c["flat"] if not _dark else _c["bg"],
            "ring":        _c["main"],
            "badge_top":   _c["hover"] if not _dark else _c["main"],
            "badge_bot":   _c["main"],
            "disabled_bg": _c["disabled"],
            "dim_text":    _c["disabled_t"],
        }

        title = QLabel(
            "<b style='color:#d6497a; font-size:14pt;'>🌟 关于我们的「第一次」</b>")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(title)

        # 左右两栏：左=收集第一次时间线，右=成就徽章+收藏册
        cols = QHBoxLayout()
        cols.setSpacing(10)
        lay.addLayout(cols, 1)

        # ---- 左栏 ----
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)
        sub1 = QLabel("<b style='color:#c07a93;'>📖 收集「第一次」</b>")
        lv.addWidget(sub1)
        self.progress = QLabel()
        self.progress.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.progress.setStyleSheet("color:%s;" % hint_color(self.cfg))
        lv.addWidget(self.progress)
        self.title_label = QLabel()
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lv.addWidget(self.title_label)
        self.view = QTextEdit()
        self.view.setReadOnly(True)
        _pb, _pd, _pt = self._paper_colors()
        self.view.setStyleSheet(
            f"QTextEdit{{background:{_pb}; border:1px solid {_pd};"
            " border-radius:8px; font:10pt 'Microsoft YaHei';"
            f" color:{_pt}; padding:8px;}}")
        lv.addWidget(self.view, 1)
        tip1 = QLabel("集齐更多「第一次」，解锁专属称号与小惊喜~ 多陪陪小狗吧！")
        tip1.setStyleSheet("color:%s;" % hint_color(self.cfg))
        tip1.setWordWrap(True)
        lv.addWidget(tip1)
        cols.addWidget(left, 1)

        # ---- 右栏 ----
        right = QWidget()
        right.setFixedWidth(320)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)

        # 成就徽章组（圆形奖章卡片横排）
        g_badge = QGroupBox("🏅 成就徽章")
        gbl = QHBoxLayout(g_badge)
        gbl.setContentsMargins(10, 14, 10, 10)
        gbl.setSpacing(8)
        self._badge_circles = []
        self._badge_vals = []
        for emoji, name in (("📅", "连续陆伴"), ("🥚", "彩蛋触发"), ("👕", "服饰收集")):
            tile, circle, val = self._make_badge_tile(emoji, name)
            gbl.addWidget(tile, 1)
            self._badge_circles.append(circle)
            self._badge_vals.append(val)
        rv.addWidget(g_badge)
        
        # 狗狗收藏册组（相册格子）
        g_coll = QGroupBox("🐾 狗狗收藏册")
        gcl = QVBoxLayout(g_coll)
        gcl.setSpacing(4)
        self._coll_scroll = QScrollArea()
        self._coll_scroll.setWidgetResizable(True)
        self._coll_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._coll_scroll.setStyleSheet(
            "QScrollArea{background:transparent; border:none;}"
            "QWidget#collHost{background:transparent;}")
        self._coll_scroll.setMinimumHeight(220)
        self._coll_host = QWidget()
        self._coll_host.setObjectName("collHost")
        self._coll_grid = QGridLayout(self._coll_host)
        self._coll_grid.setContentsMargins(2, 2, 2, 2)
        self._coll_grid.setSpacing(8)
        self._coll_scroll.setWidget(self._coll_host)
        gcl.addWidget(self._coll_scroll, 1)
        self._coll_empty = QLabel(
            "还没捡到小宝贝哦~\n多陪小狗在桌面散步，\n它偶尔会掉下小东西等你捡 🐾")
        self._coll_empty.setStyleSheet("color:%s;" % hint_color(self.cfg))
        self._coll_empty.setWordWrap(True)
        self._coll_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        gcl.addWidget(self._coll_empty)
        coll_tip = QLabel("点击桌面掉落的小东西，小狗会跑过去叼回来~")
        coll_tip.setStyleSheet("color:%s;" % hint_color(self.cfg))
        coll_tip.setWordWrap(True)
        gcl.addWidget(coll_tip)
        self._coll_total = QLabel("")
        self._coll_total.setStyleSheet(
            "color:%s; font:9pt 'Microsoft YaHei';" % self._pal["name"])
        gcl.addWidget(self._coll_total)
        rv.addWidget(g_coll, 1)

        cols.addWidget(right)

        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        lay.addWidget(close_btn)

    # ---------------- 徽章 / 收藏册 卡片工厂 ----------------
    def _make_badge_tile(self, emoji, name):
        """一枚奖章卡片：圆形徽章 + 名称 + 数值（点亮/灰暗在刷新时切换）。"""
        pal = self._pal
        tile = QFrame()
        tile.setObjectName("badgeTile")
        tile.setStyleSheet("#badgeTile{background:transparent; border:none;}")
        v = QVBoxLayout(tile)
        v.setContentsMargins(4, 8, 4, 8)
        v.setSpacing(5)
        circle = QLabel(emoji, tile)
        circle.setFixedSize(46, 46)
        circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(circle, 0, Qt.AlignmentFlag.AlignHCenter)
        nm = QLabel(name, tile)
        nm.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nm.setStyleSheet("background:transparent; color:%s;"
                         " font:9pt 'Microsoft YaHei';" % pal["name"])
        v.addWidget(nm)
        val = QLabel("0", tile)
        val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        val.setStyleSheet("background:transparent; color:%s;"
                          " font:bold 11pt 'Microsoft YaHei';" % pal["accent"])
        v.addWidget(val)
        return tile, circle, val

    def _set_badge_state(self, circle, active):
        """切换奖章的点亮/灰暗外观。"""
        pal = self._pal
        if active:
            circle.setStyleSheet(
                "font-size:18pt; color:%s; border:2px solid %s; border-radius:23px;"
                " background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
                " stop:0 %s, stop:1 %s);"
                % (pal["accent"], pal["ring"], pal["badge_top"], pal["badge_bot"]))
        else:
            circle.setStyleSheet(
                "font-size:18pt; color:%s; border:2px solid %s; border-radius:23px;"
                " background:%s;"
                % (pal["dim_text"], pal["card_border"], pal["disabled_bg"]))

    def _make_coll_card(self, emoji, name, count):
        """相册格子：大图居中，左上角 ×N 计数角标，底部名称。"""
        pal = self._pal
        W, H = 84, 100
        card = QFrame()
        card.setFixedSize(W, H)
        card.setObjectName("collCard")
        card.setStyleSheet(
            "#collCard{border-radius:12px; border:1px solid %s;"
            " background:qlineargradient(x1:0,y1:0,x2:0,y2:1,"
            " stop:0 %s, stop:1 %s);}"
            % (pal["card_border"], pal["card_top"], pal["card_bot"]))
        em = QLabel(emoji, card)
        em.setGeometry(0, 8, W, 54)
        em.setAlignment(Qt.AlignmentFlag.AlignCenter)
        em.setStyleSheet("background:transparent; font-size:28pt;")
        txt = "×%d" % count
        pw = 14 + 8 * len(txt)
        pill = QLabel(txt, card)
        pill.setGeometry(6, 6, pw, 18)
        pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pill.setStyleSheet(
            "background:%s; color:%s; border:none; border-radius:9px;"
            " font:bold 8pt 'Microsoft YaHei';" % (pal["accent"], pal["pill_text"]))
        nm = QLabel(name, card)
        nm.setGeometry(2, 74, W - 4, 20)
        nm.setAlignment(Qt.AlignmentFlag.AlignCenter)
        nm.setStyleSheet("background:transparent; color:%s;"
                         " font:8pt 'Microsoft YaHei';" % pal["name"])
        return card

    def _refresh(self):
        db = self.db
        firsts = db.firsts()
        total = db.total_kinds()
        n = db.firsts_count()
        self.progress.setText(f"已收集 {n} / {total} 个「第一次」")
        cur = db.current_title()
        nxt = db.next_reward()
        if cur:
            self.title_label.setText(
                f"<b style='color:#d6497a;'>当前称号：{cur}</b>"
                f"<span style='color:#8a5a72;'>　已解锁福利 ✨</span>")
        elif nxt:
            self.title_label.setText(
                f"<span style='color:#8a5a72;'>再集齐 {nxt[0]} 个，"
                f"解锁称号「{nxt[1]}」</span>")
        else:
            self.title_label.setText(
                "<span style='color:#8a5a72;'>已集齐全部「第一次」！</span>")

        # 成就徽章
        streak = db.streak_days()
        eggs = db.egg_count()
        outfits = db.outfit_count()
        badges = [(f"{streak} 天", streak > 0),
                  (f"{eggs} 次", eggs > 0),
                  (f"{outfits} 件", outfits > 0)]
        for i, (text, active) in enumerate(badges):
            if i >= len(self._badge_circles):
                break
            self._set_badge_state(self._badge_circles[i], active)
            self._badge_vals[i].setText(text)

        # 时间线
        if not firsts:
            self.view.setPlainText(
                "还没有任何「第一次」哦。\n\n"
                "多摸摸我、喂喂我、陪我玩小游戏……\n"
                "和小狗一起慢慢创造属于我们的回忆吧~ 🐾")
        else:
            lines = []
            for i, f in enumerate(firsts, 1):
                label = diary_mod.EVENT_LABELS.get(f["kind"], f["kind"])
                lines.append(f"第 {i} 个「第一次」\n"
                             f"　{f['date']} · 第一次{label}")
            self.view.setPlainText("\n\n".join(lines))

        # 狗狗收藏册
        self._refresh_collection()

    def _refresh_collection(self):
        # 清空旧卡片
        while self._coll_grid.count():
            it = self._coll_grid.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        items = self.db.collection_list()
        total = self.db.collection_total()
        try:
            self._coll_total.setText("共收集 %d 件 · %d 种小宝贝" % (total, len(items)))
        except Exception:
            pass
        if not items:
            self._coll_empty.show()
            return
        self._coll_empty.hide()
        n_cols = 3
        for i, it in enumerate(items):
            card = self._make_coll_card(
                _ITEM_EMOJI.get(it["item"], "🎁"), it["item"], it["count"])
            self._coll_grid.addWidget(
                card, i // n_cols, i % n_cols,
                Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter)
        # 余量塞到末尾空白行/列，卡片保持左上聚拢、不被拉伸
        for col in range(n_cols):
            self._coll_grid.setColumnStretch(col, 0)
        self._coll_grid.setColumnStretch(n_cols, 1)
        rows = (len(items) + n_cols - 1) // n_cols
        for r in range(rows):
            self._coll_grid.setRowStretch(r, 0)
        self._coll_grid.setRowStretch(rows, 1)
