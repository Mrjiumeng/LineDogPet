# -*- coding: utf-8 -*-
"""
Windows 系统感知：全部用 ctypes 直接调系统 API，零第三方依赖、零联网。
- 键鼠闲置时间：GetLastInputInfo
- CPU 占用率：GetSystemTimes 两次采样差分
- 前台窗口 / 窗口矩形 / 类名：GetForegroundWindow 等
- 全屏检测、窗口枚举：EnumWindows
- 全局快捷键：RegisterHotKey（WM_HOTKEY 在 app 层接收）
非 Windows 平台给出安全降级（返回空数据，不报错）。
"""
import sys

IS_WINDOWS = sys.platform == "win32"

# 虚拟键码
VK_MAP = {
    "SPACE": 0x20, "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "INSERT": 0x2D, "DELETE": 0x2E, "HOME": 0x24, "END": 0x23,
    "PGUP": 0x21, "PGDOWN": 0x22, "TAB": 0x09, "RETURN": 0x0D, "ENTER": 0x0D,
    "ESC": 0x1B, "CAPSLOCK": 0x14,
}
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN = 0x1, 0x2, 0x4, 0x8
WM_HOTKEY = 0x0312

# 透明宿主窗类名（肉眼不可见却会被 EnumWindows 枚举到的合成/系统层窗口）
CLOAKED_CLASSES = {
    "Progman", "WorkerW", "Shell_TrayWnd",
    "Windows.UI.Core.CoreWindow", "ApplicationManager_ImmersiveWindow",
    "Windows.UI.Composition.DesktopWindowContentBridge",  # WinUI 3 合成宿主
    "XamlExplorerHostIslandWindow",                       # XAML Island 宿主
}
# 带随机后缀的透明宿主类名前缀（如 WPF 的 HwndWrapper[App.exe;;xxxx]）
CLOAKED_CLASS_PREFIXES = ("HwndWrapper",)


def dwm_is_cloaked(hwnd):
    """窗口是否被 DWM 合成器隐藏（不可见合成宿主/虚拟桌面不可见窗等）。

    非 Windows、dwmapi 缺失或调用失败时返回 False（按"不排除"处理，
    绝不误杀真实窗口）。
    """
    if not IS_WINDOWS:
        return False
    try:
        DWMWA_CLOAKED = 14
        val = wintypes.BOOL()
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(
                hwnd, DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val)):
            return bool(val.value)
        return False
    except Exception:
        return False


def is_transparent_host(hwnd, cls, ex=0):
    """综合判定是否为透明宿主/不可见辅助窗（类名黑名单 + 前缀 + DWM cloak）。

    ex 预留供后续扩展，当前不参与判定（避免误用样式位误杀 Chromium 窗口）。
    """
    if cls in CLOAKED_CLASSES:
        return True
    for p in CLOAKED_CLASS_PREFIXES:
        if cls.startswith(p):
            return True
    return dwm_is_cloaked(hwnd)


if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    # ---------- 结构体 ----------
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    class RECT(ctypes.Structure):
        _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                    ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        def as_tuple(self):
            return self.left, self.top, self.right, self.bottom

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    GWL_STYLE = -16
    GWL_EXSTYLE = -20
    WS_VISIBLE = 0x10000000
    WS_EX_TOOLWINDOW = 0x00000080
    MONITOR_DEFAULTTONEAREST = 0x2

    _prev_cpu = None  # (idle, kernel, user)


def get_idle_seconds():
    """返回距上次键鼠输入的秒数。"""
    if not IS_WINDOWS:
        return 0.0
    li = LASTINPUTINFO()
    li.cbSize = ctypes.sizeof(LASTINPUTINFO)
    if not user32.GetLastInputInfo(ctypes.byref(li)):
        return 0.0
    # DWORD 回绕减法：用无符号运算
    delta = (kernel32.GetTickCount() - li.dwTime) & 0xFFFFFFFF
    return delta / 1000.0


def _filetime_to_int(ft):
    return (ft.dwHighDateTime << 32) | ft.dwLowDateTime


