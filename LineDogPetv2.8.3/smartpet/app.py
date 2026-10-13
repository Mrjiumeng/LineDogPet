# -*- coding: utf-8 -*-
"""
总装：托盘图标、系统感知调度、智能避让、全局快捷键、AI 接线、小游戏、持久化。
所有定时器都很低频（50ms 移动节拍 / 1s / 2s / 5s），配合 GIF 即用即弃，
空闲内存只有 Python+Qt 基线（实测几十 MB 内）。
"""
import sys
import time
import random
import math
import os

from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu, QMessageBox, QWidget, QPushButton,
    QGridLayout, QWidgetAction,
)
from PyQt6.QtGui import QAction, QIcon, QCursor, QPixmapCache, QDesktopServices
from PyQt6.QtCore import QTimer, QObject, QAbstractNativeEventFilter, QUrl
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkRequest

from . import config as cfgmod
from . import winapi
from .assets import AssetIndex, SoundManager
from .pet import (
    PetWidget, IDLE, WALK, FLEE, HAPPY, PET as PET_S, CHIN, PICKED, SPIN, HOP,
    SLEEP, DEEP, GO_SLEEP, CHASE, HIDE, NERVOUS, WORK, PEEK, EAT, PLAY, PAPER,
    RELAX, GROOM, STARE,
)
from . import ai as ai_mod
from . import chatdb
from .panels import (SettingsDialog, StatusDialog, ChatDialog, DiaryDialog,
                     MilestoneDialog, generate_style)
from . import diary as diary_mod
from .pawprint import PawCanvas, StepCounter, Prank, ShotBubble
from .effects import EffectEngine, EffectLayer, CursorLightLayer
from .link import LinkMonitor, PAWLIVEWALL_MUTEX
from .observe import ObserveManager, MusicDetector, NightCompanion
from .drops import DroppedItem, random_item
from .cursorpet import CursorPet
from .bongopet import BongoPet
from .pluginapi import PluginManager

HOTKEY_ID = 0xD001

CODE_KEYWORDS = (
    "VISUAL STUDIO CODE", "VSCODE", "PYCHARM", "INTELLIJ", "IDEA", "WEBSTORM",
    "CURSOR", "WINDSURF", "SUBLIME", "NOTEPAD++", "GOLAND", "CLION", "RIDERS",
    "PHPSTORM", "RUSTROVER", "DATAGRIP", "JETBRAINS", "VIM", "NEOVIM",
    "ANDROID STUDIO", "ECLIPSE", "DEV-C++", "CODE -", "TRAE",
)
BROWSER_CLASSES = ("Chrome_WidgetWin", "MozillaWindowClass", "OperaWindowClass",
                   "IEFrame", "360se6_Frame", "msedge " )


def looks_like_code(t):
    marks = ("def ", "import ", "class ", "=>", "function", "{", "}", "</",
             "<div", "#include", "console.", "print(", "SELECT ", "npm ",
             "git ", "->", "::", " = ", "&&", ";")
    if any(m in t for m in marks):
        return True
    return t.count("\n") >= 4


def overlap_area(a, b):
    w = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    h = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    return w * h


def _compare_version(a, b):
    """比较两个版本号字符串，返回 >0/0/<0。"""
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


class HotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, cb):
        super().__init__()
        self.cb = cb

    def nativeEventFilter(self, eventType, message):
        if eventType == b"windows_generic_MSG":
            try:
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == winapi.WM_HOTKEY and int(msg.wParam) == HOTKEY_ID:
                    self.cb()
                    return True, 0
            except Exception:
                pass
        return False, 0


