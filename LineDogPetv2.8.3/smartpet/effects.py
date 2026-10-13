# -*- coding: utf-8 -*-
"""
v2.2.0 桌面特效层（与动态壁纸视觉联动）。
- EffectLayer：全屏透明置顶画布（鼠标穿透），复用 PawCanvas 的分层窗口模式，
  特效画在壁纸之上、窗口之下观感一致（壁纸本就是桌面最底层）。
- EffectEngine：轻量粒子引擎，由小狗坐标/状态/心情驱动 11 种桌面特效：
    小狗光环(52) 脚步粒子(53) 鼠标吸引粒子(41) 点击爆炸(42) 鼠标光源(44)
    脚下阴影(60) 花瓣互动(6) 落叶互动(7) 萤火虫聚集(8) 蝴蝶追踪(9)
    能量尾巴(54) 音乐律动(22)
- 数据流：引擎每帧直读 PetWidget（进程内零开销）；feed_step/feed_music 是
  标准数据口，未来若要改成由壁纸进程渲染，仅需把这两处调用换成 UDP 广播。
- 性能：渐变球预渲染缓存 / 粒子池上限 / 无活动自动停帧 / 全屏自动暂停 /
  特效帧率可调（15/24/30/60）。
"""
import math
import random
import time
from collections import deque

from PyQt6.QtCore import Qt, QTimer, QObject, QRectF, QRect, QPointF
from PyQt6.QtGui import (QPainter, QColor, QPixmap, QRadialGradient, QBrush,
                         QPen, QFont)
from PyQt6.QtWidgets import QWidget

from . import winapi

# 特效帧率档位 → 定时器间隔(ms)
FPS_INTERVALS = {15: 66, 24: 42, 30: 33, 60: 16}

MAX_PARTICLES = 180          # 瞬态粒子池上限（降低常驻粒子数，减少绘制与内存）
PETAL_WOBBLE = 1.0           # 环境粒子摇摆系数（预留调参）

# 瞬态粒子类型
K_DOT = "dot"                # 渐变光点
K_PETAL = "petal"            # 花瓣片
K_LEAF = "leaf"              # 落叶片
K_NOTE = "note"              # 音符字符


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class EffectLayer(QWidget):
    """全屏透明置顶画布：不抢焦点、鼠标穿透、按需重绘（同 PawCanvas 模式）。"""

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
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._interval = 33
        self._timer = QTimer(self)
        self._timer.setInterval(self._interval)
        self._timer.timeout.connect(self._on_frame)
        self._engine = None
        self._miss = 0  # 连续无活动帧数

    def showEvent(self, ev):
        super().showEvent(ev)
        # 双保险：确保系统级鼠标穿透（WS_EX_TRANSPARENT），点击落到桌面
        winapi.set_click_through(int(self.winId()), True)

    def bind_engine(self, engine):
        self._engine = engine

    def set_interval(self, ms):
        self._interval = _clamp(int(ms), 16, 100)
        if self._timer.isActive():
            self._timer.setInterval(self._interval)

    def ensure_running(self):
        """有特效需要渲染时启动画布（幂等）。"""
        if not self._timer.isActive():
            self._ensure_cover()
            self._timer.start()
            self.show()
        self._miss = 0

    def pause(self):
        if self._timer.isActive():
            self._timer.stop()
        self.hide()

    def resume(self):
        if self._engine is not None and self._engine.any_effect_on():
            self.ensure_running()

    def _ensure_cover(self):
        scr = self.screen()
        if scr is None:
            from PyQt6.QtWidgets import QApplication
            scr = QApplication.primaryScreen()
        if scr is not None:
            self.setGeometry(scr.availableGeometry())

    def _on_frame(self):
        if self._engine is None:
            self.pause()
            return
        dirty = self._engine.frame()
        if dirty is not None:
            self._miss = 0
            # 只重绘本帧脏区域（含上帧位置以清除残影），避免全屏每帧重绘导致闪烁/内存增长
            self.update(dirty.translated(-self.x(), -self.y()))
        else:
            self._miss += 1
            if self._miss >= 4:
                self.pause()

    def paintEvent(self, ev):
        if self._engine is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        try:
            self._engine.paint(p, self.x(), self.y())
        except Exception:
            pass
        p.end()