def cpu_percent():
    """两次调用之间的 CPU 占用率（0-100）。首次调用返回 None。"""
    if not IS_WINDOWS:
        return None
    idle = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    if not kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)):
        return None
    cur = (_filetime_to_int(idle), _filetime_to_int(kernel), _filetime_to_int(user))
    global _prev_cpu
    prev, _prev_cpu = _prev_cpu, cur
    if prev is None:
        return None
    idle_d = cur[0] - prev[0]
    total_d = (cur[1] - prev[1]) + (cur[2] - prev[2])
    if total_d <= 0:
        return None
    busy = total_d - idle_d
    return max(0.0, min(100.0, busy * 100.0 / total_d))


def trim_working_set():
    """把暂不使用的内存页归还给系统，降低桌宠常驻内存（安全、系统会按需再分页回来）。"""
    if not IS_WINDOWS:
        return
    try:
        h = kernel32.GetCurrentProcess()
        kernel32.SetProcessWorkingSetSize(h, ctypes.c_size_t(-1).value,
                                         ctypes.c_size_t(-1).value)
    except Exception:
        pass


def _window_title(hwnd):
    try:
        n = user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return ""
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        return buf.value or ""
    except Exception:
        return ""


def _window_class(hwnd):
    try:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf, 256)
        return buf.value or ""
    except Exception:
        return ""


def _window_rect(hwnd):
    r = RECT()
    if user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return r.as_tuple()
    return None


def foreground_info():
    """
    返回前台窗口信息：
    {hwnd, title, cls, rect:(l,t,r,b), monitor, workarea, fullscreen}
    非 Windows 返回 None。
    """
    if not IS_WINDOWS:
        return None
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    title = _window_title(hwnd)
    cls = _window_class(hwnd)
    rect = _window_rect(hwnd)
    mon_rect = work_rect = None
    try:
        hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            mon_rect = mi.rcMonitor.as_tuple()
            work_rect = mi.rcWork.as_tuple()
    except Exception:
        pass
    fullscreen = False
    if rect and mon_rect:
        ml, mt, mr, mb = mon_rect
        l, t, r, b = rect
        covered = (max(0, min(r, mr) - max(l, ml)) * max(0, min(b, mb) - max(t, mt)))
        mon_area = max(1, (mr - ml) * (mb - mt))
        # 覆盖显示器 98% 以上认为是全屏（排除桌面本身）
        fullscreen = (covered / mon_area) >= 0.98 and cls not in ("Progman", "WorkerW")
    return {
        "hwnd": hwnd, "title": title, "cls": cls,
        "rect": rect, "monitor": mon_rect, "workarea": work_rect,
        "fullscreen": fullscreen,
    }


def enum_window_rects(min_area=120000):
    """
    枚举可见的顶层普通窗口矩形（用于避让）。
    min_area 约 300x400（物理像素），过滤小工具窗口。
    返回 [(l, t, r, b, title), ...]
    过滤规则：工具窗/桌面/任务栏、UWP 透明宿主窗
    （Windows.UI.Core.CoreWindow，如「Windows 输入体验」，肉眼不可见却铺满屏幕）。
    注意：不能按 WS_EX_NOREDIRECTIONBITMAP 过滤——Chromium/Electron 窗口
    （VSCode/Trae/Chrome/Edge）都带该样式，它们是必须避让的真实窗口。
    """
    if not IS_WINDOWS:
        return []
    results = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if ex & WS_EX_TOOLWINDOW:
                return True
            title = _window_title(hwnd)
            cls = _window_class(hwnd)
            if is_transparent_host(hwnd, cls, ex):
                return True
            r = RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return True
            w, h = r.right - r.left, r.bottom - r.top
            if w * h < min_area:
                return True
            results.append((r.left, r.top, r.right, r.bottom, title))
        except Exception:
            pass
        return True

    user32.EnumWindows(cb, 0)
    return results


