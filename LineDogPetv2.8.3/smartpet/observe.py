# -*- coding: utf-8 -*-
"""
v1.3.0 偷偷观察你 / 听歌 / 夜间陪伴。
全部纯本地规则匹配，不联网、不用大模型。
- ObserveManager：前台窗口/进程变化时从台词池随机选一句。
- MusicDetector：检测系统音频播放状态和媒体信息。
- NightCompanion：按时段触发夜间陪伴台词与睡觉状态。
"""
import time
import random

import re

from PyQt6.QtCore import QTimer, QObject

from . import winapi

# ---- 观察你：窗口标题/类名关键词 -> 台词池 ----
OBSERVE_RULES = [
    (["VISUAL STUDIO CODE", "VSCODE", "CODE -", "PYCHARM", "INTELLIJ",
      "IDEA", "CURSOR", "WINDSURF", "TRAE", "SUBLIME"],
     ["程序员又开始写代码了。", "又在调 bug 吗？", "写代码要坐直哦~"]),
    (["STEAM"], ["工作结束了吗？", "又开 Steam 啦~", "玩会儿也行啦。"]),
    (["WORD", "WPS", "DOCUMENT"], ["又写报告？", "文档写完了吗~"]),
    (["EXCEL", "表格"], ["又在算表格呀。", "数据好多~"]),
    (["BILIBILI", "B站", "YOUTUBE"], ["又在看视频~", "劳逸结合不错嘛。"]),
    (["PHOTOSHOP", "PS", "CLIP STUDIO"], ["画画呢~好认真。", "画完给我看看嘛。"]),
    (["POWERPOINT", "PPT"], ["又要做 PPT 呀。"]),
]

# ---- 听歌：播放器窗口标题关键词（完整名，避免误匹配）----
MUSIC_PLAYERS = [
    "网易云音乐", "Spotify", "QQ音乐", "QQMusic", "foobar2000", "foobar",
    "酷狗音乐", "酷我音乐", "AIMP", "Dopamine", "MusicBee", "MediaMonkey",
    "VLC", "PotPlayer", "WMPlayer", "Windows Media Player", "Groove 音乐",
    "iTunes", "Apple Music", "Winamp", "KMPlayer", "GOM Player",
    "MPC-HC", "MPC-BE", "千千音乐", "百度音乐", "虾米音乐", "咪咕音乐",
    "YesPlayMusic", "Listen1", "网易云音乐",
]

# 播放器窗口类名（标题可能只有"歌名 - 歌手"而无播放器名，靠类名兜底）
MUSIC_PLAYER_CLASSES = {
    "TXGuiFoundation",        # QQ音乐（腾讯系 GUI）
    "OrpheusBrowserHost",     # 网易云音乐
    "WMPlayerApp",            # Windows Media Player
    "foobar2000",             # foobar2000
    "Winamp",                 # Winamp
    "AIMP",                   # AIMP
    "Dopamine",               # Dopamine
    "MusicBee",               # MusicBee
    "KuGou",                  # 酷狗
    "KwMusic",                # 酷我
    "TTPlayer",               # 千千
}


class ObserveManager(QObject):
    """前台窗口变化时按规则吐槽/陪伴。"""

    def __init__(self, cfg, on_bubble):
        super().__init__()
        self.cfg = cfg
        self.on_bubble = on_bubble
        self._last_sig = ""
        self._timer = QTimer(self)
        self._timer.setInterval(8000)  # 8s 检查一次
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
        if not self.cfg.get("observe_enabled"):
            return
        if not (self.cfg.get("sense_enabled") and self.cfg.get("sense_window")):
            return
        fg = winapi.foreground_info() if winapi.IS_WINDOWS else None
        if not fg:
            return
        title = (fg.get("title") or "").upper()
        if not title:
            return
        sig = title[:40]
        if sig == self._last_sig:
            return
        for keys, words in OBSERVE_RULES:
            if any(k in title for k in keys):
                self._last_sig = sig
                self.on_bubble(random.choice(words))
                return
        self._last_sig = sig


