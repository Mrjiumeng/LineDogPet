# -*- coding: utf-8 -*-
"""
桌宠主体：
- 无边框 + 置顶 + 不抢焦点 + 透明背景的悬浮窗
- QMovie 按需加载/即用即弃，配合速度调节实现疲劳降帧
- 状态机 + 50ms 移动节拍：粘人/胆小跟随、追球、踱步、躲猫猫、睡觉…
- 鼠标交互：轻点摸头、长按抱起、右键挠下巴、双击呼唤、画圈转圈、甩动追/躲、悬停盯光标
"""
import os
import math
import time
import random
from collections import deque

from PyQt6.QtWidgets import QWidget, QLabel, QApplication
from PyQt6.QtCore import Qt, QTimer, QPoint, QSize, QRect, pyqtSignal
from PyQt6.QtGui import (
    QMovie, QCursor, QFontMetrics, QPainter, QPainterPath, QPen, QColor,
)
from PyQt6.QtCore import QRectF

from . import config as cfgmod
from . import winapi

# ---- 状态 ----
IDLE = "idle"
WALK = "walk"            # 赶往某个明确目标点（避让/躲猫猫/去睡觉）
FLEE = "flee"            # 胆小躲闪 / 猫被甩动
HAPPY = "happy"
PET = "pet"              # 摸头
CHIN = "chin"            # 挠下巴
PICKED = "picked"
SPIN = "spin"
HOP = "hop"
SLEEP = "sleep"
DEEP = "deep"
GO_SLEEP = "go_sleep"
CHASE = "chase"          # 追小球
HIDE = "hide"            # 躲猫猫
NERVOUS = "nervous"      # 高负载踱步
WORK = "work"            # 陪写代码
PEEK = "peek"            # 浏览器探头
EAT = "eat"
PLAY = "play"
PAPER = "paper"
RELAX = "relax"
GROOM = "groom"          # 舔毛/打盹小动作
STARE = "stare"

# 到时自动回到基础状态的临时反应
TEMP_STATES = {
    FLEE: 900, HAPPY: 1600, PET: 1900, CHIN: 1900, SPIN: 2100, HOP: 1300,
    EAT: 2600, PLAY: 3500, PAPER: 3200, STARE: 4200, RELAX: 3200,
    GROOM: 3000, PEEK: 4200,
}

STATE_GIF = {
    IDLE: "idle", WALK: "walk", FLEE: "scared", HAPPY: "happy", PET: "pet",
    CHIN: "chin", PICKED: "pickup", SPIN: "spin", HOP: "hop",
    SLEEP: "sleep", DEEP: "deep", GO_SLEEP: "walk", CHASE: "chase",
    HIDE: "hide", NERVOUS: "nervous", WORK: "work", PEEK: "peek",
    EAT: "eat", PLAY: "play", PAPER: "paper", RELAX: "relax",
    GROOM: "groom", STARE: "chin",
}

# 跟随逻辑可以在这些“松弛”状态下触发
RELAXED_STATES = (IDLE, STARE, GROOM)

# 摸头延迟 = 系统双击时间：松手后等满双击窗口再摸头，
# 第二次按下会取消它，慢双击（250~500ms）才不会被拆成两次单击
TAP_CANCEL_MS = winapi.double_click_ms()
HAPPY_WORDS = ["摸摸头好舒服~", "嘿嘿，再摸一下！", "好喜欢和你在一起", "摇尾巴ing~"]
CALL_WORDS_DOG = ["汪？我在！", "叫我啦？", "汪呜~好开心"]
SPIN_WORDS = ["晕乎乎~", "转圈圈撒娇！", "我转得好看吗？"]
CHASE_WORDS = ["追小球去咯！", "小球别跑！", "汪汪！追上你！"]
FIND_WORDS = ["找到我啦！", "嘿嘿被发现了~", "我出来咯！"]
SLEEP_WORDS = ["Zzz...", "困困...", "趴在你旁边睡~"]
EAT_WORDS = ["吧唧吧唧，好吃！", "谢谢投喂！", "吃饱饱~"]
PLAY_WORDS = ["出去玩最喜欢啦！", "嘿嘿陪我玩~"]
SLEEPY_IDLE_KEYS = ("groom", "relax", "sleep")


# 空闲小动作池：(state, gif_key, duration_ms, weight)
# 按 (mood高/低, energy高/低) 分组——同组姿态接近，组内切换跳切不明显
IDLE_ACTION_POOLS = {
    (True, True): [    # 活泼：开心有活力
        (GROOM, "groom", 3000, 3),
        (HAPPY, "happy", 1600, 2),
        (HOP, "hop", 1300, 1),
        (STARE, "chin", 4200, 1),
    ],
    (True, False): [   # 开心但懒
        (RELAX, "relax", 3200, 3),
        (GROOM, "groom", 3000, 2),
        (STARE, "chin", 4200, 1),
    ],
    (False, True): [   # 烦躁有活力
        (STARE, "chin", 4200, 2),
        (GROOM, "groom", 3000, 2),
        (HAPPY, "happy", 1600, 1),
    ],
    (False, False): [  # 萎靡
        (RELAX, "relax", 3200, 3),
        (GROOM, "groom", 3000, 2),
        (STARE, "chin", 4200, 1),
    ],
}
NIGHT_ACTION_POOL = [  # 夜间安静池
    (RELAX, "relax", 4000, 3),
    (GROOM, "groom", 3000, 2),
    (STARE, "chin", 4200, 1),
]