def enum_window_titles(min_area=4000):
    """宽松枚举所有可见窗口标题（供听歌检测，不过滤类名/工具窗）。

    与 enum_window_rects 的区别：不排除 UWP 透明宿主、工具窗，
    这样能抓到网易云/QQ音乐 UWP 版、Spotify 等播放器窗口。
    返回 [(title, cls), ...]，只过滤不可见和面积过小的窗口。
    """
    if not IS_WINDOWS:
        return []
    results = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            r = RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
                return True
            w, h = r.right - r.left, r.bottom - r.top
            if w * h < min_area:
                return True
            title = _window_title(hwnd)
            if not title:
                return True
            results.append((title, _window_class(hwnd)))
        except Exception:
            pass
        return True

    user32.EnumWindows(cb, 0)
    return results


def double_click_ms():
    """系统双击时间间隔（毫秒）。摸头延迟必须等过这个窗口，
    否则真人慢双击（250~500ms）会被拆成两次单击。"""
    if not IS_WINDOWS:
        return 500
    try:
        v = int(user32.GetDoubleClickTime())
        return v if 100 <= v <= 1000 else 500
    except Exception:
        return 500


# ---------------- 全局快捷键 ----------------

def keyseq_to_hotkey(text):
    """'Ctrl+Alt+D' -> (modifiers, vk)。无法识别主键返回 None。"""
    if not text:
        return None
    parts = [p.strip().upper() for p in text.replace(" ", "").split("+") if p.strip()]
    mod = 0
    vk = None
    for p in parts:
        if p in ("CTRL", "CONTROL"):
            mod |= MOD_CONTROL
        elif p == "ALT":
            mod |= MOD_ALT
        elif p == "SHIFT":
            mod |= MOD_SHIFT
        elif p in ("WIN", "META", "SUPER"):
            mod |= MOD_WIN
        elif p in VK_MAP:
            vk = VK_MAP[p]
        elif len(p) == 1 and ("0" <= p <= "9" or "A" <= p <= "Z"):
            vk = ord(p)
        elif p.startswith("F") and p[1:].isdigit() and 1 <= int(p[1:]) <= 24:
            vk = 0x70 + int(p[1:]) - 1
    if vk is None or mod == 0:  # 必须带修饰键，避免误抢普通按键
        return None
    return mod, vk


def register_hotkey(hotkey_id, text):
    """成功返回 True。"""
    if not IS_WINDOWS:
        return False
    parsed = keyseq_to_hotkey(text)
    if not parsed:
        return False
    mod, vk = parsed
    user32.UnregisterHotKey(None, hotkey_id)
    return bool(user32.RegisterHotKey(None, hotkey_id, mod, vk))


def unregister_hotkey(hotkey_id):
    if not IS_WINDOWS:
        return
    try:
        user32.UnregisterHotKey(None, hotkey_id)
    except Exception:
        pass


# ---------------- 系统深色模式 ----------------
DARK_THEME_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def system_dark_mode():
    """Windows 系统当前是否为深色模式。

    读取注册表 AppsUseLightTheme：0=深色，1=浅色。
    非 Windows / 读取失败返回 None（调用方按"保持默认"处理）。
    """
    if not IS_WINDOWS:
        return None
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, DARK_THEME_REG_PATH)
        try:
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        finally:
            winreg.CloseKey(key)
        return val == 0
    except Exception:
        return None


# ---------------- 开机自启 ----------------
AUTOSTART_REG_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_REG_NAME = "LineDogPet"


def _autostart_command():
    """构建开机自启的命令行：打包 exe 用自身路径，脚本用 pythonw + 入口脚本。"""
    import sys
    import os
    if getattr(sys, "frozen", False):
        return '"%s"' % sys.executable
    # 脚本模式：用 pythonw（无控制台窗口）运行入口脚本
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    entry = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "智能桌宠.py")
    return '"%s" "%s"' % (pythonw, entry)


