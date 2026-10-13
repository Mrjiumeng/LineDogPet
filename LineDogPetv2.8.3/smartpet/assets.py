# -*- coding: utf-8 -*-
"""
素材管理（文件夹约定，方便后续维护更新）：

素材全部放在 pets/<角色>/<动作>/*.gif，例如：
    pets/xiaobai/idle/无聊.gif
    pets/xiaobai/happy/开心.gif
    pets/xiaojimao/eat/吃饭.gif
新增/更换素材时，只要把 GIF 丢进对应角色的对应动作文件夹即可，无需改代码。

另有一个“双狗共享池” pets/shared/<动作>/*.gif（不是可选角色，不出现在设置里）：
里面是两只小狗同框的 GIF。任何角色播某动作时，只要池里有该动作的双狗图，
就会按 SHARED_CHANCE 的概率随机播到（各种表情同理）。
想加双狗素材，直接把 GIF 丢进 pets/shared/<对应动作>/ 即可。

手势固定动画（FIXED_GIFS）：长按抱起、画圈、甩鼠标这几个交互的反应是“招牌动作”，
永远固定播 pets/shared/<动作>/ 下指定的那一张（与角色无关、不随机、不抽池），
和原版手感保持一致。要换招牌图，替换对应文件即可。

- 启动时只扫描一遍路径建立索引，驻留内存的只是字符串（几百条路径）。
- QMovie 绝不缓存：播放时才按需加载，切换即销毁旧的，内存里同一时刻只有一个动画。
- 某角色某动作文件夹为空时，按 ACTION_FALLBACK 找视觉近似动作，
  再缺就借用小白（xiaobai），保证永远有图可播。
- 音效：首次需要时用标准库合成几个极短 wav 到临时目录（几 KB），winsound 异步播放。
"""
import os
import math
import struct
import wave
import tempfile
import random

from . import config

PETS_DIR = os.path.join(config.APP_DIR, "pets")
# 打包 onefile 后 APP_DIR 是临时解压目录，自定义角色需持久化到 exe 同级目录
PETS_DIRS = [PETS_DIR]
if config.EXE_DIR != config.APP_DIR:
    PETS_DIRS.append(os.path.join(config.EXE_DIR, "pets"))
SOUNDS_DIR = os.path.join(config.APP_DIR, "sounds")
CURSOR_DIR = os.path.join(config.APP_DIR, "cursor")
DEFAULT_CHAR = "xiaobai"
# 「小白和小鸡毛」：特殊默认角色，任何动作都始终播放“双狗同框”共享池素材
DUO_CHAR = "duo"
# 内置默认角色（不可删除/改名；不出现在自定义角色里）
BUILTIN_CHARS = ("xiaobai", "xiaojimao", DUO_CHAR)
# 双狗共享池目录名（保留字，不会被当作角色出现在设置里）
SHARED_DIR = "shared"
# 本角色素材与双狗池都非空时，抽到双狗 GIF 的概率
SHARED_CHANCE = 0.35
# 手势招牌动作 -> pets/shared/<动作>/ 下固定播放的文件名（不随机、不分角色）
FIXED_GIFS = {
    "pickup": "举起来.gif",   # 长按抱起/拖拽
    "spin": "转圈.gif",       # 按住鼠标画圈
    "chase": "狂奔.gif",      # 快甩鼠标追出去
}

# 动作清单（= 可用的动作文件夹名；也是 pet.py 传入的动作 key）
ACTIONS = [
    "idle",      # 发呆/待机
    "happy",     # 开心
    "pet",       # 摸头
    "chin",      # 挠下巴
    "pickup",    # 抱起来
    "spin",      # 转圈
    "hop",       # 蹦跳/呼唤
    "sleep",     # 睡觉
    "deep",      # 蜷成一团深睡
    "walk",      # 走路
    "chase",     # 追球
    "scared",    # 受惊
    "nervous",   # 紧张踱步
    "relax",     # 放松趴着
    "groom",     # 舔毛/小动作
    "eat",       # 吃东西
    "paper",     # 接收信息/发现
    "work",      # 陪写代码
    "peek",      # 探头
    "play",      # 玩耍
    "hide",      # 躲起来
]