# 点击 Q 弹关键帧：(时间秒, scaleX, scaleY, y偏移px)
# 单击：温和压扁→弹性回弹，0.5s
TAP_SQUASH_FRAMES = [
    (0.00, 1.00, 1.00, 0),
    (0.08, 1.18, 0.82, 0),
    (0.22, 0.92, 1.10, 0),
    (0.36, 1.04, 0.97, 0),
    (0.50, 1.00, 1.00, 0),
]
# 双击：蓄力→腾空→下落→落地小压扁→小回弹，1.4s，弹跳约 90px
# 蓄力可以夸张（准备动作），落地要真实温和，回弹小小一下即可
HOP_SQUASH_FRAMES = [
    (0.00, 1.00, 1.00, 0),
    (0.12, 1.30, 0.70, 0),      # 蓄力压扁（夸张准备）
    (0.22, 0.85, 1.20, -55),    # 起跳拉伸
    (0.55, 1.00, 1.00, -90),    # 腾空最高（停留久一点）
    (0.85, 0.90, 1.10, -25),    # 下落拉伸
    (0.95, 1.15, 0.85, 0),      # 落地小压扁（温和真实）
    (1.10, 0.97, 1.03, 0),      # 小回弹一下
    (1.40, 1.00, 1.00, 0),      # 恢复
]
# 右键挠下巴：温和开心小弹跳，0.7s，弹跳约 22px
CHIN_SQUASH_FRAMES = [
    (0.00, 1.00, 1.00, 0),
    (0.10, 1.15, 0.85, 0),
    (0.18, 0.92, 1.10, -16),
    (0.32, 1.00, 1.00, -22),
    (0.46, 1.08, 0.92, 0),
    (0.56, 0.97, 1.03, 0),
    (0.70, 1.00, 1.00, 0),
]


