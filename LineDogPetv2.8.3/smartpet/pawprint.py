# -*- coding: utf-8 -*-
"""
v1.3.0 脚印 / 步数 / 小恶作剧 / 截图缩略气泡。
- PawCanvas：透明置顶自绘画布，脚印渐隐消失，不抢焦点、不写桌面文件。
- StepCounter：像素距离换算步数，跨日清零，持久化到 cfg。
- Prank：低频定时器，偶尔挪鼠标 1~2px 或画几个小爪印（全部可关）。
- ShotBubble：剪贴板出现新截图时弹缩略小气泡。
"""
import math
import time
import random

from PyQt6.QtWidgets import QWidget, QLabel
from PyQt6.QtCore import Qt, QTimer, QPoint, QRectF, QObject
from PyQt6.QtGui import QPainter, QColor, QPixmap, QGuiApplication


class PawCanvas(QWidget):
    """透明置顶画布：自绘半透明脚印，按 fade_sec 渐隐消失。"""

    def __init__(self, fade_sec=6):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._fade = max(2, int(fade_sec))
        self._paws = []  # [x, y, angle, born]
        self._timer = QTimer(self)
        self._timer.setInterval(120)
        self._timer.timeout.connect(self._tick)
        self._dirty = False

    def showEvent(self, ev):
        super().showEvent(ev)
        # 系统级穿透：脚印层不能挡住桌面点击
        from . import winapi
        winapi.set_click_through(int(self.winId()), True)

    def set_fade(self, sec):
        self._fade = max(2, int(sec))

    def add(self, x, y, angle=0.0):
        """在桌面逻辑坐标 (x,y) 留一个脚印，angle 弧度朝向。"""
        self._paws.append([float(x), float(y), float(angle), time.time()])
        if not self._timer.isActive():
            self._ensure_cover()
            self._timer.start()
            self.show()
        self._dirty = True

    def _ensure_cover(self):
        """铺满主屏幕，脚印可画在任意位置。"""
        scr = self.screen() or __import__("PyQt6.QtWidgets",
                                          fromlist=["QApplication"]).QApplication.primaryScreen()
        g = scr.availableGeometry()
        self.setGeometry(g)

    def _tick(self):
        now = time.time()
        self._paws = [p for p in self._paws if now - p[3] < self._fade]
        if not self._paws:
            self._timer.stop()
            self.hide()
            return
        self.update()

    def paintEvent(self, ev):
        if not self._paws:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        now = time.time()
        for x, y, ang, born in self._paws:
            age = now - born
            alpha = int(150 * max(0.0, 1.0 - age / self._fade))
            if alpha <= 0:
                continue
            self._draw_paw(p, x - self.x(), y - self.y(), ang, alpha)
        p.end()

    @staticmethod
    def _draw_paw(p, x, y, ang, alpha):
        """一个脚印：1 脚掌 + 4 肉垫，半透明灰。"""
        col = QColor(90, 70, 60, alpha)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.save()
        p.translate(x, y)
        p.rotate(math.degrees(ang))
        p.drawEllipse(QRectF(-5, -3, 10, 7))  # 脚掌
        for dx, dy in ((-4, -6), (4, -6), (-6, -2), (6, -2)):
            p.drawEllipse(QRectF(dx - 2, dy - 2, 4, 4))
        p.restore()


class StepCounter:
    """像素距离换算步数，跨日清零。"""

    STEP_PX = 18.0  # 每 18 像素算 1 步

    def __init__(self, cfg):
        self.cfg = cfg
        self._accum = 0.0
        self._check_day()

    def _check_day(self):
        today = time.strftime("%Y-%m-%d")
        if self.cfg.get("paw_steps_date") != today:
            self.cfg["paw_steps_today"] = 0
            self.cfg["paw_steps_date"] = today

    def add_distance(self, px):
        if px <= 0:
            return
        self._check_day()
        self._accum += px
        while self._accum >= self.STEP_PX:
            self._accum -= self.STEP_PX
            self.cfg["paw_steps_today"] = int(self.cfg.get("paw_steps_today", 0)) + 1

    def today(self):
        self._check_day()
        return int(self.cfg.get("paw_steps_today", 0))


class Prank(QObject):
    """小恶作剧：偶尔挪鼠标一点点 / 画几个小爪印。全部可关。"""

    def __init__(self, cfg, paw_canvas):
        super().__init__()
        self.cfg = cfg
        self.paw = paw_canvas
        self._timer = QTimer(self)
        self._timer.setInterval(20000)  # 每 20s 掷一次骰子
        self._timer.timeout.connect(self._tick)
        self._started = False

    def set_enabled(self, on):
        if on and not self._started:
            self._started = True
            self._timer.start()
        elif not on and self._started:
            self._started = False
            self._timer.stop()

    def _tick(self):
        if not self.cfg.get("prank_enabled"):
            return
        r = random.random()
        if r < 0.4 and self.cfg.get("prank_mouse"):
            self._nudge_cursor()
        elif r < 0.8:
            self._tiny_paws()

    def _nudge_cursor(self):
        from . import winapi
        x, y = winapi.cursor_pos()
        if x is None:
            return
        winapi.set_cursor_pos(x + random.randint(-2, 2), y + random.randint(-2, 2))

    def _tiny_paws(self):
        if not self.cfg.get("paw_enabled"):
            return
        from PyQt6.QtWidgets import QApplication
        scr = QApplication.primaryScreen().availableGeometry()
        cx = random.randint(scr.left() + 80, scr.right() - 80)
        cy = random.randint(scr.top() + 80, scr.bottom() - 80)
        for i in range(random.randint(2, 4)):
            self.paw.add(cx + i * 18, cy + random.randint(-4, 4),
                         random.uniform(-0.3, 0.3))


class ShotBubble(QWidget):
    """截图缩略小气泡：显示剪贴板里的截图缩略图，几秒后淡出。"""

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)
        self._last_sig = ""

    def showEvent(self, ev):
        super().showEvent(ev)
        from . import winapi
        winapi.set_click_through(int(self.winId()), True)

    def show_shot(self, pixmap, anchor=None):
        if pixmap is None or pixmap.isNull():
            return
        sig = "%dx%d%d" % (pixmap.width(), pixmap.height(), int(time.time() * 2))
        if sig == self._last_sig:
            return
        self._last_sig = sig
        thumb = pixmap.scaled(160, 100, Qt.AspectRatioMode.KeepAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        self._label.setPixmap(thumb)
        self.resize(thumb.width() + 12, thumb.height() + 12)
        self._label.setGeometry(0, 0, self.width(), self.height())
        if anchor:
            self._move_near_pet(anchor)
        else:
            from PyQt6.QtGui import QCursor
            cur = QCursor.pos()
            self.move(cur.x() - self.width() + 20, cur.y() - self.height() - 10)
        self.show()
        self.raise_()
        self._timer.start(3500)

    def _move_near_pet(self, pet):
        """把气泡放到小狗旁边：默认小狗右侧垂直居中，右侧放不下则换到左侧。"""
        ax, ay, aw, ah = pet
        x = ax + aw + 10
        y = ay + (ah - self.height()) // 2
        screen = QGuiApplication.screenAt(QPoint(ax + aw // 2, ay + ah // 2))
        if screen is None:
            screen = QGuiApplication.primaryScreen()
        if screen:
            g = screen.availableGeometry()
            if x + self.width() > g.right() - 8:
                x = ax - self.width() - 10
            y = max(g.top() + 8, min(y, g.bottom() - self.height() - 8))
            if x < g.left() + 8:
                x = g.left() + 8
        self.move(x, y)