class PetApp(QObject):
    def __init__(self, qapp):
        super().__init__()
        self.qapp = qapp
        self.cfg = cfgmod.load_config()
        # v2.3.0 双模式开关迁移（旧 pet_mode → classic_enabled/bongo_enabled）
        if "classic_enabled" not in self.cfg and "bongo_enabled" not in self.cfg:
            self.cfg["bongo_enabled"] = (self.cfg.get("pet_mode", "classic") == "bongo")
            self.cfg["classic_enabled"] = not self.cfg["bongo_enabled"]
        self.cfg["pet_count"] = 1   # 小狗数量每次启动重置为 1（不持久生效）
        self.assets = AssetIndex()
        # 若配置里的自定义角色已被删除/文件夹不存在，启动时回退默认小白
        ch = self.cfg.get("character", "xiaobai")
        if ch not in ("xiaobai", "xiaojimao") and not self.assets.has_character(ch):
            self.cfg["character"] = "xiaobai"
        self.sound = SoundManager(enabled=self.cfg.get("sound_on", True))
        self.pet = PetWidget(self.assets, self.sound, self.cfg)
        self.pet.open_chat.connect(self.open_chat)
        # 鼠标跟随宠（鼠标皮肤）：置顶+穿透的小狗贴着光标走
        self.cursor_pet = CursorPet(self.assets, self.cfg)
        # v2.3.0 Bongo 模式：小狗趴在键盘上跟随打字（开关切换，与原桌宠互不影响）
        self.bongo = BongoPet(self.cfg)
        self.extra_pets = []   # 陪玩狗（第 2~15 只；简化：不接全屏层/日记/AI）

        # 运行时状态
        self._fs = False
        self._sleep_level = 0          # 0 清醒 / 1 睡 / 2 深睡
        self._high_cpu_streak = 0
        self._low_cpu_streak = 0
        self._comp_mode = None         # code / browser / None

        self._next_roam_at = time.time() + random.uniform(5, 10)
        self._last_chime_hour = time.localtime().tm_hour
        self._overlap_streak = 0
        self._last_clip_hash = ""
        self._next_avoid_at = time.time()   # 智能避让搬迁冷却时刻

        # 掉落物（小狗走过时偶尔掉下的小宝贝）
        self._drop_active = None          # 当前桌面上的 DroppedItem
        self._drop_fetch_item = None      # 正被叼走的物品
        self._drop_score = 0.0            # 掉落势能：走动/互动累积
        self._drop_threshold = random.randint(140, 220)
        self._drop_gate_at = time.time() + 300   # 运行 5 分钟后才允许掉落
        self._drop_cooldown_until = 0.0

        self.ai_history = []
        self.ai_worker = None
        self.chat = None
        self.chat_db = chatdb.ChatDB(
            os.path.join(cfgmod.data_dir(), "chat_history.db"))
        self._chat_new = []  # 本次运行新增的 (role, text, ts)，退出时写盘
        self.status_dlg = None
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(3000)
        self._save_timer.timeout.connect(self.save)

        self.pet.locked = False
        self._build_tray()
        self._build_timers()
        self._build_clipboard()

        # v1.3.0 新功能模块
        self.paw_canvas = PawCanvas(self.cfg.get("paw_fade_sec", 6))
        self.step_counter = StepCounter(self.cfg)
        self.pet.set_paw_hooks(self.paw_canvas, self.step_counter)
        self.prank = Prank(self.cfg, self.paw_canvas)
        self.shot_bubble = ShotBubble()
        # v2.2.0 桌面特效引擎（与动态壁纸视觉联动）
        self.fx_layer = EffectLayer()
        self.fx = EffectEngine(self.cfg, self.fx_layer)
        self.fx.attach_pet(self.pet)
        self.pet.set_fx(self.fx)
        # v2.2.1 鼠标光源：独立桌面层（壁纸之上、窗口之下），受联动门控
        self.cursor_light = CursorLightLayer()
        self.observe = ObserveManager(self.cfg, self._pet_say)
        self.music = MusicDetector(self.cfg, self._pet_say, self._on_music_state)
        self.night = NightCompanion(self.cfg, self._pet_say, self._pet_go_sleep)
        self._sync_feature_switches()

        # 插件系统：本体只带加载框架，插件为可单独下载的增量包。
        # hooks 把桌宠能力安全地暴露给插件，切换/返回逻辑集中在本体。
        self._game_mode = False
        self.plugins = PluginManager(self.cfg, hooks={
            "say": self._pet_say,
            "play_sound": lambda name: self.sound.play(name),
            "request_exit": self._on_plugin_exit,
        })
        self.pet.set_right_menu_provider(self._pet_plugin_menu)

        # v1.3.2 小狗日记 + 里程碑《第一次》埋点
        self.diary_db = diary_mod.get_db()
        self.pet.set_diary_hooks(self._record_diary)
        self.pet.set_pet_opacity(self.cfg.get("pet_opacity", 1.0))
        # 成就徽章：记下当前使用中的“服饰”（形象）
        self.diary_db.record_outfit(self.cfg.get("character", "xiaobai"))

        self.pet.place_initial()
        # place_initial 可能重新 show 桌宠，bongo 模式需在其后再压制一次
        self._apply_modes()
        self._apply_lock_corner(immediate=True)
        self._register_hotkey()

        # v2.2.0 双软件联动感知：检测 PawLiveWall 在线状态，门控特效 + 高级提醒
        self._unlink_timer = QTimer(self)
        self._unlink_timer.setSingleShot(True)
        self._unlink_timer.timeout.connect(self._finish_unlink)
        self.link = LinkMonitor(PAWLIVEWALL_MUTEX)
        self.link.linked.connect(self._on_link_up)
        self.link.unlinked.connect(self._on_link_down)
        self.fx.set_link_active(False)
        self.link.start()

        QTimer.singleShot(900, self._greet)
        # QMovie 用这个全局 QPixmapCache 存放 GIF 每一帧。
        # setCacheLimit 单位是 KB（不是字节）！32*1024*1024 KB = 32GB 是 bug，
        # 实测会导致进程吃掉十几 GB 内存。降到 32MB（32*1024 KB）：
        # 桌宠同时播放的动画帧远小于此，既能保证 GIF 不掉帧，又能省下常驻内存。
        QPixmapCache.setCacheLimit(32 * 1024)
        # 启动稳定后回收一次；之后单定时器每 1 分钟回调，每 3 分钟深回收、其余轻回收
        QTimer.singleShot(6000, self._release_memory)
        self._trim_counter = 0
        self._trim_timer = QTimer(self)
        self._trim_timer.setInterval(60000)  # 1 分钟
        self._trim_timer.timeout.connect(self._on_trim_tick)
        self._trim_timer.start()

        self._net = None
        self._update_reply = None
        self._latest_version = None
        self._update_urls = []
        self._update_idx = 0
        QTimer.singleShot(10000, self._auto_check_update)

    def _on_trim_tick(self):
        self._trim_counter += 1
        self._release_memory(deep=(self._trim_counter % 2 == 0))  # 每 2 分钟深回收

    def _release_memory(self, deep=False):
        """空闲时回收 Python 循环垃圾，并把空闲内存页归还系统。

        交互（抱起/转圈/追球等）或睡眠中不做深回收和 trim，避免动画掉帧；
        但轻 GC（年轻代）始终执行，防止频繁交互产生的临时 pixmap/字符串累积。
        深回收（完整 GC）每 3 分钟一次。

        注意：不再 QPixmapCache.clear()——QMovie 的 GIF 帧就存在 QPixmapCache
        里，定期清空会把正在播放的动画帧淘汰掉，重新引入掉帧/闪烁。
        """
        busy = not self.pet.is_free() or self.pet.sleeping
        try:
            import gc
            if busy and not deep:
                gc.collect(1)  # 忙时也做轻 GC，只跳过 trim 和深回收
                return
            gc.collect() if deep else gc.collect(1)
        except Exception:
            pass
        if not busy:
            winapi.trim_working_set()

    def _phys_to_log_rect(self, rect):
        """Win32 API 返回物理像素，Qt 使用逻辑像素，按所在屏幕的 DPR 换算。"""
        if not rect:
            return rect
        cx, cy = (rect[0] + rect[2]) / 2.0, (rect[1] + rect[3]) / 2.0
        dpr = 1.0
        for scr in self.qapp.screens():
            g = scr.geometry()
            r = scr.devicePixelRatio()
            if (g.left() * r <= cx <= g.right() * r
                    and g.top() * r <= cy <= g.bottom() * r):
                dpr = r
                break
        k = 1.0 / dpr
        return tuple(int(round(v * k)) for v in rect[:4])

    # ---------------- 托盘 ----------------
    def _build_tray(self):
        icon_path = cfgmod.app_icon_path()
        icon = QIcon(icon_path) if icon_path else self.qapp.style().standardIcon(
            self.qapp.style().StandardPixmap.SP_ComputerIcon)
        self.tray = QSystemTrayIcon(icon)
        self.tray.setToolTip("LineDogPet · 左键显示/隐藏，右键菜单")
        menu = QMenu()

        self.act_visibility = QAction("隐藏", self)
        self.act_visibility.triggered.connect(self.toggle_pet)
        act_settings = QAction("设置…", self)
        act_settings.triggered.connect(self.open_settings)
        act_status = QAction("状态面板…", self)
        act_status.triggered.connect(self.open_status)
        menu.addAction(self.act_visibility)
        menu.addAction(act_settings)
        menu.addAction(act_status)
        menu.addSeparator()

        # 喂食 / 玩耍 / 追小球 / 躲猫猫：双列按钮排版
        grid_host = QWidget()
        grid_host.setObjectName("trayGrid")
        # 用 #id 选择器只清空容器自身背景；若用裸 QWidget 会级联命中
        # 子按钮，把主题里的背景色覆盖成透明，深色模式下近黑文字
        # 落在近黑菜单底上完全看不清。
        grid_host.setStyleSheet("QWidget#trayGrid{background:transparent;}")
        glay = QGridLayout(grid_host)
        glay.setContentsMargins(6, 4, 6, 4)
        glay.setSpacing(4)
        for i, (label, slot) in enumerate((("喂食", self.feed),
                                           ("玩耍", self.play),
                                           ("追小球", self.start_chase),
                                           ("躲猫猫", self.start_hide))):
            b = QPushButton(label)
            b.setMinimumHeight(28)
            b.clicked.connect(lambda _=False, cb=slot: (cb(), menu.close()))
            glay.addWidget(b, i // 2, i % 2)
        grid_action = QWidgetAction(menu)
        grid_action.setDefaultWidget(grid_host)
        menu.addAction(grid_action)
        menu.addSeparator()

        act_steps = QAction("小狗步数…", self)
        act_steps.triggered.connect(self._show_steps)
        menu.addAction(act_steps)
        act_diary = QAction("📖小狗日记…", self)
        act_diary.triggered.connect(self.open_diary)
        menu.addAction(act_diary)
        self.act_milestone = QAction("🌟《里程碑》…", self)
        self.act_milestone.triggered.connect(self.open_milestone)
        menu.addAction(self.act_milestone)
        self.act_milestone.setVisible(diary_mod.get_db().milestone_unlocked())
        menu.addSeparator()
        # 插件 / 小游戏：子菜单展开时动态填充（支持运行期新装插件）
        self._plugin_menu = QMenu("🎮 小游戏 / 插件", menu)
        self._plugin_menu.aboutToShow.connect(self._populate_tray_plugin_menu)
        self._plugin_menu_action = menu.addMenu(self._plugin_menu)
        # 主菜单弹出前按「右键便捷显示」开关决定子菜单是否可见
        menu.aboutToShow.connect(self._sync_plugin_menu_visibility)
        menu.addSeparator()
        act_quit = QAction("退出", self)
        act_quit.triggered.connect(self.quit_app)
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self._apply_menu_theme(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.messageClicked.connect(self._on_update_message_clicked)
        self.tray.show()

    def _apply_menu_theme(self, menu):
        """给托盘右键菜单应用主题样式。"""
        menu.setStyleSheet(generate_style(
            self.cfg.get("theme_color", "#ff96bb"),
            self.cfg.get("theme_radius", 8),
            1.0,
            dark=bool(self.cfg.get("dark_mode", False))))

    def _tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.toggle_pet()

    # ---------------- 定时器 ----------------
    def _build_timers(self):
        self.t_1s = QTimer(self)
        self.t_1s.setInterval(1000)
        self.t_1s.timeout.connect(self._tick_1s)
        self.t_1s.start()

        self.t_2s = QTimer(self)
        self.t_2s.setInterval(2000)
        self.t_2s.timeout.connect(self._tick_sense)
        self.t_2s.start()

        self.t_avoid = QTimer(self)
        self.t_avoid.setInterval(3000)
        self.t_avoid.timeout.connect(self._tick_avoid)
        self.t_avoid.start()

        self._tick_count = 0

    def _build_clipboard(self):
        self.clipboard = self.qapp.clipboard()
        self.clipboard.dataChanged.connect(self._on_clipboard)

    # ---------------- 显示/隐藏 ----------------
    def toggle_pet(self):
        self.set_pet_visible(not self.pet.isVisible())

    def set_pet_visible(self, vis):
        self.pet.setVisible(vis)
        for p in self.extra_pets:
            p.setVisible(vis)
        if vis:
            self.pet.raise_()
            for p in self.extra_pets:
                p.raise_()
        self.act_visibility.setText("隐藏" if vis else "显示")

    def _greet(self):
        if not self.pet.isVisible():
            return
        self.pet.show_bubble("汪！今天也要加油呀~", 3200)

    # ---------------- v1.3.0 功能辅助 ----------------
    def _pet_say(self, text):
        if self.pet.isVisible() and text:
            self.pet.show_bubble(text, 3500)

    def _record_diary(self, kind, mood=None, energy=None):
        """日记/里程碑统一埋点：同类事件当日只记一次，首次事件只存一次。"""
        diary_mod.record(kind, mood, energy)
        # 互动加分掉落势能（互动越多越容易捡到小宝贝）
        if kind in ("feed", "play", "chase", "hide", "chin",
                    "pet", "call", "talk", "spin", "chat"):
            self._drop_score = min(999.0, self._drop_score + 22.0)
        # 达到里程碑数量阈值时弹提示解锁福利
        for name in self.diary_db.check_rewards():
            self._pet_say(f"集齐了新的「第一次」！解锁称号「{name}」✨")
            if self.tray is not None:
                self.tray.showMessage(
                    cfgmod.APP_NAME,
                    f"里程碑达成：解锁称号「{name}」！",
                    QSystemTrayIcon.MessageIcon.Information, 6000)

    def _pet_go_sleep(self):
        if self.pet.is_free():
            self.pet.go_sleep(deep=False)

    def _on_music_state(self, song):
        if self.fx is not None:
            self.fx.feed_music()
        if self.pet.state in (IDLE, STARE, GROOM, RELAX):
            self.pet.sleeping = False
            self.pet.set_state(HAPPY, 2000)

    # ---------------- 插件 / 游戏模式 ----------------
    def _pet_plugin_menu(self):
        """右键小狗时调用：有可用插件才返回菜单，否则返回 None（保持原挠下巴）。"""
        if not self.cfg.get("plugins_pet_menu", True):
            return None      # 设置→插件→更多 里可关闭右键便捷入口
        try:
            entries = self.plugins.menu_entries()
        except Exception:
            entries = []
        if not entries:
            return None
        menu = QMenu()
        for pid, label in entries:
            act = QAction(label, menu)
            act.triggered.connect(lambda _=False, p=pid: self._launch_plugin(p))
            menu.addAction(act)
        menu.addSeparator()
        chin = QAction("挠下巴", menu)
        chin.triggered.connect(self.pet.do_chin)
        menu.addAction(chin)
        self._apply_menu_theme(menu)
        return menu

    def _launch_plugin(self, pid):
        """从桌宠切换到插件（游戏）模式：隐藏桌宠相关层，再打开插件窗口。"""
        if self.plugins.is_active(pid):
            return
        self._game_mode = True
        self.set_pet_visible(False)
        try:
            self.cursor_pet.set_enabled(False)
        except Exception:
            pass
        try:
            self.fx.set_enabled(False)
        except Exception:
            pass
        if not self.plugins.launch(pid):
            self._exit_game_visuals()
            if self.tray is not None:
                self.tray.showMessage(cfgmod.APP_NAME, "这个插件没能打开…",
                                      QSystemTrayIcon.MessageIcon.Warning, 4000)

    def _on_plugin_exit(self, pid):
        """插件请求退出：关闭其窗口并恢复桌宠显示。"""
        self.plugins.close_plugin(pid)
        self._exit_game_visuals()

    def _exit_game_visuals(self):
        self._game_mode = False
        try:
            self._apply_modes()          # 按配置恢复桌宠/Bongo 显隐
        except Exception:
            pass
        self.set_pet_visible(bool(self.cfg.get("classic_enabled", True)))
        try:
            self.cursor_pet.set_enabled(bool(self.cfg.get("cursor_follow", False)))
        except Exception:
            pass
        try:
            self.fx.set_enabled(bool(self.cfg.get("fx_enabled", True)))
            self.fx.apply_config()
        except Exception:
            pass
        self._sync_cursor_light()
        self.pet.raise_()

    def _sync_plugin_menu_visibility(self):
        """托盘主菜单弹出前：按「右键便捷显示」开关显示/隐藏 小游戏/插件 子菜单。"""
        act = getattr(self, "_plugin_menu_action", None)
        if act is not None:
            act.setVisible(bool(self.cfg.get("plugins_pet_menu", True)))

    def _populate_tray_plugin_menu(self):
        m = getattr(self, "_plugin_menu", None)
        if m is None:
            return
        m.clear()
        try:
            entries = self.plugins.menu_entries()
        except Exception:
            entries = []
        if not entries:
            a = QAction("（还没有已安装的插件）", m)
            a.setEnabled(False)
            m.addAction(a)
        for pid, label in entries:
            act = QAction(label, m)
            act.triggered.connect(lambda _=False, p=pid: self._launch_plugin(p))
            m.addAction(act)
        m.addSeparator()
        mgr = QAction("管理 / 安装插件…", m)
        mgr.triggered.connect(self.open_settings_plugins)
        m.addAction(mgr)
        self._apply_menu_theme(m)

    def open_settings_plugins(self):
        """打开设置并直接切到“插件”页。"""
        self.open_settings(focus_tab="plugin")

    # ---------------- v2.2.0 双软件联动 ----------------
    def _on_link_up(self):
        """检测到 PawLiveWall 在线：激活特效 + 高级提醒。"""
        self._unlink_timer.stop()
        self.fx.set_link_active(True)
        self.fx.play_link_burst()
        self._sync_cursor_light()
        if self.pet.isVisible():
            self.pet.show_bubble("🔗 与 PawLiveWall 动态壁纸联动已开启 ✨", 4200)
        if self.tray is not None:
            self.tray.showMessage(
                "🔗 联动已开启",
                "线条小狗 × PawLiveWall 动态壁纸\n连接成功，桌面特效已激活 ✨",
                QSystemTrayIcon.MessageIcon.Information, 6000)

    def _on_link_down(self):
        """PawLiveWall 退出：播放断开动画，延迟关闭特效（让动画播完）。"""
        self.fx.play_unlink_fade()
        if self.pet.isVisible():
            self.pet.show_bubble("联动已断开，桌面特效已关闭", 3500)
        if self.tray is not None:
            self.tray.showMessage(
                "🔗 联动已断开",
                "PawLiveWall 动态壁纸已退出，桌面特效已关闭",
                QSystemTrayIcon.MessageIcon.Information, 5000)
        self._unlink_timer.start(1300)

    def _finish_unlink(self):
        # 1.3 秒后若仍未联动，才真正关闭（避免快速重连时误关）
        if not self.link.online():
            self.fx.set_link_active(False)
            self._sync_cursor_light()

    def _sync_cursor_light(self):
        """鼠标光源只在联动且非挂机时显示（桌面层，被窗口遮挡时不可见）。"""
        link = getattr(self, "link", None)
        idle = winapi.get_idle_seconds()
        on = (bool(self.cfg.get("fx_enabled", True))
              and bool(self.cfg.get("fx_cursor_light"))
              and idle < 180
              and link is not None and link.online())
        try:
            self.cursor_light.set_enabled(on)
        except Exception:
            pass

    def _sync_feature_switches(self):
        self.paw_canvas.set_fade(self.cfg.get("paw_fade_sec", 6))
        self.prank.set_enabled(bool(self.cfg.get("prank_enabled")))
        self.observe.set_enabled(bool(self.cfg.get("observe_enabled")))
        self.music.set_enabled(bool(self.cfg.get("music_enabled")))
        self.night.set_enabled(bool(self.cfg.get("night_enabled")))
        # 关闭鼠标交互时让宠物窗口鼠标穿透（点击落到下层应用）
        self.pet.set_mouse_through(not bool(self.cfg.get("mouse_interaction", True)))
        # 鼠标跟随宠：开启时贴着光标，关闭时隐藏
        self.cursor_pet.set_enabled(bool(self.cfg.get("cursor_follow", False)))
        # 桌面特效：总开关 + 各特效/帧率
        self.fx.set_enabled(bool(self.cfg.get("fx_enabled", True)))
        self.fx.apply_config()
        self._sync_cursor_light()
        self._apply_modes()

    def _apply_modes(self):
        """桌宠模式 / Bongo 模式各自独立开关，可共存（共存会加大 CPU/内存）。"""
        classic_on = bool(self.cfg.get("classic_enabled", True))
        bongo_on = bool(self.cfg.get("bongo_enabled", False))
        try:
            self.bongo.set_mirror(bool(self.cfg.get("bongo_mirror", True)))
            self.bongo.set_size(int(self.cfg.get("bongo_size", 440)))
            self.bongo.set_opacity(float(self.cfg.get("bongo_opacity", 1.0)))
            self.bongo.set_click_through(bool(self.cfg.get("bongo_click_through", False)))
            self.bongo.set_enabled(bongo_on)
        except Exception:
            pass
        # 桌宠模式：主狗 + 陪玩狗一起显隐
        self._set_classic_visible(classic_on)
        if classic_on:
            self.cursor_pet.set_enabled(bool(self.cfg.get("cursor_follow", False)))
            self.fx.set_enabled(bool(self.cfg.get("fx_enabled", True)))
            self._sync_cursor_light()
            self.prank.set_enabled(bool(self.cfg.get("prank_enabled", False)))
        else:
            # 关桌宠模式：classic 全屏层全部停 + hide，释放 backing store 省内存
            self.cursor_pet.set_enabled(False)
            self.fx.set_enabled(False)
            self.cursor_light.set_enabled(False)
            self.prank.set_enabled(False)

    def _set_classic_visible(self, on):
        """主狗 + 陪玩狗统一显隐。"""
        if on:
            self.pet.show()
            for p in self.extra_pets:
                p.show()
        else:
            self.pet.hide()
            for p in self.extra_pets:
                p.hide()

    def _rebuild_extra_pets(self, n):
        """按数量调整陪玩狗（总数 n = 1 主狗 + n-1 陪玩狗），上限 15。"""
        n = max(1, min(15, int(n)))
        want = n - 1
        while len(self.extra_pets) > want:
            p = self.extra_pets.pop()
            p.hide()
            p.deleteLater()
        while len(self.extra_pets) < want:
            self.extra_pets.append(self._make_extra_pet())
        self._layout_extra_pets()
        self._set_classic_visible(bool(self.cfg.get("classic_enabled", True)))
        self.cfg["pet_count"] = n
        self._pet_count = n
        try:
            n_pets = len(self.extra_pets) + 1
            QPixmapCache.setCacheLimit((32 + max(0, n_pets - 1) * 3) * 1024)
        except Exception:
            pass

    def _make_extra_pet(self):
        """1 只陪玩狗：共享素材/cfg（功能开关自动同步），不接全屏脚印/特效/日记，节拍降频省 CPU。"""
        p = PetWidget(self.assets, self.sound, self.cfg)
        try:
            p.tick.setInterval(80)     # 移动节拍 50→80ms，降 CPU
        except Exception:
            pass
        try:
            p._paw_tick.stop()         # 陪玩狗不画桌面脚印
        except Exception:
            pass
        try:
            p.set_pet_opacity(self.cfg.get("pet_opacity", 1.0))
            p.set_mouse_through(not bool(self.cfg.get("mouse_interaction", True)))
        except Exception:
            pass
        p.show()
        return p

    def _layout_extra_pets(self):
        """把陪玩狗分散摆到底部区域，避免和主狗重叠。"""
        scr = self.qapp.primaryScreen()
        if scr is None:
            return
        g = scr.availableGeometry()
        w, h = self.pet.width(), self.pet.height()
        step_x = max(w + 30, 40)
        step_y = max(h + 20, 40)
        for i, p in enumerate(self.extra_pets):
            col = (i + 1) % 4
            row = (i + 1) // 4
            x = g.left() + 80 + col * step_x
            y = g.bottom() - h - 40 - row * step_y
            p.move(max(g.left(), min(x, g.right() - w)),
                   max(g.top(), min(y, g.bottom() - h)))
            p.show()

    def _on_classic_enabled_changed(self, on):
        self.cfg["classic_enabled"] = bool(on)
        self._apply_modes()

    def _on_bongo_enabled_changed(self, on):
        self.cfg["bongo_enabled"] = bool(on)
        self._apply_modes()

    def _on_pet_count_changed(self, n):
        self._rebuild_extra_pets(n)

    # ---------------- 1s：状态/时间/存档 ----------------
    def _tick_1s(self):
        self._tick_count += 1
        # 体力/心情缓慢变化
        # 体力每秒减 0.0007，约 40 小时从 100 衰减到 0（原 0.0028 太快，10 小时见底）
        self.cfg["energy"] = max(0.0, self.cfg["energy"] - 0.0007)
        idle = winapi.get_idle_seconds()
        if idle > 600:
            self.cfg["mood"] = max(0.0, self.cfg["mood"] - 0.0008)

        # 昼夜
        if self.cfg.get("sense_enabled") and self.cfg.get("sense_time"):
            h = time.localtime().tm_hour
            self.pet.night = (h >= 22 or h < 6)
            # 整点轻叫
            if self.cfg.get("hourly_chime") and self.cfg.get("sound_on"):
                t = time.localtime()
                if t.tm_min == 0 and h != self._last_chime_hour and self.pet.isVisible():
                    self._last_chime_hour = h
                    self.sound.play("chime")
                    self.pet.show_bubble(f"汪~ {h}点啦", 2200)
                elif t.tm_min != 0:
                    self._last_chime_hour = h
        else:
            self.pet.night = False

        if self._tick_count % 30 == 0:
            self.save()
        self._maybe_roam()
        self._maybe_drop_tick()

    # ---------------- 掉落物 ----------------
    def _maybe_drop_tick(self):
        """每秒累积势能并判定是否掉落一件小宝贝。

        - 走动时缓慢累积、互动时大幅累积（_record_diary 里加）；
        - 程序运行满 5 分钟、且冷却结束后才有机会掉；
        - 势能越高掉率越高，掉后重置势能并进入冷却。
        """
        now = time.time()
        # 小狗正在自主走动时累积势能
        if self.pet.state in (WALK, FLEE, CHASE):
            self._drop_score = min(999.0, self._drop_score + 1.6)
        if self._drop_active is not None:
            return
        if now < self._drop_gate_at or now < self._drop_cooldown_until:
            return
        if self._drop_score < self._drop_threshold:
            return
        # 势能越高越容易掉，但封顶 55%（收藏品较稀有，需要耐心）
        p = min(0.55, 0.08 + self._drop_score / 1000.0)
        if random.random() > p:
            # 这次没掉，势能保留继续累积，下次再判
            return
        if not self.pet.is_free() or self.pet.sleeping or self._fs:
            return
        self._spawn_drop()

    def _spawn_drop(self):
        """在小狗附近掉落一件随机小宝贝。"""
        from .drops import DROP_LIBRARY
        name, emoji = random_item()
        center = self.pet.frameGeometry().center()
        screen = (self.qapp.screenAt(center) or self.qapp.primaryScreen())
        geo = screen.availableGeometry()
        dx = random.choice([-1, 1]) * random.randint(120, 300)
        dy = random.randint(-60, 160)
        tx = max(geo.left() + 30, min(geo.right() - 30, center.x() + dx))
        ty = max(geo.top() + 30, min(geo.bottom() - 30, center.y() + dy))
        item = DroppedItem(name, emoji, tx, ty)
        item.picked.connect(self._on_drop_picked)
        item.expired.connect(self._on_drop_expired)
        self._drop_active = item
        # 掉后重置势能、随机新阈值、进入冷却
        self._drop_score = 0.0
        self._drop_threshold = random.randint(140, 220)
        self._drop_cooldown_until = time.time() + random.uniform(200, 320)

    def _on_drop_picked(self, item):
        """用户点击掉落物：立即消失并记入收藏册。"""
        if self._drop_active is item:
            self._drop_active = None
        name, emoji = item.item
        try:
            item.collect()
        except Exception:
            pass
        try:
            today_n, item_n = self.diary_db.record_found(name, emoji)
        except Exception:
            item_n = 0
        self._pet_say(f"捡到 {emoji} {name}！收藏册第 {item_n} 件~")
        try:
            self.sound.play("chime")
        except Exception:
            pass

    def _on_drop_expired(self, item):
        """掉落物超时没人捡：清理引用。"""
        if self._drop_active is item:
            self._drop_active = None

    def _on_fetch_done(self):
        """保留占位：掉落物已改为点击即收，不再需要小狗走过去叼。"""
        self._drop_fetch_item = None

    # ---------------- 2s：系统感知主逻辑 ----------------
    def _tick_sense(self):
        sense_on = self.cfg.get("sense_enabled", True)
        idle = winapi.get_idle_seconds()

        # 用户动了键鼠且宠物在睡：立刻醒
        if self._sleep_level > 0 and idle < 15:
            self._sleep_level = 0
            self.pet.wake()

        fg = winapi.foreground_info() if winapi.IS_WINDOWS else None
        if fg:
            fg = dict(fg)
            fg["rect"] = self._phys_to_log_rect(fg.get("rect"))
            fg["workarea"] = self._phys_to_log_rect(fg.get("workarea"))

        # 1) 全屏 -> 缩小躲角落（最高优先级）
        fs_now = bool(sense_on and self.cfg.get("fullscreen_shrink")
                      and fg and fg.get("fullscreen"))
        if fs_now and not self._fs:
            self._fs = True
            self.pet.corner_hide(True, fg.get("workarea"))
        elif not fs_now and self._fs:
            self._fs = False
            self.pet.corner_hide(False)
        # 全屏或长时间无操作（含锁屏挂机）时暂停桌面特效：
        # 避免透明层在锁屏挂机时持续重绘导致内存持续增长
        self.fx.set_fullscreen(fs_now or idle > 180)
        self._sync_cursor_light()

        # 2) 闲置睡眠
        if sense_on and self.cfg.get("sense_idle") and not self._fs:
            threshold = 150 if self.pet.night else 180
            if idle >= 600:
                level = 2
            elif idle >= threshold:
                level = 1
            else:
                level = 0
            if level > self._sleep_level:
                if level == 2 and self.pet.sleeping:
                    self.pet.set_deep(True)
                else:
                    self.pet.go_sleep(deep=(level == 2))
                self._sleep_level = level
        else:
            self._sleep_level = 0

        # 3) CPU 负载（只自动降帧省CPU，不再紧张踱步）
        if sense_on and self.cfg.get("sense_cpu"):
            cpu = winapi.cpu_percent()
            busy = cpu is not None and cpu > 72
            calm = cpu is None or cpu < 40
            if busy:
                self._high_cpu_streak += 1
                self._low_cpu_streak = 0
            elif calm:
                self._low_cpu_streak += 1
                self._high_cpu_streak = 0
            if self._high_cpu_streak >= 2 and self._sleep_level == 0:
                self.pet.set_cpu_factor(0.55)
            elif self._low_cpu_streak >= 2:
                self.pet.set_cpu_factor(1.0)
        else:
            self.pet.set_cpu_factor(1.0)

        # 4) 前台窗口：写代码安静陪伴（浏览器偷看已移除）
        if sense_on and self.cfg.get("sense_window") and fg and not self._fs \
                and self._sleep_level == 0:
            kind = self._classify(fg)
            if kind == "code":
                if self._comp_mode != "code":
                    self._comp_mode = "code"
                    self.pet.set_companion("code")
            elif kind == "browser":
                # 只保留上下文标记（供 AI 感知），不再探头偷看
                if self._comp_mode != "browser":
                    self._comp_mode = "browser"
                    self.pet.set_companion(None)
            else:
                if self._comp_mode is not None:
                    self._comp_mode = None
                    self.pet.set_companion(None)
        elif self._comp_mode is not None:
            self._comp_mode = None
            self.pet.set_companion(None)

    def _classify(self, fg):
        title = (fg.get("title") or "").upper()
        cls = (fg.get("cls") or "")
        if any(k in title for k in CODE_KEYWORDS):
            return "code"
        if any(k in cls for k in ("Chrome_WidgetWin", "MozillaWindowClass",
                                  "OperaWindowClass", "IEFrame")):
            # Electron 代码编辑器兜底
            if "VS CODE" in title or "CODE - " in title:
                return "code"
            return "browser"
        return "other"


    # ---------------- 智能避让（3s 一次，主狗+陪玩狗都避让） ----------------
    def _tick_avoid(self):
        if self._fs or self.cfg.get("lock_corner"):
            return
        if not (self.cfg.get("sense_enabled") and self.cfg.get("avoid_windows")):
            return
        now = time.time()
        rects = [self._phys_to_log_rect(r[:4]) for r in winapi.enum_window_rects()]
        if not rects:
            return
        for pet in [self.pet] + self.extra_pets:
            self._avoid_one(pet, rects, now)

    def _avoid_one(self, pet, rects, now):
        """单只狗的避让：被窗口遮挡时搬到空白处。每只狗独立冷却/连续重叠计数。"""
        if now < getattr(pet, '_next_avoid_at', 0):
            return
        cur = QCursor.pos()
        pet_center = pet.frameGeometry().center()
        if math.hypot(cur.x() - pet_center.x(), cur.y() - pet_center.y()) < 160:
            pet._overlap_streak = 0
            return
        if self.cfg.get("follow_mode", "sticky") != "off" \
                and now - pet.last_follow_at < 1.5:
            pet._overlap_streak = 0
            return
        if not pet.is_free() or pet.sleeping:
            return
        pl, pt, pr, pb = pet.x(), pet.y(), pet.x() + pet.width(), pet.y() + pet.height()
        cur_box = (pl, pt, pr, pb)
        cur_overlap = sum(overlap_area(cur_box, r) for r in rects)
        pet_area = max(1, pet.width() * pet.height())
        if cur_overlap < pet_area * 0.25:
            pet._overlap_streak = 0
            return
        pet._overlap_streak = getattr(pet, '_overlap_streak', 0) + 1
        if pet._overlap_streak < 2 and cur_overlap < pet_area * 0.5:
            return
        pet._overlap_streak = 0
        target = self._find_free_spot(rects, cur_box, cur_overlap, pet)
        if target:
            pet.relocate(target[0], target[1], speed=20)
            pet._next_avoid_at = now + 4.0

    def _blank_candidates(self, rects, pet):
        """生成屏幕候选中心点（4×4 网格 + 四角四边中点 + 25 随机点），
        供避让与随机闲逛共用。只产出候选点，不含评分。"""
        screen = (self.qapp.screenAt(pet.frameGeometry().center())
                  or self.qapp.primaryScreen())
        geo = screen.availableGeometry()
        w, h = pet.width(), pet.height()
        cw2, ch2 = 20 + w // 2, 20 + h // 2
        candidates = []
        # 1) 4×4 网格均匀扫描（16 个点），保证整块屏幕不会有空白被漏掉
        gx0, gx1 = geo.left() + w // 2 + 6, geo.right() - w // 2 - 6
        gy0, gy1 = geo.top() + h // 2 + 6, geo.bottom() - h // 2 - 6
        if gx1 > gx0 and gy1 > gy0:
            for i in range(4):
                for j in range(4):
                    cx = gx0 + (gx1 - gx0) * i // 3
                    cy = gy0 + (gy1 - gy0) * j // 3
                    candidates.append((cx, cy))
        # 2) 四角 + 四边中点（8 点）
        candidates += [
            (geo.left() + cw2, geo.bottom() - ch2),
            (geo.right() - cw2, geo.bottom() - ch2),
            (geo.left() + cw2, geo.top() + ch2),
            (geo.right() - cw2, geo.top() + ch2),
            (geo.left() + cw2, (geo.top() + geo.bottom()) // 2),
            (geo.right() - cw2, (geo.top() + geo.bottom()) // 2),
            ((geo.left() + geo.right()) // 2, geo.top() + ch2),
            ((geo.left() + geo.right()) // 2, geo.bottom() - ch2),
        ]
        # 3) 25 个随机点，兜底覆盖边缘奇怪空白
        for _ in range(25):
            cx = random.randint(geo.left() + w // 2, geo.right() - w // 2)
            cy = random.randint(geo.top() + h // 2, geo.bottom() - h // 2)
            candidates.append((cx, cy))
        return candidates

    def _find_free_spot(self, rects, cur_box=None, cur_overlap=0, pet=None):
        if pet is None:
            pet = self.pet
        screen = (self.qapp.screenAt(pet.frameGeometry().center())
                  or self.qapp.primaryScreen())
        geo = screen.availableGeometry()
        w, h = pet.width(), pet.height()
        if cur_overlap <= 0:
            return None  # 当前本来就没被压，不用找地方
        m = 14
        padded = [(l - m, t - m, r + m, b + m) for l, t, r, b in rects]
        cw2, ch2 = 20 + w // 2, 20 + h // 2
        # 四角（全屏无空白时的最终避难位）
        corners = [
            (geo.left() + cw2, geo.bottom() - ch2),
            (geo.right() - cw2, geo.bottom() - ch2),
            (geo.left() + cw2, geo.top() + ch2),
            (geo.right() - cw2, geo.top() + ch2),
        ]
        candidates = self._blank_candidates(rects, pet)
        # 评分：优先选不重叠的；同样重叠程度选离当前位置最近的，避免乱跑
        best = None
        best_key = None
        best_box = None
        cur_center = pet.frameGeometry().center()
        for cx, cy in candidates:
            box = (cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2)
            ov = sum(overlap_area(box, rr) for rr in padded)
            dist = abs(cx - cur_center.x()) + abs(cy - cur_center.y())
            key = (ov, dist)
            if best_key is None or key < best_key:
                best_key = key
                best = (cx, cy)
                best_box = box
        pet_area = max(1, w * h)
        # 1) 存在完全空白且不是当前位置：直接去
        if best_key[0] <= 0:
            return best if best_key[1] > 8 else None
        actual_ov = sum(overlap_area(best_box, rr) for rr in rects)
        # 2) 普通情况：候选位置重叠<15% 且 比现状好（至少少压 15% 面积）就走
        if actual_ov <= pet_area * 0.15 and actual_ov < cur_overlap - pet_area * 0.15:
            return best
        # 3) 没找到完美位置但现状很差（>40%被压）：找一个"最不压"的地方
        if cur_overlap >= pet_area * 0.4:
            if actual_ov < cur_overlap - pet_area * 0.1:
                return best
            # 尝试四角中不重叠的最近角落（而非盲目去任意角落）
            for corner in sorted(
                    corners,
                    key=lambda c: abs(c[0] - cur_center.x())
                    + abs(c[1] - cur_center.y())):
                cbox = (corner[0] - w // 2, corner[1] - h // 2,
                        corner[0] + w // 2, corner[1] + h // 2)
                cov = sum(overlap_area(cbox, rr) for rr in padded)
                if cov <= 0:
                    return corner
            # 所有角落也被压：至少去最不压的角落（缩小后可能塞下）
            return best if actual_ov < cur_overlap else None
        return None

    # ---------------- 随机闲逛 ----------------
    def _maybe_roam(self):
        """空闲静置时随机走到桌面空白处，不打断手势/动画/避让。"""
        now = time.time()
        if now < self._next_roam_at:
            return
        self._next_roam_at = now + random.uniform(5, 10)
        if not self.cfg.get("roam_enabled", True):
            return
        if self._fs or self.cfg.get("lock_corner"):
            return
        # 精确限定空闲静置（严于 is_free，避免 WALK 移动途中重复触发）
        if self.pet.state != IDLE or not self.pet.is_free() or self.pet.locked:
            return
        rects = [self._phys_to_log_rect(r[:4]) for r in winapi.enum_window_rects()]
        spots = self._blank_candidates(rects, self.pet)
        w, h = self.pet.width(), self.pet.height()
        screen = (self.qapp.screenAt(self.pet.frameGeometry().center())
                  or self.qapp.primaryScreen())
        geo = screen.availableGeometry()
        blanks = []
        for cx, cy in spots:
            if cx - w // 2 < geo.left() or cx + w // 2 > geo.right():
                continue
            if cy - h // 2 < geo.top() or cy + h // 2 > geo.bottom():
                continue
            box = (cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2)
            if any(overlap_area(box, r) > 0 for r in rects):
                continue
            blanks.append((cx, cy))
        if not blanks:
            return
        cx, cy = random.choice(blanks)
        self.pet.relocate(cx, cy, speed=2.0)

    # ---------------- 剪贴板 ----------------
    def _on_clipboard(self):
        # v1.3.0 截图联动：剪贴板出现新图像时弹缩略气泡
        if self.cfg.get("shot_enabled"):
            try:
                pm = self.clipboard.pixmap()
                if pm and not pm.isNull() and pm.width() >= 200 and pm.height() >= 200:
                    g = self.pet.geometry()
                    self.shot_bubble.show_shot(pm, (g.x(), g.y(), g.width(), g.height()))
                    self.pet.show_bubble("截图收到啦~", 2200)
                    return
            except Exception:
                pass
        if not (self.cfg.get("sense_enabled") and self.cfg.get("sense_clipboard")):
            return
        if self._fs or not self.pet.isVisible():
            return
        try:
            text = self.clipboard.text()
        except Exception:
            return
        t = (text or "").strip()
        if not t or len(t) > 2000:
            return
        import hashlib  # 懒加载：不复制文本就不载入
        h = hashlib.md5(t[:2000].encode("utf-8", "ignore")).hexdigest()
        if h == self._last_clip_hash:
            return
        self._last_clip_hash = h
        if looks_like_code(t):
            msg = "你复制了代码哦~ 不懂可以中键点我问"
        else:
            first = t.splitlines()[0][:12]
            msg = "你复制了：" + first + ("…" if len(t) > 12 else "")
        self.pet.sleeping = False
        self.pet.set_state(PAPER, 3200)
        self.pet.show_bubble(msg, 4200)

    # ---------------- 喂养 / 游戏 ----------------
    def feed(self):
        self.cfg["energy"] = min(100.0, self.cfg["energy"] + 35)
        self.cfg["mood"] = min(100.0, self.cfg["mood"] + 8)
        self.pet.feed()
        self._save_timer.start()

    def play(self):
        self.cfg["mood"] = min(100.0, self.cfg["mood"] + 18)
        self.cfg["energy"] = max(0.0, self.cfg["energy"] - 12)
        self.pet.play_with()
        self._save_timer.start()

    def start_chase(self):
        self.cfg["mood"] = min(100.0, self.cfg["mood"] + 10)
        self.cfg["energy"] = max(0.0, self.cfg["energy"] - 6)
        self.pet.start_chase()
        self._save_timer.start()

    def start_hide(self):
        self.cfg["mood"] = min(100.0, self.cfg["mood"] + 10)
        self.pet.start_hide()
        self._save_timer.start()

    # ---------------- v1.3.0 步数 / 日记 ----------------
    def _show_steps(self):
        steps = self.step_counter.today()
        # 手动构造 QMessageBox 替代静态 information()，避免触发系统提示音；
        # 改用程序自合成的清脆风铃声作为弹窗音效。
        self.sound.play("chime")
        box = QMessageBox(self.tray.parent())
        box.setWindowTitle("今日步数")
        box.setStyleSheet(generate_style(
            self.cfg.get("theme_color", "#ff96bb"),
            self.cfg.get("theme_radius", 8), 1.0,
            dark=bool(self.cfg.get("dark_mode", False))))
        box.setIcon(QMessageBox.Icon.Information)
        if steps == 0:
            msg = "今天小狗还没怎么走动呢，多陪它玩玩吧~"
        elif steps < 200:
            msg = f"今天小狗走了 {steps} 步，散散步~ 🐾"
        elif steps < 1000:
            msg = f"今天小狗走了 {steps} 步，挺精神的！"
        else:
            msg = f"今天小狗走了 {steps} 步，活力满满！🎉"
        box.setText(msg)
        box.addButton("好的", QMessageBox.ButtonRole.AcceptRole)
        box.exec()

    # ---------------- 面板 ----------------
    def open_settings(self, focus_tab=None):
        dlg = SettingsDialog(self.cfg, assets=self.assets,
                             plugins=self.plugins, focus_tab=focus_tab)
        # 实时预览：拖动大小滑块 / 切换形象立即生效，取消时会由 _reject_restore 还原
        dlg.preview_size_changed.connect(self._preview_size)
        dlg.preview_character_changed.connect(self._preview_character)
        dlg.preview_cursor_size_changed.connect(self._preview_cursor_size)
        dlg.milestone_unlocked.connect(self._on_milestone_unlocked)
        dlg.dark_mode_changed.connect(self._on_dark_mode)
        dlg.bongo_size_changed.connect(lambda v: self.bongo.set_size(int(v)))
        dlg.bongo_opacity_changed.connect(lambda v: self.bongo.set_opacity(v / 100.0))
        dlg.bongo_click_through_changed.connect(self.bongo.set_click_through)
        dlg.classic_enabled_changed.connect(self._on_classic_enabled_changed)
        dlg.bongo_enabled_changed.connect(self._on_bongo_enabled_changed)
        dlg.pet_count_changed.connect(self._on_pet_count_changed)
        dlg.launch_plugin_requested.connect(self._launch_plugin)
        if dlg.exec():
            self._apply_settings()
            # 插件安装/卸载/启用可能变更，重扫并刷新小狗右键菜单
            try:
                self.plugins.rescan()
            except Exception:
                pass
        else:
            # 取消：确保回放还原（防止信号在取消处理之前已改变了实际渲染）
            self.pet.replay()
            try:
                self.cursor_pet.force_rerender()  # 还原鼠标跟随宠大小/形象
            except Exception:
                pass
            try:
                self._apply_modes()  # 还原 bongo 实时改动（大小/透明度/穿透）
            except Exception:
                pass
        # 面板关闭后释放控件/像素图占用（无论保存还是取消）
        QTimer.singleShot(1200, self._release_memory)

    def _on_dark_mode(self, _on):
        """设置面板里切换了深色模式：立即刷新托盘菜单等全局 UI。"""
        self._apply_menu_theme(self.tray.contextMenu())
        self.save()

    def _on_milestone_unlocked(self):
        """隐藏彩蛋解锁：连续点击【关于】5 次后显示里程碑入口。"""
        self.diary_db.unlock_milestone()
        self.act_milestone.setVisible(True)
        self._pet_say("哇！你发现了我的小秘密~ 里程碑入口开启啦！✨")
        if self.tray is not None:
            self.tray.showMessage(
                cfgmod.APP_NAME,
                "隐藏彩蛋解锁：里程碑《第一次》！",
                QSystemTrayIcon.MessageIcon.Information, 6000)

    def open_diary(self):
        dlg = DiaryDialog(self.cfg)
        dlg.exec()
        QTimer.singleShot(1200, self._release_memory)


    def open_milestone(self):
        for name in self.diary_db.check_rewards():
            self._pet_say(f"集齐了新的「第一次」！解锁称号「{name}」✨")
        dlg = MilestoneDialog(self.cfg)
        dlg.exec()
        QTimer.singleShot(1200, self._release_memory)

    def _preview_size(self, v):
        self.cfg["size"] = int(v)
        self.pet._rescale()
        for p in self.extra_pets:
            try:
                p._rescale()
            except Exception:
                pass

    def _preview_character(self, ch):
        self.cfg["character"] = ch or "xiaobai"
        self.pet.replay()
        for p in self.extra_pets:
            try:
                p.replay()
            except Exception:
                pass
        try:
            self.cursor_pet.set_character(self.cfg["character"])
        except Exception:
            pass
        try:
            self.cursor_pet.force_rerender()  # 刷新鼠标跟随宠
        except Exception:
            pass
        try:
            self.diary_db.record_outfit(self.cfg["character"])
        except Exception:
            pass

    def _preview_cursor_size(self, v):
        """设置面板拖动鼠标跟随宠大小滑块时实时预览。"""
        self.cfg["cursor_size"] = int(v)
        try:
            self.cursor_pet.set_size(int(v))
        except Exception:
            pass

    def _apply_settings(self):
        self.sound.set_enabled(self.cfg.get("sound_on", True))
        self.pet.refresh_bubble_style()
        self.pet.replay()
        self.pet.set_pet_opacity(self.cfg.get("pet_opacity", 1.0))
        for p in self.extra_pets:
            try:
                p.refresh_bubble_style()
                p.replay()
                p.set_pet_opacity(self.cfg.get("pet_opacity", 1.0))
            except Exception:
                pass
        try:
            self.diary_db.record_outfit(self.cfg.get("character", "xiaobai"))
        except Exception:
            pass
        self._register_hotkey()
        self._apply_lock_corner()
        self._sync_feature_switches()
        self._apply_menu_theme(self.tray.contextMenu())
        if self.chat:
            self.chat.set_ai_enabled(bool(self.cfg.get("ai_enabled")))
            self.chat.set_character_name(ai_mod.character_display_name(
                self.cfg.get("character", "xiaobai")))
        self.save()

    def _apply_lock_corner(self, immediate=False):
        corner = self.cfg.get("lock_corner") or ""
        self.pet.locked = bool(corner)
        if not corner or self._fs:
            return
        screen = self.qapp.screenAt(self.pet.frameGeometry().center()) \
                 or self.qapp.primaryScreen()
        geo = screen.availableGeometry()
        w, h = self.pet.width(), self.pet.height()
        mx = {"tl": geo.left() + w // 2 + 16, "tr": geo.right() - w // 2 - 16,
              "bl": geo.left() + w // 2 + 16, "br": geo.right() - w // 2 - 16}
        my = {"tl": geo.top() + h // 2 + 16, "tr": geo.top() + h // 2 + 16,
              "bl": geo.bottom() - h // 2 - 16, "br": geo.bottom() - h // 2 - 16}
        if corner in mx:
            self.pet.relocate(mx[corner], my[corner], immediate=immediate)

    def open_status(self):
        if self.status_dlg is not None:
            self.status_dlg.raise_()
            self.status_dlg.activateWindow()
            return
        dlg = StatusDialog(self._status_provider, self.cfg)
        dlg.feed.connect(self.feed)
        dlg.play.connect(self.play)
        dlg.chase.connect(self.start_chase)
        dlg.hidegame.connect(self.start_hide)
        dlg.finished.connect(self._status_closed)
        self.status_dlg = dlg
        dlg.show()

    def _status_closed(self):
        self.status_dlg = None
        QTimer.singleShot(1200, self._release_memory)

    def _status_provider(self):
        return self.cfg["mood"], self.cfg["energy"], self.pet.state

    # ---------------- 对话 / AI ----------------
    def open_chat(self):
        if self.chat is None:
            dlg = ChatDialog(bool(self.cfg.get("ai_enabled")), self.cfg)
            try:
                history = self.chat_db.recent(200)
            except Exception:
                history = []
            for role, text in history:
                dlg.add_msg(role, text)
            ai_seed = []
            for role, text in history:
                if role == "user":
                    ai_seed.append({"role": "user", "content": text})
                elif role == "pet":
                    ai_seed.append({"role": "assistant", "content": text})
            self.ai_history = ai_seed[-8:]
            dlg.message_added.connect(self._on_chat_msg)
            dlg.submitted.connect(self.handle_chat)
            dlg.finished.connect(self._chat_closed)
            self.chat = dlg
        self.chat.set_ai_enabled(bool(self.cfg.get("ai_enabled")))
        self.chat.set_character_name(ai_mod.character_display_name(
            self.cfg.get("character", "xiaobai")))
        self.chat.show()
        self.chat.raise_()
        self.chat.activateWindow()

    def _chat_closed(self):
        # 保留 chat 实例与 AI 历史，再开时恢复对话记录（用户要求关掉后不丢失）
        QTimer.singleShot(600, self._release_memory)

    def _on_chat_msg(self, role, text):
        self._chat_new.append((role, text, time.time()))

    def _save_chat(self):
        if not self._chat_new:
            self.chat_db.close()
            return
        try:
            self.chat_db.add_many(self._chat_new)
            self.chat_db.trim(200)
        except Exception:
            pass
        self.chat_db.close()
        self._chat_new = []

    def handle_chat(self, text):
        self.chat.add_msg("user", text)
        diary_mod.record("chat", self.cfg.get("mood"), self.cfg.get("energy"))
        cmd = ai_mod.parse_command(text)
        if cmd:
            reply = self._run_command(cmd)
            self.chat.add_msg("pet", reply)
            self.pet.show_bubble(reply, 3500)
            return
        if not (self.cfg.get("ai_enabled") and self.cfg.get("ai_key")
                and self.cfg.get("ai_base")):
            tip = "AI 还没开启：托盘→设置→AI，填入你自己的 API 就能聊天啦"
            self.chat.add_msg("sys", tip)
            self.pet.show_bubble("要先在设置里填 API 哦~", 3200)
            return
        self.chat.set_busy(True)
        context_text = self._ai_context_text() if self.cfg.get("ai_context") else ""
        self.ai_history.append({"role": "user", "content": text})
        self.ai_worker = ai_mod.AIWorker(
            self.cfg, self.ai_history, context_text, text)

        worker = self.ai_worker
        worker.replied.connect(self._ai_replied)
        worker.failed.connect(self._ai_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _ai_replied(self, text):
        # 保留 worker 引用直到 finished.connect 触发 deleteLater；这里只清空业务引用
        worker = self.ai_worker
        self.ai_worker = None
        if worker is not None:
            # 提前把 history 大对象从 worker 上摘掉，方便 GC
            worker.history = None
            worker.cfg = None
            worker.context_text = None
            worker.user_text = None
        self.ai_history.append({"role": "assistant", "content": text})
        if len(self.ai_history) > 8:  # 从 12 条缩到 8 条，少占内存
            self.ai_history = self.ai_history[-8:]
        if self.chat:
            self.chat.add_msg("pet", text)
            self.chat.set_busy(False)
        short = text if len(text) <= 40 else text[:40] + "…"
        self.pet.show_bubble(short, 6000)

    def _ai_failed(self, msg):
        worker = self.ai_worker
        self.ai_worker = None
        if worker is not None:
            worker.history = None
            worker.cfg = None
            worker.context_text = None
            worker.user_text = None
        if self.chat:
            self.chat.add_msg("sys", msg)
            self.chat.set_busy(False)
        self.pet.show_bubble("我连不上 API，等下再试~", 3200)

    def _ai_context_text(self):
        parts = []
        h = time.localtime().tm_hour
        parts.append("时段：" + ("深夜" if (h >= 22 or h < 6)
                                else "傍晚" if h >= 18 else "白天"))
        cpu = winapi.cpu_percent()
        if cpu is not None:
            parts.append("CPU负载：" + ("很高，电脑在忙" if cpu > 72
                                       else "偏低，很空闲" if cpu < 40 else "一般"))
        idle = winapi.get_idle_seconds()
        parts.append("用户状态：" + ("已经离开/在睡觉" if idle > 180
                                    else "正在专注用电脑" if idle < 30 else "在电脑前"))
        if self._comp_mode == "code":
            parts.append("用户正在写代码")
        elif self._comp_mode == "browser":
            parts.append("用户正在浏览网页")
        if self._fs:
            parts.append("用户在全屏应用里")
        parts.append("宠物心情%d/体力%d" % (int(self.cfg["mood"]), int(self.cfg["energy"])))
        return "；".join(parts)

    def _run_command(self, cmd):
        kind, arg = cmd
        r = ai_mod.COMMAND_REPLIES
        if kind == "size":
            self.cfg["size"] = max(80, min(260, self.cfg["size"] + arg))
            self.pet.replay()
            self.save()
            return r["size"][arg < 0]
        if kind == "hide":
            self.set_pet_visible(False)
            return r["hide"]
        if kind == "show":
            self.set_pet_visible(True)
            return r["show"]
        if kind == "sound":
            self.cfg["sound_on"] = arg
            self.sound.set_enabled(arg)
            self.save()
            return r["sound_on"] if arg else r["sound_off"]
        if kind == "chase":
            self.start_chase()
            return r["chase"]
        if kind == "hidegame":
            self.start_hide()
            return r["hidegame"]
        if kind == "sleep":
            self.pet.go_sleep()
            return r["sleep"]
        if kind == "feed":
            self.feed()
            return r["feed"]
        return "好~"

    # ---------------- 全局快捷键 ----------------
    def _register_hotkey(self):
        if not winapi.IS_WINDOWS:
            return
        winapi.unregister_hotkey(HOTKEY_ID)
        if self.cfg.get("hotkey_on"):
            ok = winapi.register_hotkey(HOTKEY_ID, self.cfg.get("hotkey", "Ctrl+Alt+D"))
            if not ok:
                self.tray.showMessage(
                    "LineDogPet",
                    "全局快捷键注册失败（可能被别的软件占用），可到设置里换一个。",
                    QSystemTrayIcon.MessageIcon.Warning, 4000)

    # ---------------- 自动检查更新 ----------------
    def _auto_check_update(self):
        urls = list(getattr(cfgmod, "UPDATE_CHECK_URLS", []) or [])
        if not urls:
            single = (getattr(cfgmod, "UPDATE_CHECK_URL", "") or "").strip()
            if single:
                urls = [single]
        self._update_urls = [u for u in urls if u]
        self._update_idx = 0
        self._check_next_update()

    def _check_next_update(self):
        if self._update_idx >= len(self._update_urls):
            # 全部源都失败：释放网络管理器
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
                    if _compare_version(latest, current) > 0:
                        self._latest_version = latest
                        self.tray.showMessage(
                            cfgmod.APP_NAME,
                            f"发现新版本 v{latest}！点击此处前往下载~",
                            QSystemTrayIcon.MessageIcon.Information, 8000)
        except Exception:
            pass
        finally:
            reply.deleteLater()
            if got_version:
                # 成功取到版本：释放网络管理器
                if self._net is not None:
                    self._net.deleteLater()
                    self._net = None
            else:
                # 当前源失败：尝试下一个源
                self._check_next_update()

    def _on_update_message_clicked(self):
        QDesktopServices.openUrl(QUrl(cfgmod.RELEASE_GITEE))

    # ---------------- 持久化 / 退出 ----------------
    def save(self):
        try:
            self.cfg["pos"] = [int(self.pet.x()), int(self.pet.y())]
            self.cfg["mood"] = round(float(self.cfg["mood"]), 2)
            self.cfg["energy"] = round(float(self.cfg["energy"]), 2)
            cfgmod.save_config(self.cfg)
        except Exception:
            pass

    def quit_app(self):
        self.save()
        self._save_chat()
        try:
            self.plugins.close_all()
        except Exception:
            pass
        for p in self.extra_pets:
            try:
                p.hide()
            except Exception:
                pass
        try:
            self.cursor_pet.set_enabled(False)
        except Exception:
            pass
        try:
            self.cursor_light.set_enabled(False)
        except Exception:
            pass
        winapi.unregister_hotkey(HOTKEY_ID)
        self.tray.hide()
        self.qapp.quit()


def _cursor_pos_x():
    if winapi.IS_WINDOWS:
        import ctypes
        from ctypes import wintypes
        pt = wintypes.POINT()
        ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
        return pt.x
    return 0


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("SmartPet")
    icon_path = cfgmod.app_icon_path()
    if icon_path:
        app.setWindowIcon(QIcon(icon_path))

    pet_app = PetApp(app)

    # 全局快捷键消息
    hotfilter = HotkeyFilter(pet_app.toggle_pet)
    app.installNativeEventFilter(hotfilter)

    # 托盘菜单存在即保活
    sys.exit(app.exec())