# 动作缺素材时的视觉近似回退链（按顺序尝试，最后统一兜底 idle）
ACTION_FALLBACK = {
    "idle": [],
    "happy": ["idle"],
    "pet": ["happy", "idle"],
    "chin": ["groom", "happy", "idle"],
    "pickup": ["hop", "happy", "idle"],
    "spin": ["play", "happy", "idle"],
    "hop": ["happy", "idle"],
    "sleep": ["idle"],
    "deep": ["sleep", "idle"],
    "walk": ["idle"],
    "chase": ["walk", "idle"],
    "scared": ["nervous", "idle"],
    "nervous": ["scared", "idle"],
    "relax": ["groom", "idle"],
    "groom": ["relax", "idle"],
    "eat": ["happy", "idle"],
    "paper": ["work", "idle"],
    "work": ["idle"],
    "peek": ["play", "idle"],
    "play": ["happy", "idle"],
    "hide": ["scared", "idle"],
}


class AssetIndex:
    """按 pets/<角色>/<动作> 文件夹组织的 GIF 索引。"""

    def __init__(self):
        self._by = {}        # (角色, 动作) -> [gif路径...]
        self._shared = {}    # 动作 -> [双狗共享 gif 路径...]
        self._chars = []     # 实际存在的角色（不含 shared）
        self._last = {}      # (角色, 动作) -> 上次选中的路径，避免连播同一张
        self._scan()

    def _scan(self):
        for pets_dir in PETS_DIRS:
            if not os.path.isdir(pets_dir):
                continue
            for ch in sorted(os.listdir(pets_dir)):
                cdir = os.path.join(pets_dir, ch)
                if not os.path.isdir(cdir):
                    continue
                if ch == SHARED_DIR:
                    bucket = self._shared
                    key_is_action = True
                else:
                    if ch in self._chars:
                        continue  # 已从前一个目录扫到，跳过
                    self._chars.append(ch)
                    bucket = self._by
                    key_is_action = False
                for act in sorted(os.listdir(cdir)):
                    adir = os.path.join(cdir, act)
                    if not os.path.isdir(adir):
                        continue
                    gifs = [os.path.join(adir, f) for f in sorted(os.listdir(adir))
                            if f.lower().endswith(".gif")]
                    if gifs:
                        key = act if key_is_action else (ch, act)
                        if key not in bucket:
                            bucket[key] = gifs

    def refresh(self):
        """添加/删除自定义角色后重新扫描素材目录。"""
        self._by.clear()
        self._shared.clear()
        self._chars.clear()
        self._last.clear()
        self._scan()

    # ---- 供面板/诊断查看 ----
    def characters(self):
        return list(self._chars)

    def char_display_pairs(self):
        """设置面板用的角色列表：[(显示名, 角色id)]。

        内置角色显示中文名且排前面，自定义角色按文件夹名排序在后。
        """
        builtin = [("小白", "xiaobai"), ("小鸡毛", "xiaojimao"),
                   ("小白和小鸡毛", DUO_CHAR)]
        custom = sorted((ch, ch) for ch in self._chars
                        if ch not in BUILTIN_CHARS and ch != SHARED_DIR)
        return builtin + custom

    def has_character(self, name):
        return name in BUILTIN_CHARS or name in self._chars

    def is_custom(self, name):
        """是否为用户自定义角色（内置角色不允许删除/改名）。"""
        return (name not in BUILTIN_CHARS and name != SHARED_DIR
                and name in self._chars)

    def action_counts(self, character):
        """该角色自己文件夹里每个动作的 gif 数量：{动作: 数量}（不含 shared 池）。"""
        return {a: len(v) for (c, a), v in self._by.items() if c == character}

    def _has_any(self, character):
        return any(k[0] == character for k in self._by)

    def actions(self, char=DEFAULT_CHAR):
        return sorted(a for (c, a) in self._by if c == char)

    def action_count(self, char, action):
        return len(self._by.get((char, action), ()))

    def shared_count(self, action):
        return len(self._shared.get(action, ()))

    def _shared_for(self, action):
        """该动作的双狗池；为空时沿回退链找视觉近似的双狗图。"""
        pool = self._shared.get(action)
        if pool:
            return pool
        for a in ACTION_FALLBACK.get(action, []):
            if self._shared.get(a):
                return self._shared[a]
        return None

    def _pick(self, char, action, allow_shared=True):
        """从“本角色该动作”与“双狗共享池该动作”里抽一张。

        两者都有：按 SHARED_CHANCE 抽双狗，其余抽本角色；
        该动作没有双狗图时，沿回退链借近似动作的双狗图（例如转圈借开心双狗）；
        只有一边：只能用哪边；都没有：None（交给回退链）。
        allow_shared=False 时（自定义角色）只用本角色素材，彻底独立。
        """
        own = self._by.get((char, action))
        share = self._shared_for(action) if allow_shared else None
        if own and share:
            # 本角色素材越少（如摸头几乎都是双狗图），越倾向共享池，避免反复同一张；
            # 素材充足时至少保证 SHARED_CHANCE 的双狗出镜率
            chance = max(SHARED_CHANCE, len(share) / (len(own) + len(share)))
            pool = share if random.random() < chance else own
        else:
            pool = own or share
        if not pool:
            return None
        key = (char, action)
        last = self._last.get(key)
        choices = [p for p in pool if p != last] or pool
        pick = random.choice(choices)
        self._last[key] = pick
        return pick

    def _chain(self, char, action, tried, allow_shared=True):
        for a in [action] + ACTION_FALLBACK.get(action, ["idle"]):
            if (char, a) in tried:
                continue
            tried.add((char, a))
            p = self._pick(char, a, allow_shared)
            if p:
                return p
        return None

    def resolve(self, action, character=DEFAULT_CHAR):
        """角色 + 动作 -> 真实存在的 gif 路径（对默认角色保证兜底成功）。

        手势招牌动作（FIXED_GIFS）永远返回固定那一张；
        默认角色顺序：本角色该动作 → 本角色近似回退 → 小白 → 小白 idle。
        自定义角色完全独立：只在自己的素材里选动作（连双狗共享池也不参与），
        绝不影响默认角色；仅当该自定义角色一张图都没有（刚创建还没上传）时，
        才用小白兜底保证切换过去仍有画面可看。
        """
        fixed = FIXED_GIFS.get(action)
        if fixed:
            for pets_dir in PETS_DIRS:
                p = os.path.join(pets_dir, SHARED_DIR, action, fixed)
                if os.path.exists(p):
                    return p
        if character == DUO_CHAR:
            return self._resolve_duo(action)
        tried = set()
        if character != DEFAULT_CHAR:
            # 自定义角色：独立路径，只用自己文件夹的素材
            p = self._chain(character, action, tried, allow_shared=False)
            if not p and not self._has_any(character):
                # 一张都没有才借用小白（保证刚创建时可见）
                p = self._chain(DEFAULT_CHAR, action, tried)
            if not p and self._has_any(character):
                pool = [g for k, v in self._by.items()
                        if k[0] == character for g in v]
                p = random.choice(pool) if pool else None
            if not p:
                p = self._pick(DEFAULT_CHAR, "idle", allow_shared=False)
            return p or ""
        # 默认角色：原逻辑（含双狗共享池 + 小白兜底）
        p = self._chain(character, action, tried)
        if not p:
            p = self._chain(DEFAULT_CHAR, action, tried)
        if not p:
            p = self._pick(DEFAULT_CHAR, "idle", allow_shared=False)
        return p or ""

    def resolve_own(self, action, character=DEFAULT_CHAR):
        """只用该角色自己的素材解析（不参与双狗共享池）。

        用于像“鼠标跟随宠”这样需要一个单纯个体形象的场景，
        避免出现双狗同框图；结果永远是真实存在的路径或空串。
        """
        tried = set()
        p = self._chain(character, action, tried, allow_shared=False)
        if not p and character != DEFAULT_CHAR and not self._has_any(character):
            p = self._chain(DEFAULT_CHAR, action, tried)
        if not p:
            p = self._pick(DEFAULT_CHAR, "idle", allow_shared=False)
        return p or ""

    def _resolve_duo(self, action):
        """「小白和小鸡毛」：始终播放双狗同框素材。

        顺序：双狗池该动作 → 沿回退链借近似动作的双狗图 → 任意一张双狗图；
        共享池完全为空时才兜底小白 idle（保证仍有画面）。
        """
        pool = self._shared_for(action)
        if not pool:
            allshared = [g for v in self._shared.values() for g in v]
            pool = allshared or None
        if pool:
            key = (DUO_CHAR, action)
            last = self._last.get(key)
            choices = [g for g in pool if g != last] or pool
            pick = random.choice(choices)
            self._last[key] = pick
            return pick
        p = self._pick(DEFAULT_CHAR, "idle", allow_shared=False)
        return p or ""