def set_autostart(enabled):
    """设置或取消开机自启（写入/删除注册表 Run 键）。"""
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, AUTOSTART_REG_PATH, 0,
            winreg.KEY_SET_VALUE | winreg.KEY_READ)
        if enabled:
            winreg.SetValueEx(key, AUTOSTART_REG_NAME, 0,
                              winreg.REG_SZ, _autostart_command())
        else:
            try:
                winreg.DeleteValue(key, AUTOSTART_REG_NAME)
            except FileNotFoundError:
                pass
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


def is_autostart():
    """读取注册表判断当前是否已设置开机自启。"""
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, AUTOSTART_REG_PATH, 0,
            winreg.KEY_READ)
        try:
            winreg.QueryValueEx(key, AUTOSTART_REG_NAME)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            winreg.CloseKey(key)
            return False
    except Exception:
        return False

# ---------------- 鼠标位置（恶作剧用）----------------

def cursor_pos():
    """返回 (x, y) 逻辑光标位置；非 Windows 返回 (None, None)。"""
    if not IS_WINDOWS:
        return None, None
    try:
        from ctypes import wintypes
        pt = wintypes.POINT()
        if user32.GetCursorPos(ctypes.byref(pt)):
            return pt.x, pt.y
    except Exception:
        pass
    return None, None


def set_cursor_pos(x, y):
    if not IS_WINDOWS:
        return
    try:
        user32.SetCursorPos(int(x), int(y))
    except Exception:
        pass

def left_mouse_pressed():
    """左键当前是否按住（GetAsyncKeyState，轮询用，无钩子零开销）。"""
    if not IS_WINDOWS:
        return False
    try:
        return bool(user32.GetAsyncKeyState(0x01) & 0x8000)
    except Exception:
        return False


# 打字常用键的虚拟键码（排除鼠标键与纯修饰键，避免只按 Shift 就误判为打字）
TYPING_KEYS = (
    [0x08, 0x09, 0x0D, 0x20]            # Backspace / Tab / Enter / Space
    + list(range(0x30, 0x3A))           # 0-9
    + list(range(0x41, 0x5B))           # A-Z
    + list(range(0x60, 0x70))           # 小键盘 0-9 与运算符
    + list(range(0xBA, 0xC1))           # ; = , - . / `
    + list(range(0xDB, 0xE0))           # [ \ ] '
)


def any_key_pressed():
    """当前是否有打字键被按下（GetAsyncKeyState 轮询，无钩子零开销）。

    用于"跟随打字"：桌宠 / Bongo 面板据此判断用户是否正在敲键盘。
    只检测有内容的键，单独按 Shift/Ctrl/Alt 不算。
    """
    if not IS_WINDOWS:
        return False
    try:
        for vk in TYPING_KEYS:
            if user32.GetAsyncKeyState(vk) & 0x8000:
                return True
        return False
    except Exception:
        return False


# ---------------- Bongo 模式：左右手 + 键盘分区检测 ----------------
# 标准键位按"哪只手、哪一排"分组（VK 码）；修饰键/功能键归入同侧同排。
_HAND_KEYS = {
    ("left", "top"): (0x31, 0x32, 0x33, 0x34, 0x35,          # 1-5
                      0x51, 0x57, 0x45, 0x52, 0x54,          # Q W E R T
                      0xC0, 0x09),                            # ` ~ / Tab
    ("left", "home"): (0x41, 0x53, 0x44, 0x46, 0x47,         # A S D F G
                       0x14, 0xA0),                           # CapsLock / LShift
    ("left", "bottom"): (0x5A, 0x58, 0x43, 0x56, 0x42,      # Z X C V B
                         0xA2, 0xA4, 0x5B),                   # LCtrl / LAlt / LWin
    ("right", "top"): (0x36, 0x37, 0x38, 0x39, 0x30,         # 6-0
                       0x59, 0x55, 0x49, 0x4F, 0x50,         # Y U I O P
                       0xBD, 0xBB, 0x08),                     # - = / Backspace
    ("right", "home"): (0x48, 0x4A, 0x4B, 0x4C, 0xBA, 0xDE,  # H J K L ; '
                        0xDB, 0xDD, 0xDC, 0x0D, 0xA1),        # [ ] \ / Enter / RShift
    ("right", "bottom"): (0x4E, 0x4D, 0xBC, 0xBE, 0xBF,     # N M , . /
                          0xA3, 0xA5, 0x5C,                   # RCtrl / RAlt / RWin
                          0x25, 0x28, 0x26, 0x27),            # 方向键
}
_SPACE_VK = 0x20