class BubbleLabel(QLabel):
    """气泡：圆角白底自绘。

    QLabel 在 WA_TranslucentBackground 的顶层窗上不绘制 QSS 背景
    （连纯白 background 都不画），所以底色/描边在 paintEvent 里自己画，
    文字仍交给 QLabel（自动换行/字体/颜色）。
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._bg = QColor(255, 255, 255, 245)
        self._border = QColor("#d9b88a")
        self.setContentsMargins(10, 6, 10, 6)

    def set_text_color(self, color):
        self.setStyleSheet(
            "QLabel{color:%s; background:transparent;"
            "font:9pt 'Microsoft YaHei','SimHei';}" % color)

    def paintEvent(self, ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(self.rect()).adjusted(0.75, 0.75, -0.75, -0.75)
        path = QPainterPath()
        path.addRoundedRect(rect, 10, 10)
        p.fillPath(path, self._bg)
        p.setPen(QPen(self._border, 1))
        p.drawPath(path)
        p.end()
        super().paintEvent(ev)


class PetWidget(QWidget):
    open_chat = pyqtSignal()

    def __init__(self, assets, sound, cfg, parent=None):
        super().__init__(parent)
        self.assets = assets
        self.sound = sound
        self.cfg = cfg

        self.state = IDLE
        self.base_mode = "idle"          # idle / work / nervous
        self.state_until = 0.0
        self.roam_target = None          # QPoint 窗口左上角目标
        self.arrive_action = "base"      # base / sleep / hide / peek / fetch
        self._relocate_speed = 3.2
        self._fetch_done = None          # 叼掉落物到达后的回调
        self.chase_until = 0.0
        self.hide_until = 0.0
        self.next_pace_at = 0.0
        self.sleeping = False
        self.night = False
        self._deep_on_arrive = False

        self.movie = None
        self._last_path = ""
        self._extra_scale = 1.0
        self._cpu_factor = 1.0
        self._corner = False
        self._corner_saved = None   # 全屏躲角落前的位置，退出全屏后还原
        self.locked = False              # 锁定角落时不跟随、不乱跑
        self._walk_visual = False        # 跟随移动时临时播放走路 GIF

        # 鼠标交互
        self._press_global = None
        self._press_t = 0.0
        self._moved = False
        self._picked = False
        self._dbl_seen = False      # 双击后的那次松开不再补一个“摸头”
        self._gesture = deque()
        self._spun = False
        self._cursor_track = deque()
        self._cursor_still_since = time.time()
        self.last_follow_at = 0.0      # 最近一次主动跟随/躲闪的时刻，避让逻辑据此让路
        self._bounce_timer = None
        self._bounce_seq = []
        self._bounce_base_y = 0
        self._last_whine = 0.0
        self._next_idle_flicker = time.time() + random.uniform(3, 8)
        self._last_idle_action = None    # 上一个空闲小动作，避免连续重复

        # 点击 Q 弹形变动画
        self._squash_timer = None
        self._squash_frames = []
        self._squash_start = 0.0
        self._squash_duration = 0.0
        self._squash_ground = None
        self._squash_base_w = 0
        self._squash_base_h = 0

        # v1.3.0 脚印 / 步数
        self._paw_canvas = None
        self._step_counter = None
        self._fx = None
        self._last_paw_pos = None
        self._paw_accum = 0.0

        # v1.3.2 日记埋点
        self._diary_rec = None

        self._init_window()
        self._init_childs()
        self._init_timers()

    # ---------------- 初始化 ----------------
    def _init_window(self):
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))

    def _init_childs(self):
        self.image = QLabel(self)
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setStyleSheet("background:transparent;")

        # 气泡必须是独立顶层窗：作为子控件放在父窗口矩形外会被直接裁掉
        self.bubble = BubbleLabel()
        self.bubble.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.bubble.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.bubble.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.bubble.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.bubble.setWordWrap(True)
        self.bubble.set_text_color(self.cfg.get("bubble_color", "#222222"))
        self.bubble.hide()
        self._bubble_timer = QTimer(self)
        self._bubble_timer.setSingleShot(True)
        self._bubble_timer.timeout.connect(self.bubble.hide)

        self.resize(self.cfg["size"], self.cfg["size"])

    def _init_timers(self):
        self.tick = QTimer(self)
        self.tick.setInterval(50)
        self.tick.timeout.connect(self._tick_move)
        self.tick.start()

        self.behavior = QTimer(self)
        self.behavior.setInterval(1000)
        self.behavior.timeout.connect(self._tick_behavior)
        self.behavior.start()

        self._tap_timer = QTimer(self)
        self._tap_timer.setSingleShot(True)
        self._tap_timer.timeout.connect(self._do_tap)

        self._paw_tick = QTimer(self)
        self._paw_tick.setInterval(50)
        self._paw_tick.timeout.connect(self._tick_paw)
        self._paw_tick.start()

    def set_paw_hooks(self, paw_canvas, step_counter):
        """接入脚印画布与步数计数器（由 PetApp 注入）。"""
        self._paw_canvas = paw_canvas
        self._step_counter = step_counter

    def set_fx(self, engine):
        """接入桌面特效引擎（由 PetApp 注入）。"""
        self._fx = engine

    def set_diary_hooks(self, recorder):
        """接入日记埋点回调 recorder(kind, mood, energy)（由 PetApp 注入）。"""
        self._diary_rec = recorder

    def set_right_menu_provider(self, provider):
        """注入右键菜单提供者 provider() -> QMenu|None。
        返回 None 时右键回退为原来的“挠下巴”（无插件时体验不变）。"""
        self._right_menu_provider = provider

    def do_chin(self):
        """挠下巴：原右键行为，现也供插件菜单里的“挠下巴”项调用。"""
        self.sleeping = False
        self._rec_activity("chin")
        self.set_state(CHIN, 1900)
        self.show_bubble("下巴~好舒服~", 1800)
        self._snd("happy")

    def _rec_activity(self, kind):
        if self._diary_rec is None:
            return
        try:
            self._diary_rec(kind, self.cfg.get("mood"), self.cfg.get("energy"))
        except Exception:
            pass

    def _tick_paw(self):
        """自主移动时累计步数并按间隔留脚印（拖拽/睡眠不计）。"""
        if (self._paw_canvas is None and self._step_counter is None
                and self._fx is None):
            return
        if self.state in (PICKED, SPIN, SLEEP, DEEP, HIDE, GO_SLEEP):
            self._last_paw_pos = None
            return
        moving = self.state in (WALK, FLEE, CHASE, NERVOUS) or self._walk_visual
        cur = (self.x(), self.y())
        old = self._last_paw_pos
        self._last_paw_pos = cur
        if not moving or old is None:
            return
        dx, dy = cur[0] - old[0], cur[1] - old[1]
        dist = math.hypot(dx, dy)
        if dist < 1:
            return
        if self._step_counter is not None:
            self._step_counter.add_distance(dist)
        self._paw_accum += dist
        if self._paw_accum >= 28:
            self._paw_accum = 0.0
            ang = math.atan2(dy, dx) if (dx or dy) else 0.0
            fx = self.x() + self.width() // 2
            fy = self.y() + self.height() - 4
            if self._paw_canvas is not None and self.cfg.get("paw_enabled", True):
                self._paw_canvas.add(fx, fy, ang)
            if self._fx is not None:
                self._fx.feed_step(fx, fy, ang)

    def place_initial(self):
        s = self.cfg.get("size", 150)
        geo = QApplication.primaryScreen().availableGeometry()
        pos = self.cfg.get("pos")
        ok = False
        if pos and len(pos) == 2:
            x, y = int(pos[0]), int(pos[1])
            if geo.left() - 100 <= x <= geo.right() and geo.top() - 100 <= y <= geo.bottom():
                self.move(x, y)
                ok = True
        if not ok:
            self.move(geo.right() - s - random.randint(60, 180),
                      geo.bottom() - s - random.randint(40, 120))
        self._play("idle")
        self.show()

    # ---------------- GIF / 尺寸 / 帧率 ----------------
    def _speed_pct(self):
        base = cfgmod.FPS_PRESETS.get(self.cfg.get("fps_mode", "balanced"), 80)
        factor = self._cpu_factor
        if self.state in (SLEEP, DEEP):
            factor *= 0.4
        return max(20, int(base * factor))

    def update_speed(self):
        if self.movie:
            self.movie.setSpeed(self._speed_pct())

    def set_cpu_factor(self, f):
        if abs(f - self._cpu_factor) > 0.01:
            self._cpu_factor = f
            self.update_speed()

    def set_pet_opacity(self, opacity):
        """设置宠物透明度（0.3~1.0），1.0=不透明。"""
        try:
            self.setWindowOpacity(max(0.3, min(1.0, float(opacity))))
        except Exception:
            pass

    def _ground_center(self):
        return QPoint(self.x() + self.width() // 2, self.y() + self.height())

    def _place_by_ground(self, ground):
        self.move(ground.x() - self.width() // 2, ground.y() - self.height())

    def _play(self, key):
        self._stop_squash()
        path = self.assets.resolve(key, self.cfg.get("character", "xiaobai"))
        if not path or not os.path.exists(path):
            return
        if path == self._last_path and self.movie is not None:
            return
        ground = self._ground_center()
        movie = QMovie(path)
        # 用默认缓存模式（不要 CacheNone）：桌宠 GIF 多为带透明 + 分帧局部
        # 更新的多帧动画，CacheNone 会破坏帧合成，偶发把帧画在黑底上、颜色错乱。
        movie.jumpToFrame(0)
        img = movie.currentImage()
        w0, h0 = img.width(), img.height()
        if w0 <= 0 or h0 <= 0:
            w0 = h0 = 300
        # 统一高度：所有 GIF 以固定高度显示，宽度按各自比例自适应，
        # 让狗在不同动作之间“身高”一致，观感更统一。
        le = max(40, int(self.cfg["size"] * self._extra_scale))
        nh = le
        nw = max(1, int(w0 * le / h0))
        # 高清渲染：按屏幕缩放比做整数倍超采样渲染，再让 Qt 按该倍率显示，
        # 保证 125%/150% 缩放的屏幕上也能铺满物理像素，表情不再发虚发糊。
        # 渲染尺寸永远取逻辑尺寸的至少 2 倍，兼顾普通屏幕的抗锯齿效果。
        try:
            dpr = self.devicePixelRatioF() or 1.0
        except Exception:
            dpr = 1.0
        ss = max(2, int(round(dpr)))
        movie.setScaledSize(QSize(nw * ss, nh * ss))
        movie.setSpeed(self._speed_pct())
        self.image.clear()  # 先清空旧图，解除旧 movie 绑定
        self.image.setGeometry(0, 0, nw, nh)

        def _apply_frame():
            pm = movie.currentPixmap()
            if not pm.isNull():
                pm.setDevicePixelRatio(ss)
                self.image.setPixmap(pm)

        movie.frameChanged.connect(_apply_frame)
        _apply_frame()
        movie.start()

        old = self.movie
        self.movie = movie
        self._last_path = path
        self._src_w, self._src_h = w0, h0   # 记住源帧尺寸，_rescale 用
        self._render_ss = ss
        self.resize(nw, nh)
        self._place_by_ground(ground)
        if old is not None:
            try:
                old.frameChanged.disconnect()
            except Exception:
                pass
            old.stop()
            old.deleteLater()  # 不 jumpToFrame(0)，避免销毁前多余解码一帧进 QPixmapCache



    def _rescale(self):
        """只缩放当前动画的显示尺寸，不重新加载 GIF 文件。

        拖动大小滑块时每像素都触发，若每次都 replay() 会重新创建 QMovie、
        jumpToFrame(0)、随机抽图，表现为表情包快速循环播放。
        这里只改 setScaledSize + 窗口尺寸 + 立即重绘当前帧，动画连续不跳。
        """
        self._stop_squash()
        m = self.movie
        if m is None:
            return
        w0 = getattr(self, "_src_w", 0)
        h0 = getattr(self, "_src_h", 0)
        if w0 <= 0 or h0 <= 0:
            return
        le = max(40, int(self.cfg["size"] * self._extra_scale))
        nh = le
        nw = max(1, int(w0 * le / h0))
        ss = getattr(self, "_render_ss", 2)
        ground = self._ground_center()
        m.setScaledSize(QSize(nw * ss, nh * ss))
        self.image.setGeometry(0, 0, nw, nh)
        pm = m.currentPixmap()
        if not pm.isNull():
            pm.setDevicePixelRatio(ss)
            self.image.setPixmap(pm)
        self.resize(nw, nh)
        self._place_by_ground(ground)

    def replay(self):
        """皮肤/大小/缩放变化后重新加载当前状态动画。"""
        self._last_path = ""
        self._play(STATE_GIF.get(self.state, "idle"))

    def set_state(self, state, duration_ms=0, gif_key=None):
        self.state = state
        self._walk_visual = False
        self.state_until = time.time() + duration_ms / 1000.0 if duration_ms else 0.0
        self._play(gif_key or STATE_GIF.get(state, "idle"))

    def _back_to_base(self):
        if self.sleeping:
            self.set_state(DEEP if self.state == DEEP else SLEEP)
        elif self.base_mode == "work":
            self.set_state(WORK)

        else:
            self.set_state(IDLE)

    # ---------------- 气泡 ----------------
    def refresh_bubble_style(self):
        """设置里改了气泡文字颜色后调用；气泡正在显示就立即重排。"""
        self.bubble.set_text_color(self.cfg.get("bubble_color", "#222222"))
        self.bubble.update()
        if self.bubble.isVisible():
            text = self.bubble.text()
            if text:
                self.show_bubble(text)

    def _position_bubble(self):
        bw, bh = self.bubble.width(), self.bubble.height()
        center = self.frameGeometry().center()
        # 屏幕几何缓存：只有狗跨出当前屏幕时才重新枚举显示器，
        # 避免拖拽途中每次定位都调 screenAt()
        geo = getattr(self, "_bubble_geo", None)
        if geo is None or not geo.contains(center):
            screen = QApplication.screenAt(center) or QApplication.primaryScreen()
            geo = screen.availableGeometry()
            self._bubble_geo = geo
        x = int(center.x() - bw / 2)
        y = self.y() - bh - 8
        if y < geo.top() + 2:
            y = self.y() + self.height() + 4
        x = max(geo.left() + 2, min(geo.right() - bw - 2, x))
        y = max(geo.top() + 2, min(geo.bottom() - bh - 2, y))
        # 位置没变就别动：独立分层透明窗每 move 一次都要重新合成，
        # 以前定时器每 50ms 无脑 move 是静止时也掉帧的原因
        if self.bubble.x() != x or self.bubble.y() != y:
            self.bubble.move(x, y)

    def show_bubble(self, text, ms=None):
        if not self.cfg.get("bubble_on", True):
            return
        if not text:
            return
        if ms is None:
            ms = int(self.cfg.get("bubble_duration", 4200))
        self.bubble.setText(text)
        fm = QFontMetrics(self.bubble.font())
        # 26=左右内边距20+边框2+余量4；宽度上限放到260，长句折行更舒服
        bw = min(260, max(90, fm.horizontalAdvance(text) + 26))
        # 让 QLabel 自己算高度：字体/换行/padding/边框全部包含，
        # 以前手算的文本宽度比实际宽2px，两句刚好折成两行时会裁掉第二行
        bh = self.bubble.heightForWidth(bw)
        if bh < 30:  # 极端情况下的兜底
            rect = fm.boundingRect(QRect(0, 0, bw - 22, 600),
                                   Qt.TextFlag.TextWordWrap, text)
            bh = rect.height() + 14
        self.bubble.resize(bw, max(30, bh))
        self._position_bubble()
        self.bubble.raise_()
        self.bubble.show()
        self._bubble_timer.start(ms)

    # ---------------- 对外动作接口 ----------------
    def wake(self):
        if self.state in (SLEEP, DEEP, GO_SLEEP):
            self.sleeping = False
            self.show_bubble("嗯？你回来了！")
            self.set_state(HAPPY, 1200)
            self._snd("happy")

    def go_sleep(self, deep=False):
        if self.state in (PICKED, CHASE, HIDE, GO_SLEEP):
            return
        self.sleeping = True
        # 原地入睡：不再朝鼠标位置走位（那正是“长时间不动后小狗自己挪一点”的来源），
        # 直接在当前位置切换到睡姿；deep=True 时进入深睡（蜷成一团）。
        self.set_state(DEEP if deep else SLEEP)
        self.show_bubble(random.choice(SLEEP_WORDS), 2400)

    def set_deep(self, on):
        if self.sleeping and self.state in (SLEEP, DEEP):
            self.set_state(DEEP if on else SLEEP)

    def set_nervous(self, on):
        """CPU 踱步已下线：不再进入/维持 NERVOUS 状态（保留方法壳兼容旧调用）。"""

    def set_companion(self, mode):
        if mode == "code":
            self.base_mode = "work"
            if self.state in (IDLE, WALK, STARE, RELAX, GROOM):
                self.set_state(WORK)
        elif self.base_mode == "work":
            self.base_mode = "idle"
            if self.state == WORK:
                self.set_state(IDLE)

    def is_free(self):
        return self.state in (IDLE, WALK, STARE, GROOM, RELAX, WORK) and not self._corner

    def relocate(self, center_x, center_y, speed=3.2, immediate=False, force=False):
        if not force and self.state in (PICKED, SLEEP, DEEP, CHASE, HIDE, GO_SLEEP, SPIN):
            return False
        tx = int(center_x - self.width() / 2)
        ty = int(center_y - self.height() / 2)
        if immediate:
            self.roam_target = None
            self.arrive_action = "base"
            self.move(tx, ty)
            if self.state in (WALK, FLEE, NERVOUS):
                self.set_state(IDLE)
            return True
        self.arrive_action = "base"
        self._relocate_speed = speed
        self.roam_target = QPoint(tx, ty)
        if self.state != WALK:
            self.set_state(WALK)
        return True

    def peek_at(self, center_x, top_y):
        """浏览器偷看已下线：保留方法壳防止旧入口引用报错，不再触发任何行为。"""
        return

    def fetch_item(self, center_x, center_y, done_cb=None):
        """让小狗走过去把掉落物叼起来：到达后调用 done_cb()。

        返回 True 表示已派出，False 表示当前正忙没法去叼。
        """
        if not self.is_free():
            return False
        self.sleeping = False
        self._fetch_done = done_cb
        self.arrive_action = "fetch"
        self._relocate_speed = 4.2
        self.roam_target = QPoint(int(center_x - self.width() / 2),
                                  int(center_y - self.height()))
        if self.state != WALK:
            self.set_state(WALK)
        return True

    def corner_hide(self, on, workarea=None):
        """全屏时把自己缩成小团躲到屏幕角落，只露出一点点。

        进入时：保存当前位置，缩小到约 40% 并贴到角落；
        退出时：恢复原来大小，并回到进入全屏前的位置。
        """
        if on and not self._corner:
            self._corner_saved = (self.x(), self.y())
        self._corner = on
        if on:
            self._extra_scale = 0.4
            self._last_path = ""
            self._play(STATE_GIF.get(self.state, "idle"))
            if workarea:
                l, t, r, b = workarea
                w, h = self.width(), self.height()
                # 贴住角落并只露一条边，真·藏在角落
                self.move(r - w - 6, b - h - 6)
            self.show_bubble("退出全屏我就出现啦~", 2600)
        else:
            self._extra_scale = 1.0
            self._last_path = ""
            self._play(STATE_GIF.get(self.state, "idle"))
            if self._corner_saved is not None:
                self.move(self._corner_saved[0], self._corner_saved[1])
                self._corner_saved = None
            self.show_bubble("回来啦~", 1600)

    def feed(self):
        self.sleeping = False
        self._rec_activity("feed")
        self.set_state(EAT, 2600)
        self.show_bubble(random.choice(EAT_WORDS))
        self._snd("munch")

    def play_with(self):
        self.sleeping = False
        self._rec_activity("play")
        self.set_state(PLAY, 3500)
        self.show_bubble(random.choice(PLAY_WORDS))
        self._snd("happy")

    def start_chase(self, seconds=11.0):
        self.sleeping = False
        self._rec_activity("chase")
        self.chase_until = time.time() + seconds
        self.set_state(CHASE)
        self.show_bubble(random.choice(CHASE_WORDS), 2200)
        self._snd("happy")

    def start_hide(self):
        self.sleeping = False
        self._rec_activity("hide")
        geo = (QApplication.screenAt(self.frameGeometry().center())
               or QApplication.primaryScreen()).availableGeometry()
        side = random.choice(["left", "right"])
        tx = geo.left() - int(self.width() * 0.72) if side == "left" \
            else geo.right() - int(self.width() * 0.28)
        ty = random.randint(geo.top() + 20, geo.bottom() - self.height() - 20)
        self.roam_target = QPoint(tx, ty)
        self.arrive_action = "hide"
        self.hide_until = time.time() + 16
        self.set_state(WALK)

    def _pop_hide(self):
        geo = (QApplication.screenAt(self.frameGeometry().center())
               or QApplication.primaryScreen()).availableGeometry()
        c = self.frameGeometry().center()
        tx = max(geo.left() + self.width() // 2 + 4,
                 min(geo.right() - self.width() // 2 - 4, c.x()))
        ty = max(geo.top() + self.height() // 2 + 4,
                 min(geo.bottom() - self.height() // 2 - 4, c.y()))
        self.roam_target = QPoint(int(tx - self.width() / 2), int(ty - self.height() / 2))
        self.arrive_action = "base"
        self.set_state(HAPPY, 1700)
        self.show_bubble(random.choice(FIND_WORDS))
        self._snd("chirp")

    # ---------------- 声音 ----------------
    def _snd(self, kind):
        table = {
            "happy": "bark",
            "chime": "chime", "whine": "whine",
            "chirp": "chirp", "munch": "munch",
        }
        name = table.get(kind)
        if name:
            self.sound.play(name)

    # ---------------- 鼠标交互 ----------------
    def set_mouse_through(self, on):
        """关闭鼠标交互时开启鼠标穿透：点击/悬停直接落到下层应用。

        全穿透后仍可通过系统托盘、全局快捷键与它交互。
        """
        try:
            self.setAttribute(
                Qt.WidgetAttribute.WA_TransparentForMouseEvents, bool(on))
        except Exception:
            pass
        # Qt 属性只在本进程内生效，还需系统级 WS_EX_TRANSPARENT 才能
        # 让点击真正落到下层窗口（否则小狗仍会吞掉桌面点击）。
        try:
            from . import winapi
            winapi.set_click_through(int(self.winId()), bool(on))
        except Exception:
            pass

    def mousePressEvent(self, e):
        if not self.cfg.get("mouse_interaction", True):
            e.ignore()
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._stop_squash(restore=True)   # 再次点击：重置当前动画再播放新动作
            self._tap_timer.stop()
            g = e.globalPosition().toPoint()
            self._press_global = g
            self._press_t = time.time()
            self._press_offset = g - self.frameGeometry().topLeft()
            self._moved = False
            self._picked = False
            self._spun = False
            self._gesture = deque([(g.x(), g.y(), time.time())])
            self.setCursor(QCursor(Qt.CursorShape.ClosedHandCursor))
            e.accept()
        elif e.button() == Qt.MouseButton.RightButton:
            provider = getattr(self, "_right_menu_provider", None)
            if provider is not None:
                try:
                    menu = provider()
                except Exception:
                    menu = None
                if menu is not None:
                    menu.exec(e.globalPosition().toPoint())
                    e.accept()
                    return
            self.do_chin()
            e.accept()
        elif e.button() == Qt.MouseButton.MiddleButton:
            self._rec_activity("talk")
            self.open_chat.emit()
            e.accept()

    def mouseMoveEvent(self, e):
        if not self.cfg.get("mouse_interaction", True):
            return
        if not (e.buttons() & Qt.MouseButton.LeftButton) or self._press_global is None:
            return
        g = e.globalPosition().toPoint()
        now = time.time()
        if not self._moved and (g - self._press_global).manhattanLength() > 8:
            self._moved = True
        if not self._picked and not self._spun and now - self._press_t > 0.2:
            # 按住 0.2 秒后才“抱起来”，此后窗口才跟随鼠标；
            # 短按快滑属于甩动手势，窗口不能被随手拖走
            self._picked = True
            self.sleeping = False
            self.set_state(PICKED)
        # 手势轨迹始终记录（供画圈/甩动判定）
        if self._moved or self._picked:
            self._gesture.append((g.x(), g.y(), now))
            while self._gesture and now - self._gesture[0][2] > 0.9:
                self._gesture.popleft()
        if self._picked or self._spun:
            tl = g - self._press_offset
            screen = QApplication.screenAt(g) or QApplication.primaryScreen()
            geo = screen.availableGeometry()
            self.move(max(geo.left(), min(geo.right() - self.width(), tl.x())),
                      max(geo.top(), min(geo.bottom() - self.height(), tl.y())))
            # 气泡与主窗在同一次鼠标事件里同步移动，
            # 若等 50ms 定时器追赶，两个独立窗口一前一后就会显得卡顿
            if self.bubble.isVisible():
                self._position_bubble()
        if not self._spun and self._moved:
            self._detect_circle()

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton or self._press_global is None:
            return
        self.setCursor(QCursor(Qt.CursorShape.OpenHandCursor))
        g = e.globalPosition().toPoint()
        now = time.time()
        recent = [p for p in self._gesture if now - p[2] < 0.18]
        flick = False
        if len(recent) >= 2 and self._moved:
            dt = max(0.01, now - recent[0][2])
            disp = ((g.x() - recent[0][0]) ** 2 + (g.y() - recent[0][1]) ** 2) ** 0.5
            flick = disp / dt > 1.0 and disp > 48
        press_start = self._press_global
        was_picked = self._picked
        total_move = (g - press_start).manhattanLength() if press_start else 9999
        self._press_global = None
        self._picked = False
        self._gesture.clear()

        if self._dbl_seen:
            self._dbl_seen = False
            e.accept()
            return
        if self._spun:
            e.accept()
            return
        if flick:
            self.start_chase()
        elif not was_picked and total_move < 14 and now - self._press_t < 0.5:
            # 轻点（容许 14px 内的手抖）：形变立即触发（无延迟体感），
            # 摸头状态/气泡/声音延迟一个双击窗口，若第二次按下会取消，保证双击优先
            self._play_tap_squash()
            self._tap_timer.start(TAP_CANCEL_MS)
        else:
            if was_picked:
                self._land_bounce()
            self._back_to_base()
        e.accept()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._tap_timer.stop()
            self._dbl_seen = True
            self.sleeping = False
            self._rec_activity("call")
            self.set_state(HOP, 1300)
            self._play_hop_squash()
            self.show_bubble(random.choice(CALL_WORDS_DOG), 1800)
            self._snd("chirp")
            e.accept()

    def _do_tap(self):
        self.sleeping = False
        self._rec_activity("pet")
        self.set_state(PET, 1900)

        self.show_bubble(random.choice(HAPPY_WORDS), 2000)
        self._snd("happy")

    def _detect_circle(self):
        pts = list(self._gesture)
        if len(pts) < 7:
            return
        duration = pts[-1][2] - pts[0][2]
        if duration < 0.25 or duration > 1.2:
            return
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        radii = [math.hypot(p[0] - cx, p[1] - cy) for p in pts]
        if min(radii) < 6 or max(radii) > 150:
            return
        angle_sum = 0.0
        for a, b in zip(pts, pts[1:]):
            d = math.atan2(b[1] - cy, b[0] - cx) - math.atan2(a[1] - cy, a[0] - cx)
            while d > math.pi:
                d -= 2 * math.pi
            while d < -math.pi:
                d += 2 * math.pi
            angle_sum += d
        sx = max(p[0] for p in pts) - min(p[0] for p in pts)
        sy = max(p[1] for p in pts) - min(p[1] for p in pts)
        if abs(angle_sum) > 5.0 and sx < 220 and sy < 220:
            self._spun = True
            self._picked = False
            self.sleeping = False
            self._rec_activity("spin")
            self.set_state(SPIN, 2100)
            self.show_bubble(random.choice(SPIN_WORDS), 2000)
            self._snd("chime")

    def _land_bounce(self):
        self._bounce_base_y = self.y()
        self._bounce_seq = [0, -7, 0, -3, 0]
        self._start_bounce()

    def _hop_bounce(self):
        self._bounce_base_y = self.y()
        self._bounce_seq = [0, -12, -16, -10, 0]
        self._start_bounce()

    def _start_bounce(self):
        if self._bounce_timer is None:
            self._bounce_timer = QTimer(self)
            self._bounce_timer.setInterval(45)
            self._bounce_timer.timeout.connect(self._bounce_step)
        self._bounce_timer.start()
        self._bounce_step()

    def _bounce_step(self):
        if not self._bounce_seq:
            self._bounce_timer.stop()
            return
        dy = self._bounce_seq.pop(0)
        self.move(self.x(), self._bounce_base_y + dy)

    # ---------------- 点击 Q 弹形变动画 ----------------
    def _play_tap_squash(self):
        """单击原地 Q 弹：压扁→弹性回弹。受 click_squash_on 开关控制。"""
        if not self.cfg.get("click_squash_on", True):
            return
        self._start_squash(TAP_SQUASH_FRAMES)

    def _play_hop_squash(self):
        """双击弹跳：蓄力→腾空→下落→落地 Q 弹回弹。受 click_squash_on 控制。"""
        if not self.cfg.get("click_squash_on", True):
            self._hop_bounce()  # 关闭时退回纯位置弹跳
            return
        self._start_squash(HOP_SQUASH_FRAMES)

    def _play_chin_squash(self):
        """右键挠下巴：开心小弹跳 Q 弹。受 click_squash_on 控制。"""
        if not self.cfg.get("click_squash_on", True):
            return
        self._start_squash(CHIN_SQUASH_FRAMES)

    def _start_squash(self, frames):
        """启动形变动画。frames: [(t秒, scaleX, scaleY, y偏移px), ...] 按 t 升序。"""
        if not frames:
            return
        m = self.movie
        if m is None:
            return
        w0 = getattr(self, "_src_w", 0)
        h0 = getattr(self, "_src_h", 0)
        if w0 <= 0 or h0 <= 0:
            return
        # 基础逻辑尺寸（与 _play/_rescale 计算一致）
        le = max(40, int(self.cfg["size"] * self._extra_scale))
        self._squash_base_h = le
        self._squash_base_w = max(1, int(w0 * le / h0))
        # 锚定底部中心，Q 弹脚下不动
        self._squash_ground = self._ground_center()
        self._squash_frames = list(frames)
        self._squash_duration = frames[-1][0]
        self._squash_start = time.time()
        if self._squash_timer is None:
            self._squash_timer = QTimer(self)
            self._squash_timer.setInterval(33)  # 30fps 足够，减少临时 pixmap
            self._squash_timer.timeout.connect(self._squash_tick)
        self._squash_timer.start()
        self._squash_tick()

    def _squash_tick(self):
        if not self._squash_frames:
            self._stop_squash()
            return
        elapsed = time.time() - self._squash_start
        if elapsed >= self._squash_duration:
            self._apply_squash(*self._squash_frames[-1][1:])
            self._stop_squash()
            return
        # 在关键帧区间内线性插值（关键帧本身已设计过冲，线性即可表现 Q 弹）
        fr = self._squash_frames
        sx, sy, dy = fr[-1][1:]
        for i in range(1, len(fr)):
            if fr[i][0] >= elapsed:
                t0, sx0, sy0, dy0 = fr[i - 1]
                t1, sx1, sy1, dy1 = fr[i]
                if t1 > t0:
                    a = (elapsed - t0) / (t1 - t0)
                    sx = sx0 + (sx1 - sx0) * a
                    sy = sy0 + (sy1 - sy0) * a
                    dy = dy0 + (dy1 - dy0) * a
                else:
                    sx, sy, dy = sx1, sy1, dy1
                break
        self._apply_squash(sx, sy, dy)

    def _apply_squash(self, sx, sy, dy):
        """把形变应用到 movie 渲染尺寸 + 窗口位置，保持透明通道与高清渲染。"""
        m = self.movie
        if m is None or self._squash_ground is None:
            return
        nw = max(1, int(self._squash_base_w * sx))
        nh = max(1, int(self._squash_base_h * sy))
        ss = getattr(self, "_render_ss", 2)
        m.setScaledSize(QSize(nw * ss, nh * ss))
        self.image.setGeometry(0, 0, nw, nh)
        pm = m.currentPixmap()
        if not pm.isNull():
            pm.setDevicePixelRatio(ss)
            self.image.setPixmap(pm)
        self.resize(nw, nh)
        g = self._squash_ground
        # 底部中心锚定 + y 偏移（dy 负=向上）
        self.move(int(g.x() - nw / 2), int(g.y() - nh + dy))

    def _stop_squash(self, restore=False):
        """停止形变动画。restore=True 时若曾有动画则 _rescale 恢复基础尺寸。"""
        if self._squash_timer is not None:
            self._squash_timer.stop()
        active = bool(self._squash_frames)
        self._squash_frames = []
        self._squash_ground = None
        if restore and active:
            self._rescale()
        if active:
            # 形变期间产生的临时 pixmap 立即归还年轻代，避免累积
            try:
                import gc
                gc.collect(1)
            except Exception:
                pass

    # ---------------- 移动节拍 ----------------
    def _clamp_in(self, x, y):
        if self.state == HIDE:
            return int(x), int(y)
        screen = QApplication.screenAt(QPoint(int(x + self.width() / 2),
                                              int(y + self.height() / 2))) \
                 or QApplication.primaryScreen()
        geo = screen.availableGeometry()
        return (int(max(geo.left(), min(geo.right() - self.width(), x))),
                int(max(geo.top(), min(geo.bottom() - self.height(), y))))

    def _step_to(self, target, speed):
        dx, dy = target.x() - self.x(), target.y() - self.y()
        dist = math.hypot(dx, dy)
        if dist <= speed:
            self.move(int(target.x()), int(target.y()))
            return True
        self.move(int(self.x() + dx / dist * speed),
                  int(self.y() + dy / dist * speed))
        return False

    def _set_walk_visual(self, on):
        if on and not self._walk_visual:
            self._walk_visual = True
            self._play("walk")
        elif not on and self._walk_visual:
            self._walk_visual = False
            self._last_path = ""
            self._play(STATE_GIF[self.state])

    def _tick_move(self):
        now = time.time()
        cur = QCursor.pos()
        self._cursor_track.append((cur.x(), cur.y(), now))
        while self._cursor_track and now - self._cursor_track[0][2] > 0.5:
            self._cursor_track.popleft()
        moved_recent = math.hypot(cur.x() - self._cursor_track[0][0],
                                  cur.y() - self._cursor_track[0][1])
        if moved_recent > 4:
            self._cursor_still_since = now

        st = self.state

        if st in TEMP_STATES and now > self.state_until:
            self._back_to_base()
            return

        if st in (PICKED, SPIN):
            # 拖拽时气泡已在 mouseMoveEvent 中同步定位，
            # tick 里不再重复做窗口查询和 move
            return

        if self.bubble.isVisible():
            if not self.isVisible():
                self.bubble.hide()
            else:
                self._position_bubble()

        center = self.frameGeometry().center()
        dist_cur = math.hypot(cur.x() - center.x(), cur.y() - center.y())

        if st == CHASE:
            if now > self.chase_until:
                self.show_bubble("呼…追到啦！", 2200)
                self.set_state(RELAX, 2600)
                return
            target = QPoint(cur.x() - self.width() // 2, cur.y() - self.height() + 18)
            self.move(*self._clamp_in(*self._calc_step(target, 6.2)))
            return

        if st == HIDE:
            if now > self.hide_until or dist_cur < 130:
                self._pop_hide()
            return

        if st == GO_SLEEP:
            if self._step_to(self.roam_target, 4.6):
                self.sleeping = True
                self.set_state(DEEP if self._deep_on_arrive else SLEEP)
            return

        if st == WALK:
            if self._step_to(self.roam_target, self._relocate_speed):
                action = self.arrive_action
                if action == "sleep":
                    self.sleeping = True
                    self.set_state(DEEP if self._deep_on_arrive else SLEEP)
                elif action == "hide":
                    self.set_state(HIDE)
                elif action == "peek":
                    self.set_state(PEEK, 4200)
                elif action == "fetch":
                    cb = self._fetch_done
                    self._fetch_done = None
                    self.arrive_action = "base"
                    self.set_state(HAPPY, 1800)
                    if cb:
                        try:
                            cb()
                        except Exception:
                            pass
                else:
                    self._back_to_base()
            return

        if st == FLEE:
            self._step_to(self.roam_target, 6.0)
            return

        if st == NERVOUS:
            # CPU 踱步已下线：残留的 NERVOUS 状态立即退回闲逛/发呆，不再随机移动
            self.roam_target = None
            self._back_to_base()
            return

        if st == STARE and moved_recent > 6:
            self._back_to_base()
            return

        if st in RELAXED_STATES and self.cfg.get("mouse_interaction", True) \
                and not self._corner and not self.locked \
                and self.base_mode != "work":
            self._follow(cur, dist_cur, now)

    def _calc_step(self, target_tl, speed):
        dx, dy = target_tl.x() - self.x(), target_tl.y() - self.y()
        dist = math.hypot(dx, dy)
        if dist <= speed:
            return target_tl.x(), target_tl.y()
        return self.x() + dx / dist * speed, self.y() + dy / dist * speed

    def _follow(self, cur, dist_cur, now):
        mode = self.cfg.get("follow_mode", "sticky")
        if mode == "off":
            self._set_walk_visual(False)
            return
        if self.state == STARE:
            # 正盯着光标＝在陪用户玩，持续豁免避让，
            # 否则它会被“智能避让”当成挡住窗口的狗拽去角落
            self.last_follow_at = now
        still_for = now - self._cursor_still_since

        # 光标在附近停 2 秒：盯着看、摇尾巴
        if dist_cur < 150 and still_for > 2.0 and self.state != STARE:
            self._set_walk_visual(False)
            self.set_state(STARE, 4200)
            self.last_follow_at = now
            return

        if mode == "sticky":
            # 速度随距离线性变化：身边小碎步，越远跑得越快（上限 6.5/拍）
            if dist_cur <= 90:
                self._set_walk_visual(False)
                return
            # 光标已静止很久就不再跑过去，避免用户没在操作时小狗突然自己乱跑
            if still_for > 3.0:
                self._set_walk_visual(False)
                return
            speed = min(6.5, 1.8 + dist_cur * 0.009)
            target = QPoint(cur.x() - self.width() // 2, cur.y() - self.height() + 20)
            nx, ny = self._clamp_in(*self._calc_step(target, speed))
            if nx != self.x() or ny != self.y():
                self.move(nx, ny)
                self.last_follow_at = now
            self._set_walk_visual(True)

        elif mode == "timid":
            recent = [p for p in self._cursor_track if now - p[2] < 0.25]
            c_speed = 0.0
            if len(recent) >= 2:
                c_speed = math.hypot(recent[-1][0] - recent[0][0],
                                     recent[-1][1] - recent[0][1])
            if c_speed > 24 and dist_cur < 250:
                self._set_walk_visual(False)
                ax = self.frameGeometry().center().x() - cur.x()
                ay = self.frameGeometry().center().y() - cur.y()
                if abs(ax) < 1 and abs(ay) < 1:
                    ax = random.choice([-1, 1])
                length = max(1.0, math.hypot(ax, ay))
                x, y = self._clamp_in(self.x() + ax / length * 220,
                                      self.y() + ay / length * 220)
                self.roam_target = QPoint(x, y)
                self.arrive_action = "base"
                self.set_state(FLEE, 700)
                self.last_follow_at = now
                self._snd("whine")
            elif still_for > 2.5 and dist_cur > 120:
                target = QPoint(cur.x() - self.width() // 2, cur.y() - self.height() + 16)
                nx, ny = self._clamp_in(*self._calc_step(target, 2.3))
                if nx != self.x() or ny != self.y():
                    self.move(nx, ny)
                    self.last_follow_at = now
                self._set_walk_visual(True)
            else:
                self._set_walk_visual(False)

    def _tick_behavior(self):
        now = time.time()
        st = self.state
        if st != IDLE:
            return
        if self.base_mode == "work":
            return
        if now < self._next_idle_flicker:
            return
        mood = self.cfg.get("mood", 80)
        energy = self.cfg.get("energy", 80)
        # 间隔随心情：高心情更爱动（间隔短），低心情更懒（间隔长）；整体放慢避免频繁跳切
        self._next_idle_flicker = now + random.uniform(8, 18) * (1.3 - mood / 250)
        # 触发概率随心情：高心情 0.4，低心情 0.18；夜间固定 0.28
        prob = 0.28 if self.night else (0.18 + mood / 450)
        if random.random() > prob:
            return
        # 选动作池：夜间独立池；白天按 mood/energy 四象限
        if self.night:
            pool = NIGHT_ACTION_POOL
        else:
            pool = IDLE_ACTION_POOLS.get((mood >= 50, energy >= 50),
                                          IDLE_ACTION_POOLS[(True, True)])
        # 避免连续同一个动作，降低跳切突兀感
        candidates = [a for a in pool if a[0] != self._last_idle_action] or pool
        chosen = random.choices(candidates, weights=[a[3] for a in candidates])[0]
        self._last_idle_action = chosen[0]
        self.set_state(chosen[0], chosen[2], gif_key=chosen[1])
