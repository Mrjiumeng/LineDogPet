# -*- coding: utf-8 -*-
"""Bongo 模式：可拖动的透明面板，小狗趴在键盘上、跟着按键放爪。

与 classic 桌宠模式**互不影响**：由 config 的 pet_mode 切换，同一时刻只显示一个。

整帧切换播放：
- 素材 pets/bongo/：idle.png（待机）+ 若干敲键帧 hit_*.png（也可换成你自己的图）。
- "每张图覆盖哪些按键"由 smartpet/framemap.py 决定：默认按左右手/排布自动分类，
  也可用 bongo_frames_editor.py 可视化自定义，存进 bongo_frames.json 即时生效。
- 运行期轮询按下的键名（winapi.pressed_key_names），查映射切到对应帧，松开回 idle。
"""
import os
import time

from PyQt6.QtCore import Qt, QTimer, QPoint
from PyQt6.QtGui import QPixmap, QPainter, QTransform
from PyQt6.QtWidgets import QWidget, QApplication

from . import winapi
from . import framemap
from . import config as cfgmod


def _asset_path(name):
    return os.path.join(cfgmod.APP_DIR, "pets", "bongo", name)


class BongoPet(QWidget):
    """Bongo 模式面板：透明置顶、可拖动、按"图片->按键"映射切换播放帧素材。"""

    BASE_W, BASE_H = 1276, 1037       # 新素材统一画布尺寸
    DISPLAY_W = 440
    DISPLAY_H = round(DISPLAY_W * BASE_H / BASE_W)   # ≈358，与素材同比例，杜绝非等比拉伸
    MIRROR_DEFAULT = True   # 新素材键盘与真实键盘"面对面"(左右反)，需水平镜像；开关保留供切换

    POLL_MS = 33          # 轮询间隔（≈30fps）
    HOLD = 0.09           # 短按至少显示多久，避免一闪而过

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._dw = int((cfg or {}).get("bongo_size", self.DISPLAY_W))
        self._dh = round(self._dw * self.BASE_H / self.BASE_W)
        self.setFixedSize(self._dw, self._dh)
        self.setWindowOpacity(float((cfg or {}).get("bongo_opacity", 1.0)))
        self._click_through = bool((cfg or {}).get("bongo_click_through", False))
        self._apply_click_through()

        self._mirror = bool((cfg or {}).get("bongo_mirror", self.MIRROR_DEFAULT))
        self._idle = self._load(framemap.idle_frame())
        self._frame = self._idle
        self._cache = {}
        self._until = 0.0
        self._hit_name = None          # 当前敲击帧文件名；None=待机


        self._placed = False
        self._dragging = False
        self._drag_off = QPoint()

        self._poll = QTimer(self)
        self._poll.setInterval(self.POLL_MS)
        self._poll.timeout.connect(self._on_poll)

    # ---------------- 素材 ----------------
    def _load(self, name):
        pm = QPixmap(_asset_path(name))
        if pm.isNull():
            return QPixmap()
        pm = pm.scaled(self._dw, self._dh,
                       Qt.AspectRatioMode.IgnoreAspectRatio,
                       Qt.TransformationMode.SmoothTransformation)
        if self._mirror:
            # 水平翻转：素材键盘"面对面"朝向我们，翻过来才和真实键盘同向，
            # 于是"按左边键 -> 爪子出现在屏幕左边"自然成立。
            pm = pm.transformed(QTransform().scale(-1, 1))
        return pm

    def _frame_pm(self, name):
        if name not in self._cache:
            pm = self._load(name)
            if pm.isNull():
                pm = self._idle
            self._cache[name] = pm
        return self._cache[name]


    # ---------------- 生命周期 ----------------
    def set_enabled(self, on):
        """开启/关闭 Bongo 面板（切换模式时调用）。"""
        if on:
            if not self._placed:
                self._place_default()
            self._poll.start()
            self.show()
            self.raise_()
        else:
            self._poll.stop()
            self._dragging = False
            self.hide()

    def is_on(self):
        return self._poll.isActive()

    def set_mirror(self, on):
        """切换是否水平镜像素材（设置面板"镜像键盘"）。变化时重载全部帧。"""
        on = bool(on)
        if on == self._mirror:
            return
        self._mirror = on
        self._cache.clear()
        self._idle = self._load(framemap.idle_frame())
        self._frame = self._idle
        self._hit_name = None
        self.update()

    def set_size(self, w):
        """设置显示宽度（高度按素材比例自动算），重载全部帧。"""
        w = int(w)
        if w == self._dw:
            return
        self._dw = w
        self._dh = round(w * self.BASE_H / self.BASE_W)
        self.setFixedSize(self._dw, self._dh)
        self._cache.clear()
        self._idle = self._load(framemap.idle_frame())
        self._frame = self._idle
        self._hit_name = None
        self.update()

    def set_opacity(self, o):
        self.setWindowOpacity(float(o))

    def set_click_through(self, on):
        on = bool(on)
        if on == self._click_through:
            return
        self._click_through = on
        self._apply_click_through()
        if self._poll.isActive():
            self.show()

    def _apply_click_through(self):
        if self._click_through:
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowTransparentForInput)
        else:
            self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowTransparentForInput)

    def _place_default(self):
        """首次显示：默认落在屏幕底部中央（之后用户拖动的位置会被保留）。"""
        scr = self.screen() or QApplication.primaryScreen()
        if scr is None:
            return
        g = scr.availableGeometry()
        x = g.left() + (g.width() - self.width()) // 2
        y = g.bottom() - self.height() + 1
        self.move(x, y)
        self._placed = True

    # ---------------- 帧循环 ----------------
    def _on_poll(self):
        now = time.time()
        framemap.load()                       # 拾取编辑页对 bongo_frames.json 的改动
        names = winapi.pressed_key_names()
        fn = framemap.held(names) if names else None
        if fn:
            self._hit_name = fn               # 按住 -> 持续敲击帧（长按连敲）
            self._until = now + self.HOLD     # 松开后至少再显示 HOLD，避免一闪而过
        elif now >= self._until:
            self._hit_name = None             # 超时未按 -> 回待机
        want = self._frame_pm(self._hit_name) if self._hit_name else self._idle
        if want is not self._frame and not want.isNull():
            self._frame = want
            self.update()

    # ---------------- 绘制 ----------------
    def paintEvent(self, ev):
        if self._frame.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawPixmap(0, 0, self._frame)

        p.end()

    # ---------------- 拖动 ----------------
    def mousePressEvent(self, ev):
        if ev.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_off = (ev.globalPosition().toPoint()
                              - self.frameGeometry().topLeft())
            ev.accept()

    def mouseMoveEvent(self, ev):
        if self._dragging:
            self.move(ev.globalPosition().toPoint() - self._drag_off)
            ev.accept()

    def mouseReleaseEvent(self, ev):
        if self._dragging:
            self._dragging = False
            self._placed = True
            ev.accept()