# 键码 -> 键名（供"图片覆盖按键"编辑页与 framemap 使用；无条目=无名修饰键）
VK_NAME = {
    0xC0: "`", 0x09: "Tab", 0x31: "1", 0x32: "2", 0x33: "3", 0x34: "4", 0x35: "5",
    0x51: "Q", 0x57: "W", 0x45: "E", 0x52: "R", 0x54: "T",
    0x41: "A", 0x53: "S", 0x44: "D", 0x46: "F", 0x47: "G", 0x14: "Caps", 0xA0: "LSh",
    0x5A: "Z", 0x58: "X", 0x43: "C", 0x56: "V", 0x42: "B", 0xA2: "LCt", 0xA4: "LAl", 0x5B: "LWn",
    0x36: "6", 0x37: "7", 0x38: "8", 0x39: "9", 0x30: "0",
    0x59: "Y", 0x55: "U", 0x49: "I", 0x4F: "O", 0x50: "P", 0xBD: "-", 0xBB: "=", 0x08: "Bksp",
    0x48: "H", 0x4A: "J", 0x4B: "K", 0x4C: "L", 0xBA: ";", 0xDE: "'",
    0xDB: "[", 0xDD: "]", 0xDC: "\\", 0x0D: "Enter", 0xA1: "RSh",
    0x4E: "N", 0x4D: "M", 0xBC: ",", 0xBE: ".", 0xBF: "/",
    0xA3: "RCt", 0xA5: "RAl", 0x5C: "RWn",
    0x25: "Lft", 0x28: "Ddn", 0x26: "Up", 0x27: "Rgt",
    0x20: "space",
}

# 所有打字键的 (name, side, row)；side ∈ left/right，row ∈ top/home/bottom；空格单列。
KEYS_INFO = []
for (_side, _row), _vks in _HAND_KEYS.items():
    for _vk in _vks:
        if _vk in VK_NAME:
            KEYS_INFO.append((VK_NAME[_vk], _side, _row))
KEYS_INFO.append(("space", "both", "space"))


def pressed_hands():
    """轮询当前按下的键属于哪只手、哪一排。返回 (left, right, space)。

    left / right 为 None 或 'top' / 'home' / 'bottom'；space 为 bool。
    Bongo 面板据此决定抬左爪 / 右爪 / 双爪，以及敲键盘的哪个区域。
    按住不放（自动重复）会持续返回按下状态，天然支持长按连敲。
    """
    if not IS_WINDOWS:
        return None, None, False
    left = right = None
    try:
        gs = user32.GetAsyncKeyState
        for (side, row), vks in _HAND_KEYS.items():
            if any(gs(vk) & 0x8000 for vk in vks):
                if side == "left":
                    left = left or row
                else:
                    right = right or row
        space = bool(gs(_SPACE_VK) & 0x8000)
        return left, right, space
    except Exception:
        return None, None, False


def pressed_key_names():
    """返回当前按下的所有打字键键名列表（含 'space'），顺序按 KEYS_INFO。
    键名 -> 图片 的帧映射（smartpet/framemap.py）据此选帧。"""
    if not IS_WINDOWS:
        return []
    names = []
    try:
        gs = user32.GetAsyncKeyState
        vk_of = {n: vk for vk, n in VK_NAME.items()}
        for name, _side, _row in KEYS_INFO:
            vk = vk_of.get(name)
            if vk is not None and (gs(vk) & 0x8000):
                names.append(name)
    except Exception:
        return []
    return names


# ---------------- 窗口鼠标穿透 / 进程存活探测 ----------------

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000