class CursorLightLayer(QWidget):
    """桌面层鼠标光源：画在壁纸之上、桌面图标与所有应用窗口之下。

    与 EffectLayer（置顶）方向相反——光源是"环境光"，只应照亮桌面背景，
    被应用窗口遮挡时自然不可见（用户要求：鼠标光源只在桌面显示）。
    窗口不置顶、系统级鼠标穿透、不抢焦点；由 winapi.pin_above_wallpaper
    把 Z 序钉在图标层正下方，并周期性重钉防止壁纸宿主后启动时压住本层。
    """

    RADIUS = 190

    def __init__(self):
        super().__init__()
        # 注意：没有 WindowStaysOnTopHint —— 光源必须沉到普通窗口之下
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._glow = self._render_glow("#ffdf9e", self.RADIUS * 2)
        self._pos = None                     # QPointF 全局逻辑坐标
        self._timer = QTimer(self)
        self._timer.setInterval(33)          # 30fps 跟随光标
        self._timer.timeout.connect(self._on_frame)
        self._pin_timer = QTimer(self)
        self._pin_timer.setInterval(4000)    # 周期重钉 Z 序
        self._pin_timer.timeout.connect(self._repin)

    @staticmethod
    def _render_glow(color, size):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        g = QRadialGradient(size / 2, size / 2, size / 2)
        c = QColor(color)
        g.setColorAt(0.0, QColor(c.red(), c.green(), c.blue(), 235))
        g.setColorAt(0.35, QColor(c.red(), c.green(), c.blue(), 130))
        g.setColorAt(1.0, QColor(c.red(), c.green(), c.blue(), 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(0, 0, size, size)
        p.end()
        return pm

    def showEvent(self, ev):
        super().showEvent(ev)
        # 双保险：系统级鼠标穿透（WS_EX_TRANSPARENT），点击落到桌面
        winapi.set_click_through(int(self.winId()), True)
        self._repin()

    def set_enabled(self, on):
        if on:
            self._ensure_cover()
            self._pos = None
            if not self._timer.isActive():
                self._timer.start()
                self._pin_timer.start()
                self.show()
        else:
            self._timer.stop()
            self._pin_timer.stop()
            self.hide()

    def repin(self):
        """外部触发立即重钉（如 PawLiveWall 刚启动、壁纸宿主刚出现在 Z 序里）。"""
        if self.isVisible():
            self._repin()

    def _repin(self):
        if winapi.IS_WINDOWS and self.isVisible():
            winapi.pin_above_wallpaper(int(self.winId()))

    def _ensure_cover(self):
        scr = self.screen()
        if scr is None:
            from PyQt6.QtWidgets import QApplication
            scr = QApplication.primaryScreen()
        if scr is not None:
            # 光源用整块显示器（含任务栏区域，任务栏本就盖在上面）
            self.setGeometry(scr.geometry())

    def _on_frame(self):
        from PyQt6.QtGui import QCursor
        cp = QCursor.pos()
        old = self._pos
        if old is not None and old.x() == cp.x() and old.y() == cp.y():
            return  # 光标没动就不重绘（静态层零开销）
        self._pos = QPointF(cp)
        r = self.RADIUS + 8
        lx, ly = self.x(), self.y()
        rect = QRect(int(cp.x() - lx - r), int(cp.y() - ly - r), r * 2, r * 2)
        if old is not None:
            rect = rect.united(QRect(int(old.x() - lx - r),
                                     int(old.y() - ly - r), r * 2, r * 2))
        self.update(rect)

    def paintEvent(self, ev):
        if self._pos is None:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
        p.setOpacity(0.34)
        d = self.RADIUS * 2
        lx, ly = self.x(), self.y()
        p.drawPixmap(QRectF(self._pos.x() - lx - self.RADIUS,
                            self._pos.y() - ly - self.RADIUS, d, d),
                     self._glow, QRectF(0, 0, d, d))
        p.end()


class EffectEngine(QObject):
    """桌面特效引擎：每帧更新 + 绘制。数据来源：
    - 直读 PetWidget（坐标/状态/尺寸/心情）
    - feed_step()：pet._tick_paw 步进事件
    - feed_music()：检测到正在听歌
    - QCursor.pos()：全局鼠标位置
    - GetAsyncKeyState：全局左键按下沿（点击爆炸）
    """

    def __init__(self, cfg, layer):
        super().__init__()
        self.cfg = cfg
        self.layer = layer
        layer.bind_engine(self)
        self.pet = None

        self._parts = []            # 瞬态粒子 dict
        self._rings = []            # 爆炸圆环 [x, y, born, life, max_r, color]
        self._env = []              # 花瓣/落叶 dict
        self._flies = []            # 萤火虫 dict
        self._bfly = []             # 蝴蝶 dict
        self._env_ready = False
        self._hist = deque(maxlen=26)   # 小狗中心位置历史（能量尾巴）
        self._trail_until = 0.0
        self._trail_energy = 0.0    # 能量尾巴平滑强度(0~1)：速度波动时平滑升降，避免一闪一闪
        self._music_until = 0.0
        self._music_beat_at = 0.0
        self._paused = False        # 全屏等场景暂停
        self._enabled = True
        self._link_active = False   # 联动门控：Lipaper 在线才渲染
        self._dirty = None          # 本帧脏区域（全局坐标 QRect），供画布局部重绘
        self._dirty_prev = None     # 上帧脏区域：并回本帧以清除旧位置残影
        self._mx = -1
        self._my = -1
        self._pmx = -1
        self._pmy = -1
        self._lb_prev = False
        self._ball_cache = {}
        self._t0 = time.time()

    # ---------------- 装配 ----------------
    def attach_pet(self, pet):
        self.pet = pet

    def apply_config(self):
        """读取 cfg 应用帧率档位；关闭的环境特效清空粒子。"""
        fps = int(self.cfg.get("fx_fps", 30))
        self.layer.set_interval(FPS_INTERVALS.get(fps, 33))
        self._env_ready = False
        if not self.cfg.get("fx_petals"):
            self._env = [e for e in self._env if e.get("kind") != "petal"]
        if not self.cfg.get("fx_leaves"):
            self._env = [e for e in self._env if e.get("kind") != "leaf"]
        if not self.cfg.get("fx_fireflies"):
            self._flies = []
        if not self.cfg.get("fx_butterfly"):
            self._bfly = []
        self._sync_running()

    def set_enabled(self, on):
        self._enabled = bool(on)
        self._sync_running()

    def set_fullscreen(self, fs):
        """全屏应用时暂停特效（不打扰游戏/演示画面）。"""
        self._paused = bool(fs)
        self._sync_running()

    def any_effect_on(self):
        c = self.cfg
        if not c.get("fx_enabled", True):
            return False
        if not self._link_ok():
            return False
        return any(c.get(k) for k in (
            "fx_halo", "fx_step", "fx_mouse_trail", "fx_click_burst",
            "fx_shadow", "fx_petals", "fx_leaves",
            "fx_fireflies", "fx_butterfly", "fx_trail", "fx_music"))

    def _link_ok(self):
        """联动门控：必须 PawLiveWall 在线才渲染。"""
        return self._link_active

    def set_link_active(self, on):
        """联动状态变化（由 LinkMonitor 驱动）。"""
        self._link_active = bool(on)
        if not on:
            # 断开：清空所有粒子，视觉立即消失
            self._parts.clear()
            self._rings.clear()
            self._env.clear()
            self._flies.clear()
            self._bfly.clear()
            self._hist.clear()
        self._sync_running()

    def play_link_burst(self):
        """联动建立视觉：金色双环扩散 + 星屑爆发（仪式感）。"""
        now = time.time()
        cx, cy = self._burst_anchor()
        self._rings.append([cx, cy, now, 1.2, 190.0, "#ffd76a"])
        self._rings.append([cx, cy, now + 0.18, 1.0, 130.0, "#ff96bb"])
        for i in range(26):
            ang = (i / 26.0) * 6.283 + random.uniform(-0.1, 0.1)
            spd = random.uniform(130, 280)
            self._parts.append({
                "kind": K_DOT,
                "x": cx, "y": cy,
                "vx": math.cos(ang) * spd,
                "vy": math.sin(ang) * spd,
                "born": now, "life": random.uniform(0.9, 1.6),
                "size": random.uniform(3.0, 6.5),
                "color": random.choice(
                    ["#ffd76a", "#ff96bb", "#ffffff", "#9fd8ff"]),
                "spin": 0.0, "grav": 30.0,
            })
        self._trim_particles()
        self._kick()

    def play_unlink_fade(self):
        """联动断开视觉：蓝色光环 + 粒子轻飘散。"""
        now = time.time()
        cx, cy = self._burst_anchor()
        self._rings.append([cx, cy, now, 1.0, 110.0, "#8fb8ff"])
        for i in range(14):
            ang = random.uniform(0, 6.283)
            self._parts.append({
                "kind": K_DOT,
                "x": cx + math.cos(ang) * 28,
                "y": cy + math.sin(ang) * 28,
                "vx": math.cos(ang) * 45,
                "vy": math.sin(ang) * 45 - 22,
                "born": now, "life": random.uniform(0.8, 1.2),
                "size": random.uniform(2.5, 4.5),
                "color": "#8fb8ff",
                "spin": 0.0, "grav": -10.0,
            })
        self._trim_particles()
        self._kick()

    def _burst_anchor(self):
        dog = self._dog_anchor()
        if dog is not None:
            return dog[0], dog[1]
        if self._mx >= 0:
            return self._mx, self._my
        return 600, 400

    def _sync_running(self):
        if self._enabled and not self._paused and self.any_effect_on():
            self.layer.resume()
        else:
            self.layer.pause()

    # ---------------- 数据口 ----------------
    def feed_step(self, x, y, angle=0.0):
        """小狗一步（约 28px）：脚下冒出小光点/花瓣屑。"""
        if not self._on("fx_step"):
            return
        theme = self.cfg.get("theme_color", "#ff96bb")
        colors = [theme, "#ffffff", "#ffd9e8"]
        n = random.randint(3, 5)
        for i in range(n):
            ang = angle + random.uniform(-1.2, 1.2)
            spd = random.uniform(18, 55)
            self._parts.append({
                "kind": K_DOT if random.random() < 0.72 else K_PETAL,
                "x": x + random.uniform(-6, 6),
                "y": y + random.uniform(-4, 2),
                "vx": math.cos(ang) * spd * 0.4,
                "vy": -abs(math.sin(ang)) * spd - random.uniform(10, 40),
                "born": time.time(),
                "life": random.uniform(0.6, 1.0),
                "size": random.uniform(3.0, 6.5),
                "color": random.choice(colors),
                "spin": random.uniform(-180, 180),
                "grav": 60.0,
            })
        self._trim_particles()
        self._kick()

    def feed_music(self):
        """检测到正在听歌：进入 8 秒律动期，节拍粒子 + 音符上浮。"""
        if not self._on("fx_music"):
            return
        now = time.time()
        self._music_until = now + 8.0
        self._music_beat_at = 0.0
        self._kick()

    # ---------------- 内部工具 ----------------
    def _on(self, key):
        # 特效仅在"与动态壁纸联动"时生效；单独运行桌宠时一律关闭
        return (bool(self.cfg.get("fx_enabled", True))
                and bool(self.cfg.get(key))
                and self._link_ok())

    def _kick(self):
        if self._enabled and not self._paused and self._link_ok():
            self.layer.ensure_running()

    def _trim_particles(self):
        if len(self._parts) > MAX_PARTICLES:
            del self._parts[:len(self._parts) - MAX_PARTICLES]

    def _mark(self, x, y, r):
        """标记脏区域（全局坐标）：以 (x,y) 为中心、半径 r 的方形。

        只重绘脏区域，避免全屏每帧重绘导致的合成闪烁与显存/内存增长。
        """
        if x is None or y is None:
            return
        r = int(max(8, r))
        rect = QRect(int(x) - r, int(y) - r, r * 2, r * 2)
        self._dirty = rect if self._dirty is None else self._dirty.united(rect)

    def _mark_full(self):
        """标记整屏脏区域（花瓣/落叶等全屏环境特效）。"""
        g = getattr(self, "_env_g", None)
        if g is None:
            return
        rect = QRect(g[0], g[1], g[2], g[3])
        self._dirty = rect if self._dirty is None else self._dirty.united(rect)

    def _theme(self):
        return self.cfg.get("theme_color", "#ff96bb")

    def _ball(self, color):
        """预渲染径向渐变球（64x64），按颜色缓存，绘制时缩放。"""
        pm = self._ball_cache.get(color)
        if pm is None:
            pm = QPixmap(64, 64)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            g = QRadialGradient(32, 32, 32)
            c0 = QColor(color)
            g.setColorAt(0.0, QColor(c0.red(), c0.green(), c0.blue(), 255))
            g.setColorAt(0.35, QColor(c0.red(), c0.green(), c0.blue(), 170))
            g.setColorAt(1.0, QColor(c0.red(), c0.green(), c0.blue(), 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(0, 0, 64, 64)
            p.end()
            self._ball_cache[color] = pm
        return pm

    def _dog_anchor(self):
        """返回 (中心x, 中心y, 底部y, 长边, 可见, 被抱起)。"""
        if self.pet is None or not self.pet.isVisible():
            return None
        pet = self.pet
        w, h = pet.width(), pet.height()
        cx = pet.x() + w // 2
        cy = pet.y() + int(h * 0.55)
        by = pet.y() + h
        picked = (pet.state == "picked")
        return cx, cy, by, max(w, h), True, picked

    def _ensure_env(self):
        """初始化环境粒子（花瓣/落叶/萤火虫/蝴蝶）。"""
        if self._env_ready:
            return
        self._env_ready = True
        scr = self.layer.screen()
        if scr is None:
            from PyQt6.QtWidgets import QApplication
            scr = QApplication.primaryScreen()
        if scr is None:
            return
        g = scr.availableGeometry()
        self._env_g = (g.left(), g.top(), g.width(), g.height())
        now = time.time()
        want_petals = 34 if self._on("fx_petals") else 0
        want_leaves = 24 if self._on("fx_leaves") else 0
        petals = [e for e in self._env if e.get("kind") == "petal"]
        leaves = [e for e in self._env if e.get("kind") == "leaf"]
        while len(petals) < want_petals:
            petals.append(self._new_env("petal", g, now, random_y=True))
        while len(leaves) < want_leaves:
            leaves.append(self._new_env("leaf", g, now, random_y=True))
        self._env = petals + leaves
        if self._on("fx_fireflies") and not self._flies:
            for i in range(16):
                self._flies.append({
                    "x": random.uniform(g.left(), g.right()),
                    "y": random.uniform(g.top() + 40, g.bottom() - 60),
                    "vx": random.uniform(-14, 14),
                    "vy": random.uniform(-10, 10),
                    "phase": random.uniform(0, 6.28),
                })
        if self._on("fx_butterfly") and not self._bfly:
            colors = ["#f7a8c4", "#ffd479", "#9fd8ff"]
            for i in range(2):
                self._bfly.append({
                    "x": random.uniform(g.left() + 60, g.right() - 60),
                    "y": random.uniform(g.top() + 60, g.bottom() - 120),
                    "color": colors[i % len(colors)],
                    "phase": random.uniform(0, 6.28),
                    "orbit": 0.0,
                })

    @staticmethod
    def _new_env(kind, g, now, random_y=False):
        lx, ly, lw, lh = g.left(), g.top(), g.width(), g.height()
        return {
            "kind": kind,
            "x": random.uniform(lx, lx + lw),
            "y": (random.uniform(ly, ly + lh) if random_y
                  else ly - random.uniform(10, 120)),
            "vx": random.uniform(-8, 8),
            "vy": (random.uniform(10, 20) if kind == "petal"
                   else random.uniform(16, 30)),
            "size": (random.uniform(4.5, 8.0) if kind == "petal"
                     else random.uniform(5.0, 9.0)),
            "rot": random.uniform(0, 360),
            "spin": random.uniform(-90, 90) if kind == "leaf"
                    else random.uniform(-40, 40),
            "phase": random.uniform(0, 6.28),
            "color": ("#f8b8d0" if kind == "petal"
                      else random.choice(["#d8a45e", "#c98f4a", "#b7793a"])),
        }

    # ---------------- 主帧 ----------------
    def frame(self):
        """每帧更新；返回本帧脏区域 QRect（全局坐标），无内容返回 None。"""
        if self._paused or not self._enabled or not self._link_ok():
            return None
        now = time.time()
        dt = _clamp(now - getattr(self, "_last_t", now - 0.033), 0.005, 0.1)
        self._last_t = now
        t = now - self._t0

        # 脏区域：从上帧区域起步（保证旧位置被重绘清除，避免残影），本帧各特效再并集
        self._dirty = QRect(self._dirty_prev) if self._dirty_prev is not None else None

        # 全局鼠标
        from PyQt6.QtGui import QCursor
        cp = QCursor.pos()
        self._pmx, self._pmy = self._mx, self._my
        self._mx, self._my = cp.x(), cp.y()

        dog = self._dog_anchor()

        # 点击爆炸：全局左键按下沿
        if self._on("fx_click_burst"):
            pressed = winapi.left_mouse_pressed()
            if pressed and not self._lb_prev:
                self._spawn_burst(self._mx, self._my, self._theme())
                self._mark(self._mx, self._my, 90)
            self._lb_prev = pressed

        # 小狗状态历史（能量尾巴）
        if dog is not None:
            cx, cy = dog[0], dog[1]
            if self._hist:
                lx, ly = self._hist[-1]
                speed = math.hypot(cx - lx, cy - ly) / dt
                # 平滑升降：过阈值渐显、降速渐隐，避免速度波动导致尾巴一闪一闪
                if speed > 200:
                    self._trail_energy = min(1.0, self._trail_energy + dt * 5.0)
                else:
                    self._trail_energy = max(0.0, self._trail_energy - dt * 2.2)
            self._hist.append((cx, cy))
            if self._on("fx_trail") and self._trail_energy > 0.02:
                for hx, hy in self._hist:
                    self._mark(hx, hy, dog[3] * 0.32)
        else:
            self._trail_energy = max(0.0, self._trail_energy - dt * 3.0)

        # 瞬态粒子
        if self._parts:
            for pt in self._parts:
                pt["vy"] += pt.get("grav", 0.0) * dt
                pt["x"] += pt["vx"] * dt
                pt["y"] += pt["vy"] * dt
                pt["rot"] = pt.get("rot", 0.0) + pt.get("spin", 0.0) * dt
                self._mark(pt["x"], pt["y"], pt["size"] * 2 + 14)
            self._parts = [p_ for p_ in self._parts
                           if now - p_["born"] < p_["life"]]

        # 圆环
        if self._rings:
            for r in self._rings:
                self._mark(r[0], r[1], r[4] + 8)
            self._rings = [r for r in self._rings
                           if now - r[2] < r[3]]

        # 鼠标吸引粒子
        if self._on("fx_mouse_trail") and self._pmx >= 0:
            md = math.hypot(self._mx - self._pmx, self._my - self._pmy)
            if md > 2:
                self._mark(self._mx, self._my, 70)
                # 移动越快撒得越多，微粒被轻微吸向光标
                n = 1 if md < 24 else 2
                if random.random() < 0.8:
                    for _ in range(n):
                        ang = random.uniform(0, 6.28)
                        r0 = random.uniform(6, 26)
                        self._parts.append({
                            "kind": K_DOT,
                            "x": self._mx + math.cos(ang) * r0,
                            "y": self._my + math.sin(ang) * r0,
                            "vx": (self._mx - (self._mx + math.cos(ang) * r0)) * 1.6,
                            "vy": (self._my - (self._my + math.sin(ang) * r0)) * 1.6,
                            "born": now,
                            "life": random.uniform(0.7, 1.3),
                            "size": random.uniform(2.0, 4.5),
                            "color": self._theme() if random.random() < 0.6
                                     else "#ffffff",
                            "spin": 0.0,
                            "grav": -6.0,
                        })
                    self._trim_particles()

        # 音乐律动
        if now < self._music_until:
            if now >= self._music_beat_at:
                self._music_beat_at = now + 0.45
                gx, gy, gw, gh = getattr(self, "_env_g",
                                         (0, 0, 1200, 800))
                beat_x = random.uniform(gx + 60, gx + gw - 60)
                beat_y = random.uniform(gy + 60, gy + gh - 120)
                self._mark(beat_x, beat_y, 130)
                theme = self._theme()
                for _ in range(random.randint(2, 4)):
                    ang = random.uniform(0, 6.28)
                    self._parts.append({
                        "kind": K_DOT,
                        "x": beat_x, "y": beat_y,
                        "vx": math.cos(ang) * random.uniform(30, 80),
                        "vy": math.sin(ang) * random.uniform(30, 80) - 30,
                        "born": now, "life": random.uniform(0.8, 1.4),
                        "size": random.uniform(3.0, 7.0),
                        "color": random.choice(
                            [theme, "#ffffff", "#ffd479", "#9fd8ff"]),
                        "spin": 0.0, "grav": -18.0,
                    })
                if random.random() < 0.5:
                    self._parts.append({
                        "kind": K_NOTE,
                        "x": beat_x + random.uniform(-40, 40),
                        "y": beat_y + 10,
                        "vx": random.uniform(-12, 12),
                        "vy": -random.uniform(36, 60),
                        "born": now, "life": random.uniform(1.6, 2.4),
                        "size": random.uniform(13, 19),
                        "color": theme if random.random() < 0.5 else "#ffffff",
                        "spin": 0.0, "grav": 0.0,
                    })
                self._trim_particles()

        # 环境粒子（花瓣/落叶）
        if self._on("fx_petals") or self._on("fx_leaves"):
            self._ensure_env()
            self._mark_full()
            dgx = dgy = dvx = dvy = 0.0
            if dog is not None and len(self._hist) >= 2:
                dgx, dgy = self._hist[-1]
                lx, ly = self._hist[-2]
                dvx, dvy = (dgx - lx) / dt, (dgy - ly) / dt
            for e in self._env:
                wob = math.sin(now * (1.6 if e["kind"] == "petal" else 1.1)
                               + e["phase"])
                e["vx"] += wob * (26 if e["kind"] == "petal" else 12) * dt
                e["x"] += e["vx"] * dt
                e["y"] += e["vy"] * dt
                e["rot"] += e["spin"] * dt
                e["vx"] *= 0.985
                # 小狗扰动：走近的花瓣/落叶被带起
                if dog is not None:
                    dd = math.hypot(e["x"] - dgx, e["y"] - (dgy + dog[2] - dog[1]))
                    if dd < 130:
                        sp = math.hypot(dvx, dvy)
                        if sp > 30:
                            e["vx"] += _clamp(dvx, -400, 400) * 0.5 * dt
                            e["vy"] = min(e["vy"], 0) - random.uniform(28, 80) * dt * 12
                            e["spin"] += random.uniform(-160, 160) * dt
                lx0, ly0, lw0, lh0 = getattr(self, "_env_g", (0, 0, 1200, 800))
                if e["y"] > ly0 + lh0 + 40 or e["x"] < lx0 - 60 or e["x"] > lx0 + lw0 + 60:
                    e.update(self._new_env(e["kind"], QRect(lx0, ly0, lw0, lh0), now))

        # 萤火虫
        if self._on("fx_fireflies"):
            self._ensure_env()
            for f in self._flies:
                f["vx"] += random.uniform(-30, 30) * dt
                f["vy"] += random.uniform(-24, 24) * dt
                if dog is not None:
                    tx, ty = dog[0], dog[1] + 20
                    dd = math.hypot(tx - f["x"], ty - f["y"])
                    if dd > 90:
                        f["vx"] += (tx - f["x"]) / dd * 46 * dt
                        f["vy"] += (ty - f["y"]) / dd * 46 * dt
                    else:
                        # 聚齐后绕小狗游动
                        f["vx"] += math.cos(now * 1.4 + f["phase"]) * 40 * dt
                        f["vy"] += math.sin(now * 1.8 + f["phase"]) * 40 * dt
                spd = math.hypot(f["vx"], f["vy"])
                if spd > 70:
                    f["vx"] *= 70 / spd
                    f["vy"] *= 70 / spd
                f["x"] += f["vx"] * dt
                f["y"] += f["vy"] * dt
                self._mark(f["x"], f["y"], 16)

        # 蝴蝶
        if self._on("fx_butterfly"):
            self._ensure_env()
            for b in self._bfly:
                if dog is not None:
                    tx, ty = dog[0], dog[1] - dog[3] * 0.18
                    dd = math.hypot(tx - b["x"], ty - b["y"])
                    if dd > 64:
                        sp = 66
                        b["x"] += (tx - b["x"]) / dd * sp * dt
                        b["y"] += ((ty - b["y"]) / dd * sp
                                   + math.sin(now * 6 + b["phase"]) * 46) * dt
                        b["orbit"] = 0.0
                    else:
                        b["orbit"] += dt * 2.2
                        ox = dog[0] + math.cos(b["orbit"]) * 74
                        oy = dog[1] - dog[3] * 0.18 + math.sin(b["orbit"]) * 34
                        b["x"] += (ox - b["x"]) * _clamp(dt * 3.4, 0, 1)
                        b["y"] += (oy - b["y"]) * _clamp(dt * 3.4, 0, 1)
                else:
                    b["x"] += math.cos(now * 0.7 + b["phase"]) * 22 * dt
                    b["y"] += math.sin(now * 1.1 + b["phase"]) * 26 * dt
                self._mark(b["x"], b["y"], 30)

        # 持续类特效（光环/阴影）：标记小狗周围区域
        if dog is not None and (self._on("fx_halo") or self._on("fx_shadow")):
            self._mark(dog[0], dog[1], dog[3] * 1.1)
            self._mark(dog[0], dog[2], dog[3] * 0.6)

        self._dirty_prev = self._dirty
        return self._dirty

    def _spawn_burst(self, x, y, color):
        """点击爆炸：一圈光点 + 扩散圆环。"""
        now = time.time()
        for i in range(14):
            ang = (i / 14.0) * 6.283 + random.uniform(-0.15, 0.15)
            spd = random.uniform(90, 210)
            self._parts.append({
                "kind": K_DOT,
                "x": x, "y": y,
                "vx": math.cos(ang) * spd,
                "vy": math.sin(ang) * spd,
                "born": now, "life": random.uniform(0.45, 0.8),
                "size": random.uniform(2.5, 5.5),
                "color": color if i % 3 else "#ffffff",
                "spin": 0.0, "grav": 120.0,
            })
        self._rings.append([x, y, now, 0.5, 66.0, color])
        self._trim_particles()
        self._kick()

    # ---------------- 绘制 ----------------
    def paint(self, p, ox, oy):
        # 联动门控：单独运行桌宠时绝不绘制任何桌面特效
        if not self._link_ok():
            return
        now = time.time()
        t = now - self._t0

        # 1) 小狗脚下阴影
        #    （鼠标光源已拆到桌面层 CursorLightLayer：光源是环境光，
        #    只应照亮桌面背景，被应用窗口遮挡时不可见）
        dog = self._dog_anchor()
        if dog is not None and self._on("fx_shadow"):
            cx, cy, by, size, _, picked = dog
            w = size * (0.30 if picked else 0.42)
            h = size * (0.055 if picked else 0.10)
            alpha = 0.32 if picked else 0.5
            p.save()
            p.setOpacity(alpha)
            p.drawPixmap(QRectF(cx - ox - w, by - oy - h * 0.5,
                                w * 2, h * 2),
                         self._ball("#20242c"),
                         QRectF(0, 0, 64, 64))
            p.restore()

        # 3) 小狗光环（心情缩放 + 呼吸 + 听歌脉动）
        if dog is not None and self._on("fx_halo"):
            cx, cy, by, size, _, picked = dog
            mood = float(self.cfg.get("mood", 80))
            breathe = 1.0 + 0.05 * math.sin(t * 2.1)
            if now < self._music_until:
                breathe *= 1.0 + 0.10 * math.sin(t * 6.283 * 1.25)
            r = size * (0.62 + mood / 100.0 * 0.38) * breathe
            p.save()
            p.setOpacity(0.16 if not picked else 0.10)
            p.drawPixmap(QRectF(cx - ox - r, cy - oy - r, r * 2, r * 2),
                         self._ball(self._theme()),
                         QRectF(0, 0, 64, 64))
            p.restore()

        # 4) 能量尾巴（高速移动拖曳光带，强度平滑避免一闪一闪）
        if (dog is not None and self._on("fx_trail")
                and self._trail_energy > 0.02):
            pts = list(self._hist)
            if len(pts) >= 3:
                p.save()
                theme = QColor(self._theme())
                n = len(pts)
                strength = self._trail_energy
                for i in range(1, n):
                    k = i / float(n)
                    c = QColor(theme)
                    c.setAlpha(int(120 * k * strength))
                    wdt = max(1.5, dog[3] * 0.12 * k * strength)
                    pen = QPen(c, wdt, Qt.PenStyle.SolidLine,
                               Qt.PenCapStyle.RoundCap)
                    p.setPen(pen)
                    p.drawLine(QPointF(pts[i - 1][0] - ox, pts[i - 1][1] - oy),
                               QPointF(pts[i][0] - ox, pts[i][1] - oy))
                p.restore()

        # 5) 环境粒子（花瓣/落叶）
        for e in self._env:
            p.save()
            p.translate(e["x"] - ox, e["y"] - oy)
            p.rotate(e["rot"])
            p.setOpacity(0.75)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(e["color"]))
            s = e["size"]
            if e["kind"] == "petal":
                p.drawEllipse(QRectF(-s * 0.5, -s * 0.32, s, s * 0.64))
            else:
                p.drawEllipse(QRectF(-s * 0.55, -s * 0.24, s * 1.1, s * 0.48))
            p.restore()

        # 6) 萤火虫（呼吸闪烁）
        if self._flies:
            for i, f in enumerate(self._flies):
                glow = 0.35 + 0.30 * math.sin(now * 3.0 + f["phase"])
                p.save()
                p.setOpacity(_clamp(glow, 0.08, 0.75))
                r = 5.0
                p.drawPixmap(QRectF(f["x"] - ox - r, f["y"] - oy - r,
                                    r * 2, r * 2),
                             self._ball("#d9f07e"),
                             QRectF(0, 0, 64, 64))
                p.restore()

        # 7) 蝴蝶（扑翼）
        for b in self._bfly:
            flap = abs(math.sin(now * 11 + b["phase"]))
            p.save()
            p.translate(b["x"] - ox, b["y"] - oy)
            p.setOpacity(0.9)
            p.setPen(Qt.PenStyle.NoPen)
            col = QColor(b["color"])
            p.setBrush(col)
            wing = 7.5
            p.drawEllipse(QRectF(-wing * (0.35 + 0.65 * flap), -6,
                                 wing * (0.35 + 0.65 * flap), 6.5))
            p.drawEllipse(QRectF(0, -6,
                                 wing * (0.35 + 0.65 * flap), 6.5))
            p.setBrush(QColor(70, 60, 55, 220))
            p.drawEllipse(QRectF(-1.1, -4.5, 2.2, 9))
            p.restore()

        # 8) 爆炸圆环
        for x, y, born, life, max_r, color in self._rings:
            k = (now - born) / life
            p.save()
            p.setOpacity(_clamp(0.55 * (1.0 - k), 0, 1))
            pen = QPen(QColor(color), max(1.0, 2.5 * (1 - k)))
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            r = max_r * (0.25 + 0.75 * k)
            p.drawEllipse(QRectF(x - ox - r, y - oy - r, r * 2, r * 2))
            p.restore()

        # 9) 瞬态粒子
        for pt in self._parts:
            age = (now - pt["born"]) / pt["life"]
            if age >= 1:
                continue
            p.save()
            p.setOpacity(_clamp(1.0 - age, 0, 1) * 0.9)
            if pt["kind"] == K_DOT:
                r = pt["size"] * (1.0 - 0.35 * age)
                p.drawPixmap(QRectF(pt["x"] - ox - r, pt["y"] - oy - r,
                                    r * 2, r * 2),
                             self._ball(pt["color"]),
                             QRectF(0, 0, 64, 64))
            elif pt["kind"] == K_PETAL:
                p.translate(pt["x"] - ox, pt["y"] - oy)
                p.rotate(pt.get("rot", 0))
                s = pt["size"]
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(pt["color"]))
                p.drawEllipse(QRectF(-s * 0.5, -s * 0.3, s, s * 0.6))
            elif pt["kind"] == K_NOTE:
                p.setPen(QColor(pt["color"]))
                f = QFont()
                f.setPixelSize(int(pt["size"] * 2))
                f.setBold(True)
                p.setFont(f)
                p.drawText(QRectF(pt["x"] - ox - 20, pt["y"] - oy - 20,
                                  40, 40),
                           Qt.AlignmentFlag.AlignCenter, "♪")
            p.restore()