# ---------------------------------------------------------------- 音效

def _tone(fr, dur, f0, f1=None, vol=0.4, arch=False, harmonics=()):
    """生成一段带包络的正弦音（可加泛音、可选拱形音高，用于模仿喵叫）。"""
    n = int(fr * dur)
    out = []
    for i in range(n):
        frac = i / max(1, n - 1)
        if arch:
            freq = f0 + (f1 or 0) * math.sin(math.pi * frac)
        else:
            freq = f0 + ((f1 or f0) - f0) * frac
        env = min(1.0, i / (fr * 0.008))
        env *= math.exp(-1.6 * frac)
        val = math.sin(2 * math.pi * freq * i / fr)
        for mul, amp in harmonics:
            val += amp * math.sin(2 * math.pi * freq * mul * i / fr)
        # 结尾 8ms 淡入零，防止爆音
        if i > n - fr * 0.008:
            env *= (n - i) / (fr * 0.008)
        out.append(vol * env * val)
    return out


def _silence(fr, dur):
    return [0.0] * int(fr * dur)


def _write_wav(path, samples, fr=22050):
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fr)
        frames = bytearray()
        for s in samples:
            v = int(max(-1.0, min(1.0, s)) * 30000)
            frames += struct.pack("<h", v)
        w.writeframes(bytes(frames))