def set_click_through(hwnd, on=True):
    """系统级鼠标穿透：让点击穿过本窗口落到桌面/其他应用。

    Qt 的 WA_TransparentForMouseEvents 只在 Qt 进程内部生效，顶层窗口
    必须再带 WS_EX_TRANSPARENT 扩展样式，鼠标消息才会真正穿透到下层。
    """
    if not IS_WINDOWS or not hwnd:
        return
    try:
        import ctypes as _c
        user32.GetWindowLongW.restype = _c.c_long
        user32.GetWindowLongW.argtypes = (_c.c_void_p, _c.c_int)
        user32.SetWindowLongW.restype = _c.c_long
        user32.SetWindowLongW.argtypes = (_c.c_void_p, _c.c_int, _c.c_long)
        hwnd = int(hwnd)
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        if on:
            style |= WS_EX_TRANSPARENT | WS_EX_LAYERED | WS_EX_NOACTIVATE
        else:
            style &= ~WS_EX_TRANSPARENT
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    except Exception:
        pass


# ---------------- 桌面层窗口定位（壁纸之上、图标与普通窗口之下） ----------------

def _desktop_icons_root():
    """返回承载桌面图标的顶层窗口句柄（包含 SHELLDLL_DefView 的 Progman/WorkerW）。

    找不到返回 0。失败/非 Windows 返回 0。
    """
    if not IS_WINDOWS:
        return 0
    try:
        found = [0]

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def on_child(child, _lp):
            if _window_class(child) == "SHELLDLL_DefView":
                found[0] = child
                return False
            return True

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def on_top(hwnd, _lp):
            if _window_class(hwnd) in ("Progman", "WorkerW"):
                user32.EnumChildWindows(hwnd, on_child, 0)
                if found[0]:
                    return False
            return True

        user32.EnumWindows(on_top, 0)
        return found[0]
    except Exception:
        return 0


def pin_above_wallpaper(hwnd):
    """把顶层窗口钉到"桌面层"：壁纸之上、桌面图标与所有普通窗口之下。

    实现：把窗口插到图标层（SHELLDLL_DefView 的顶层祖先）正下方
    （SetWindowPos 的 hWndInsertAfter 语义是被插窗口排在参照窗口之下）。
    壁纸宿主 WorkerW 比图标层更低，因此本窗口始终盖在壁纸上、
    被任何应用窗口遮挡 —— 即"只在桌面显示"。失败返回 False。
    """
    if not IS_WINDOWS or not hwnd:
        return False
    try:
        import ctypes as _c
        user32.SetWindowPos.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = (
            wintypes.HWND, wintypes.HWND, _c.c_int, _c.c_int,
            _c.c_int, _c.c_int, _c.c_uint)
        root = _desktop_icons_root()
        after = root if root else 1  # 找不到图标层则退化为 Z 序最底
        SWP_NOSIZE, SWP_NOMOVE = 0x0001, 0x0002
        SWP_NOACTIVATE, SWP_NOOWNERZORDER = 0x0010, 0x0200
        return bool(user32.SetWindowPos(
            int(hwnd), after, 0, 0, 0, 0,
            SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE | SWP_NOOWNERZORDER))
    except Exception:
        return False


def mutex_exists(name):
    """检测命名互斥体是否存在 → 判断对应程序是否在运行。

    零协议、零端口的跨进程存活感知：对方进程退出（含崩溃）时互斥体
    由系统自动回收，检测立即变 False。
    """
    if not IS_WINDOWS:
        return False
    try:
        import ctypes as _c
        SYNCHRONIZE = 0x00100000
        kernel32.OpenMutexW.restype = _c.c_void_p
        kernel32.OpenMutexW.argtypes = (_c.c_ulong, _c.c_int, _c.c_wchar_p)
        kernel32.CloseHandle.restype = _c.c_int
        kernel32.CloseHandle.argtypes = (_c.c_void_p,)
        h = kernel32.OpenMutexW(SYNCHRONIZE, False, name)
        if h:
            kernel32.CloseHandle(h)
            return True
        return False
    except Exception:
        return False