class MusicDetector(QObject):
    """扫描播放器窗口标题解析歌名，统计循环次数。纯窗口枚举，不卡 UI。

    防刷屏：酷狗/QQ 等播放器会把系统标题栏当跑马灯滚动（靠不断改写窗口
    标题实现），导致每帧解析出的"歌名"都不同。这里用"滚动不变签名"判定
    同一首歌——去掉分隔符/空白后对字符排序，跑马灯只是旋转字符、集合不变，
    签名稳定；再加连续两轮去抖 + 单曲冒泡冷却，彻底避免滚动一次就乱报一次。
    """

    _ANNOUNCE_COOLDOWN = 600.0  # 同一首歌 10 分钟内不重复冒泡

    def __init__(self, cfg, on_bubble, on_state=None):
        super().__init__()
        self.cfg = cfg
        self.on_bubble = on_bubble
        self.on_state = on_state  # 可选：切换小狗动画
        self._play_counts = {}    # 签名 -> 次数
        self._sig = ""            # 已确认歌曲的滚动不变签名
        self._pending = None      # (sig, disp)：上一轮首次见到的候选，待确认
        self._last_announce = {}  # 签名 -> 上次冒泡时间戳
        self._timer = QTimer(self)
        self._timer.setInterval(5000)  # 5s 扫一次
        self._timer.timeout.connect(self._tick)
        self._started = False

    def set_enabled(self, on):
        if on and not self._started:
            self._started = True
            self._timer.start()
        elif not on and self._started:
            self._started = False
            self._timer.stop()
            self._reset()

    def _reset(self):
        self._sig = ""
        self._pending = None

    def _tick(self):
        if not self.cfg.get("music_enabled"):
            self._reset()
            return
        hit = None
        wins = winapi.enum_window_titles(min_area=4000) if winapi.IS_WINDOWS else []
        for title, cls in wins:
            song = self._extract_song(title, cls)
            if song:
                hit = (title, cls, song)
                break
        if not hit:
            return  # 无播放器窗口：保持状态，不误清空（避免窗口闪动导致重复计数）
        title, cls, disp = hit
        sig = self._signature(title)
        if sig == self._sig:
            return  # 还是同一首（含标题栏滚动）：不计数、不冒泡
        # 签名变了：需连续两轮一致才认定为"换歌"，过滤切换/滚动瞬时帧
        if self._pending is not None and self._pending[0] == sig:
            disp = self._pending[1]  # 用首次见到（通常刚切歌、标题最干净）的歌名
            self._pending = None
            self._sig = sig
            cnt = self._play_counts.get(sig, 0) + 1
            self._play_counts[sig] = cnt
            if self.on_state:
                self.on_state(disp)
            now = time.time()
            if now - self._last_announce.get(sig, 0.0) < self._ANNOUNCE_COOLDOWN:
                return  # 冷却内不重复刷屏
            self._last_announce[sig] = now
            if cnt >= 3:
                self.on_bubble("你今天循环这首歌 %d 次了。" % cnt)
            else:
                self.on_bubble("在听《%s》呀，我也爱听~" % disp)
        else:
            self._pending = (sig, disp)

    # 跑马灯只是循环移动字符，去掉分隔符/空白后字符集合不变，据此做稳定签名
    _SEP_RE = re.compile(r"[\s\-\u2014\u2013|\uff5c\u00b7\u266a\u266b\u266c\u266d]+")

    @classmethod
    def _signature(cls, title):
        s = cls._SEP_RE.sub("", title or "")
        return "".join(sorted(s))

    @staticmethod
    def _extract_song(title, cls):
        """从窗口标题/类名提取歌名。
        1) 类名命中已知播放器 → 直接解析标题；
        2) 标题含播放器名关键词 → 去掉播放器名后解析；
        3) 否则返回 None。
        """
        if not title:
            return None
        t = title.strip()
        # 1) 类名兜底：QQ音乐(TXGuiFoundation)、网易云(OrpheusBrowserHost)等
        if cls in MUSIC_PLAYER_CLASSES:
            return MusicDetector._split_song(t)
        # 2) 标题含播放器名
        up = t.upper()
        for pk in MUSIC_PLAYERS:
            if pk.upper() in up:
                return MusicDetector._split_song(t, pk)
        return None

    @staticmethod
    def _split_song(text, player_key=None):
        """按 '歌曲 - 躺手' 等分隔符取歌名，过滤掉播放器名段。"""
        t = text.strip().rstrip("[").strip()
        if not t:
            return None
        for sep in (" - ", " | ", " — ", " – "):
            if sep in t:
                parts = [p.strip() for p in t.split(sep) if p.strip()]
                if player_key:
                    parts = [p for p in parts if p != player_key]
                return parts[0] if parts else None
        if player_key and t == player_key:
            return None
        return t or None


class NightCompanion(QObject):
    """按时段触发夜间陪伴台词。"""

    NIGHT_WORDS = {
        23: ["还不睡吗？"],
        1: ["…(打了个哈欠)…"],
        2: ["明天再做吧。"],
    }

    def __init__(self, cfg, on_bubble, on_sleep=None):
        super().__init__()
        self.cfg = cfg
        self.on_bubble = on_bubble
        self.on_sleep = on_sleep  # 2 点切趴卧睡觉
        self._fired = set()       # 已触发的 (date,hour)
        self._accompanied = False  # 2 点后是否已说"那我陪你"
        self._timer = QTimer(self)
        self._timer.setInterval(15000)  # 15s 检查一次
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
        if not self.cfg.get("night_enabled"):
            return
        now = time.localtime()
        date = time.strftime("%Y-%m-%d", now)
        h = now.tm_hour
        # 跨日清理已触发标记
        self._fired = {k for k in self._fired if k[0] == date}
        # 23/1/2 点台词
        key = (date, h)
        if h in self.NIGHT_WORDS and key not in self._fired:
            self._fired.add(key)
            words = self.NIGHT_WORDS[h]
            self.on_bubble(random.choice(words))
            if h == 2 and self.on_sleep:
                self.on_sleep()
                self._accompanied = False
        # 2 点后用户有键鼠操作 -> "那我陪你。"
        if h >= 2 and h < 6 and not self._accompanied:
            idle = winapi.get_idle_seconds()
            if idle < 3:
                self._accompanied = True
                self.on_bubble("那我陪你。")