class SoundManager:
    """合成并播放极短音效；静音开关即时生效。"""
    def __init__(self, enabled=True):
        self.enabled = enabled
        self._dir = os.path.join(tempfile.gettempdir(), "smartpet_sounds")
        self._ready = False
        self._winsound = None
        try:
            import winsound
            self._winsound = winsound
        except Exception:
            pass

    def set_enabled(self, on):
        self.enabled = on

    def _ensure(self):
        if self._ready:
            return
        self._ready = True
        if not self._winsound:
            return
        try:
            os.makedirs(self._dir, exist_ok=True)
            fr = 22050
            recipes = {
                # 小狗叫：两声短促的下滑音
                "bark.wav": (_tone(fr, 0.05, 430, 300, 0.5, harmonics=((2, .25),))
                            + _silence(fr, 0.015)
                            + _tone(fr, 0.10, 280, 170, 0.55, harmonics=((2, .3),))),
                # 猫叫：拱形音高 + 轻微颤音感
                "meow.wav": _tone(fr, 0.24, 620, 360, 0.32, arch=True,
                                  harmonics=((2, .12),)),
                # 风铃声（整点 / 转圈）
                "chime.wav": (_tone(fr, 0.10, 880, 880, 0.28,
                                    harmonics=((2.01, .2), (3, .08)))
                            + _silence(fr, 0.02)
                            + _tone(fr, 0.18, 1318, 1318, 0.22,
                                    harmonics=((2.01, .15),))),
                # 啾（呼唤回应）
                "chirp.wav": (_tone(fr, 0.06, 700, 1150, 0.3)
                            + _silence(fr, 0.02)
                            + _tone(fr, 0.07, 760, 1250, 0.28)),
                # 小声哼唧（高负载/害怕）
                "whine.wav": _tone(fr, 0.30, 430, 250, 0.2,
                                   harmonics=((2, .1),)),
                # 吃饭吧唧嘴（两声闷短音）
                "munch.wav": _tone(fr, 0.05, 240, 160, 0.35)
                            + _silence(fr, 0.05)
                            + _tone(fr, 0.05, 220, 150, 0.35),
            }
            for name, samples in recipes.items():
                p = os.path.join(self._dir, name)
                if not os.path.exists(p):
                    _write_wav(p, samples, fr)
        except Exception:
            pass

    def play(self, name):
        if not self.enabled or not self._winsound:
            return
        self._ensure()
        # 用户自定义音效优先：sounds/<name>.wav（例如自定义 bark.wav 取代合成狗叫）
        custom = os.path.join(SOUNDS_DIR, name + ".wav")
        p = custom if os.path.exists(custom) else os.path.join(self._dir, name + ".wav")
        if not os.path.exists(p):
            return
        try:
            ws = self._winsound
            ws.PlaySound(p, ws.SND_FILENAME | ws.SND_ASYNC | ws.SND_NODEFAULT)
        except Exception:
            pass

    @staticmethod
    def has_custom_sound():
        return os.path.exists(os.path.join(SOUNDS_DIR, "bark.wav"))
