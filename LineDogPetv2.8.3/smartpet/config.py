# -*- coding: utf-8 -*-
"""
极简本地配置：单个几十 KB 以内的 config.json。
优先放在程序目录；不可写时退回 %APPDATA%\\SmartPet。
"""
import json
import os
import shutil
import sys
import tempfile
import traceback

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# PyInstaller 打包后：资源目录是运行期解压出的 _MEIPASS（含 pets/logo），
# 可写目录（config.json 落盘处）改为 exe 同级目录，避免配置写进临时目录丢失。
if getattr(sys, "frozen", False):
    APP_DIR = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    EXE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    EXE_DIR = APP_DIR
CONFIG_NAME = "config.json"
APP_VERSION = "2.8.3"
APP_NAME = "LineDogPet"
APP_CN_NAME = "线条小狗狗桌宠"
APP_AUTHOR = "楚明昊"
APP_EMAIL = "3174546481@qq.com"
APP_WEBSITE = "https://serve.kesug.com/"
# 三个代码托管平台的 releases 页面
RELEASE_GITHUB = "https://github.com/Mrjiumeng/LineDogPet/releases/"
RELEASE_GITEE = "https://gitee.com/chu-minghao-666/LineDogPet/releases"
RELEASE_GITCODE = "https://gitcode.com/2401_84201371/LineDogPet/releases/"
# 检查更新：依次尝试以下源，第一个成功即用（Gitee→GitCode→GitHub）
UPDATE_CHECK_URLS = [
    "https://gitee.com/api/v5/repos/chu-minghao-666/LineDogPet/releases/latest",
    "https://gitcode.com/api/v5/repos/2401_84201371/LineDogPet/releases/latest",
    "https://api.github.com/repos/Mrjiumeng/LineDogPet/releases/latest",
]
# 兼容旧代码：单 URL（实际用 UPDATE_CHECK_URLS 多源轮询）
UPDATE_CHECK_URL = UPDATE_CHECK_URLS[0]
# 兼容旧代码：检查更新时打开的发布页（优先用 Gitee，国内访问更快）
UPDATE_PAGE = RELEASE_GITEE

# —— 插件仓库（Gitee）：大家在这里发布 / 下载插件增量包 ——
# 仓库结构约定：index.json（插件索引）+ plugins/*.zip（各插件增量包）
PLUGIN_REPO_URL = "https://gitee.com/chu-minghao-666/line-dog-desktop-pet-plugin"
# 插件索引候选 raw 地址（master/main 分支依次尝试）
PLUGIN_INDEX_URLS = [
    PLUGIN_REPO_URL + "/raw/master/index.json",
    PLUGIN_REPO_URL + "/raw/main/index.json",
]
# 插件开发规范（仓库里的文档页，设置→插件 页有入口）
PLUGIN_GUIDE_URL = PLUGIN_REPO_URL + "/blob/master/%E6%8F%92%E4%BB%B6%E5%BC%80%E5%8F%91%E8%A7%84%E8%8C%83.md"


def parse_version_tag(tag):
    """从 release tag_name 提取版本号，兼容 v1.0.1 / LineDogPet-v1.5.1 / 1.5.1 等。"""
    import re
    m = re.search(r"(\d+\.\d+(?:\.\d+)?)", tag or "")
    return m.group(1) if m else ""

# 帧率档位 -> QMovie 速度百分比（疲劳/睡眠时还会再打折）
FPS_PRESETS = {"smooth": 100, "balanced": 80, "normal": 60, "saver": 45}

DEFAULT_CONFIG = {
    # —— 形象 / 显示 ——
    "character": "xiaobai",     # xiaobai=小白 / xiaojimao=小鸡毛
    "size": 150,                # 宠物长边像素
    "fps_mode": "balanced",     # smooth / balanced / normal / saver
    "bubble_color": "#222222",  # 气泡文字颜色
    "bubble_on": True,          # 字幕/气泡总开关
    "bubble_duration": 4200,    # 气泡显示时长（毫秒）

    # —— 音效 ——
    "sound_on": True,
    "hourly_chime": False,      # 整点轻轻叫一声

    # —— 鼠标交互 ——
    "mouse_interaction": True,
    "click_squash_on": True,    # 点击 Q 弹动画（单击压扁回弹 / 双击弹跳）
    "follow_mode": "off",       # off / sticky(粘人) / timid(胆小)
    "cursor_follow": False,     # 鼠标跟随宠：小狗当鼠标皮肤跟在光标右下角
    "cursor_custom_gif": "",    # 鼠标跟随宠自定义表情路径（空=跟随主宠物）
    "cursor_size": 75,          # 鼠标跟随宠长边像素（30~150）

    # —— 系统感知（纯本地）——
    "sense_enabled": True,
    "sense_idle": True,         # 键鼠闲置
    "sense_window": True,       # 全屏 / 编辑器 / 浏览器
    "sense_cpu": True,          # CPU 负载
    "sense_clipboard": True,    # 剪贴板文本
    "sense_time": True,         # 昼夜节律 / 整点

    # —— AI（可选，用户自带 Key）——
    "ai_enabled": False,
    "ai_provider": "deepseek",  # deepseek / doubao / openai / custom
    "ai_base": "https://api.deepseek.com/v1",
    "ai_key": "",
    "ai_model": "deepseek-chat",
    "ai_context": True,         # 把本地状态作为上下文发给用户自己的 API
    "ai_personas": {},          # 每个形象自定义 AI 人设：{形象id: 人设文本}，空则用内置默认

    # —— 窗口 / 桌面 ——
    "avoid_windows": True,      # 智能避让窗口
    "fullscreen_shrink": True,  # 全屏时自动缩小躲边角
    "lock_corner": "",          # "" 不锁定 / tl tr bl br
    "roam_enabled": False,      # 空闲时随机在空白处走动

    # —— 全局快捷键 ——
    "hotkey_on": True,
    "hotkey": "Ctrl+Alt+D",

    # —— 开机自启 ——
    "autostart": True,          # 开机时自动启动（默认开启）

    # —— 桌宠形态（v2.3.0）——
    "classic_enabled": True,    # 桌宠模式（经典桌宠）开关；与 bongo 可共存
    "bongo_enabled": False,     # Bongo 模式（趴在键盘上跟随打字）开关
    "pet_count": 1,             # 小狗数量（1~15；启动强制重置为 1，不持久生效）
    "pet_mode": "classic",      # 兼容旧配置字段（迁移到 classic_enabled/bongo_enabled 后不再使用）
    "bongo_mirror": True,       # bongo：水平镜像素材（新素材亦"面对面"，默认开；开关供切换）
    "bongo_click_through": False,  # bongo：鼠标穿透（点击穿过窗口到桌面，不可拖动）
    "bongo_size": 440,          # bongo：显示宽度（像素），高度按素材比例自动算
    "bongo_opacity": 1.0,       # bongo：窗口透明度 0.3~1.0

    # —— 脚印与步数（v1.3.0）——
    "paw_enabled": True,        # 移动时在桌面留淡脚印
    "paw_fade_sec": 6,          # 脚印消失时长（秒）
    "paw_steps_today": 0,       # 当日步数（像素距离换算）
    "paw_steps_date": "",       # 步数所属日期，跨日清零

    # —— 夜间陪伴 / 观察你 / 听歌 / 恶作剧 / 截图（v1.3.0）——
    "night_enabled": True,      # 深夜按时段说陪伴台词
    "observe_enabled": True,    # 按窗口/进程规则吐槽陪伴
    "music_enabled": True,      # 检测播放器并互动
    "prank_enabled": False,     # 偶尔轻度恶作剧总开关（默认关）
    "prank_mouse": False,       # 偷偷挪鼠标一点点（默认关）
    "shot_enabled": True,       # 截图后显示缩略小气泡

    # —— 插件系统（本体只带框架，插件为可单独下载的增量包）——
    "plugins_disabled": [],     # 被禁用的插件 id 列表（已安装但不显示在右键菜单）
    "plugins_pet_menu": True,   # 右键小狗便捷显示插件菜单（默认开，可关）

    # —— 桌面特效（v2.2.0，与动态壁纸视觉联动）——
    "fx_enabled": True,         # 特效总开关
    "fx_fps": 30,               # 特效帧率：15/24/30/60
    "fx_halo": True,            # 小狗光环（心情缩放+呼吸）
    "fx_step": True,            # 脚步粒子（步进冒光点/花瓣屑）
    "fx_mouse_trail": True,     # 鼠标吸引粒子
    "fx_click_burst": True,     # 点击爆炸
    "fx_cursor_light": True,    # 鼠标光源
    "fx_shadow": True,          # 脚下软阴影
    "fx_petals": False,         # 花瓣互动（小狗走过吹起）
    "fx_leaves": False,         # 落叶互动
    "fx_fireflies": False,      # 萤火虫聚集
    "fx_butterfly": False,      # 蝴蝶追踪
    "fx_trail": True,           # 能量尾巴（高速拖曳光带）
    "fx_music": True,           # 音乐律动（听歌时节拍粒子）


    # —— 运行时持久化 ——
    "pos": None,                # [x, y]
    "mood": 80.0,
    "energy": 80.0,

    # —— 外观主题（v1.3.6）——
    "theme_color": "#ff96bb",   # 主题色（默认淡粉色）
    "theme_radius": 8,          # 控件圆角半径
    "panel_opacity": 1.0,       # 面板透明度 0.5~1.0
    "pet_opacity": 1.0,         # 宠物透明度 0.3~1.0（默认不透明）
    "dark_mode": False,         # 软件深色模式（首次启动自动跟随系统）
}

# 不写入文件的易变项（这里仅作说明，所有键目前都可持久化）
_RUNTIME_KEYS = ()


def _stable_data_dir():
    """跨版本更新稳定不变的用户数据目录。

    固定放在 %APPDATA%\\LineDogPet（与安装目录、运行权限无关）。
    修复：此前 config.json / diary.db 等随 exe 同级目录存放，用户升级换目录、
    覆盖安装、或用管理员/普通权限交替运行（可写目录在两处之间跳变）时，
    程序会读到另一个空目录，导致「收藏册/日记更新后清空」。
    """
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    if base:
        return os.path.join(base, "LineDogPet")
    return EXE_DIR


# 旧版本可能残留用户数据的位置（安装目录 / 老版 %APPDATA%\\SmartPet）
_LEGACY_DATA_FILES = ("config.json", "diary.db", "diary.db-shm",
                      "diary.db-wal", "chat_history.db")
_LEGACY_DATA_DIRS = ("plugins", "plugin_data")
_data_migrated = False


def _legacy_data_dirs():
    dirs = [EXE_DIR]
    appdata = os.environ.get("APPDATA")
    if appdata:
        dirs.append(os.path.join(appdata, "SmartPet"))
    return dirs


def _migrate_legacy_data():
    """一次性把散落在旧位置的用户数据搬迁到稳定数据目录（仅复制、不删除）。

    覆盖：配置、日记收藏（diary.db）、聊天记录、插件与插件存档。
    目标目录已存在同名文件时以目标为准，绝不覆盖，避免二次启动把新数据冲掉。
    """
    global _data_migrated
    if _data_migrated:
        return
    _data_migrated = True
    dest = _stable_data_dir()
    try:
        os.makedirs(dest, exist_ok=True)
    except Exception:
        return
    for src in _legacy_data_dirs():
        if os.path.abspath(src) == os.path.abspath(dest):
            continue
        for fn in _LEGACY_DATA_FILES:
            sp, dp = os.path.join(src, fn), os.path.join(dest, fn)
            if os.path.isfile(sp) and not os.path.exists(dp):
                try:
                    shutil.copy2(sp, dp)
                except Exception:
                    pass
        for dn in _LEGACY_DATA_DIRS:
            sp, dp = os.path.join(src, dn), os.path.join(dest, dn)
            if os.path.isdir(sp) and not os.path.exists(dp):
                try:
                    shutil.copytree(sp, dp)
                except Exception:
                    pass


def _candidate_paths():
    # 首选稳定数据目录（更新/换权限后不变）；其次 exe 同级与旧版目录做兼容回退
    paths = [os.path.join(_stable_data_dir(), CONFIG_NAME),
             os.path.join(EXE_DIR, CONFIG_NAME)]
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(os.path.join(appdata, "SmartPet", CONFIG_NAME))
    paths.append(os.path.join(tempfile.gettempdir(), "smartpet_config.json"))
    seen, out = set(), []
    for p in paths:
        ap = os.path.abspath(p)
        if ap not in seen:
            seen.add(ap)
            out.append(p)
    return out


def _writable(path):
    try:
        d = os.path.dirname(path) or "."
        os.makedirs(d, exist_ok=True)
        f = os.path.join(d, ".write_test")
        with open(f, "w") as fp:
            fp.write("x")
        os.remove(f)
        return True
    except Exception:
        return False


_RESOLVED_CONFIG = None


def _config_path():
    """读写统一解析到同一个路径（结果缓存）。

    优先级：① 已存在且可写 → ② 可写（用于首次创建）→ ③ 已存在但不可写
    （只读旧数据）。修复：装到 Program Files 时，首次以管理员运行把
    config.json 写进了安装目录，之后普通权限运行"读旧文件、写不进去还
    静默失败"，导致改完设置重启恢复默认。
    """
    global _RESOLVED_CONFIG
    if _RESOLVED_CONFIG:
        return _RESOLVED_CONFIG
    _migrate_legacy_data()
    cands = _candidate_paths()
    for p in cands:
        if os.path.exists(p) and _writable(p):
            _RESOLVED_CONFIG = p
            return p
    for p in cands:
        if _writable(p):
            _RESOLVED_CONFIG = p
            return p
    for p in cands:
        if os.path.exists(p):
            _RESOLVED_CONFIG = p
            return p
    _RESOLVED_CONFIG = cands[-1]
    return _RESOLVED_CONFIG


def load_config():
    """读取配置；缺项自动用默认值补齐。"""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝
    data = {}
    try:
        path = _config_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fp:
                data = json.load(fp) or {}
    except Exception:
        data = {}
    # 旧版形象键迁移：pet_kind/skin → character（golden→小鸡毛，其余→小白）
    if "character" not in data and data.get("skin") == "golden":
        data["character"] = "xiaojimao"
    # 深色模式：首次运行无此配置时跟随系统（用户可在设置里手动切换并持久化）
    if "dark_mode" not in data:
        try:
            from . import winapi
            sysdark = winapi.system_dark_mode()
            if sysdark is not None:
                cfg["dark_mode"] = bool(sysdark)
        except Exception:
            pass
    for k, v in data.items():
        if k in cfg:
            cfg[k] = v
    # 类型兜底
    cfg["dark_mode"] = bool(cfg.get("dark_mode", False))
    cfg["size"] = int(_clamp(cfg.get("size", 150), 80, 260))
    cfg["bubble_duration"] = int(_clamp(cfg.get("bubble_duration", 4200), 1500, 12000))
    cfg["mood"] = _clamp(float(cfg.get("mood", 80)), 0, 100)
    cfg["energy"] = _clamp(float(cfg.get("energy", 80)), 0, 100)
    if cfg.get("fps_mode") not in FPS_PRESETS:
        cfg["fps_mode"] = "balanced"
    if cfg.get("follow_mode") not in ("off", "sticky", "timid"):
        cfg["follow_mode"] = "sticky"
    # character：默认角色与用户自定义角色都合法（素材文件夹由 pets/ 自动发现）
    ch = cfg.get("character")
    if not isinstance(ch, str) or not ch.strip() or ch.strip() == "shared":
        cfg["character"] = "xiaobai"
    else:
        cfg["character"] = ch.strip()
    # AI 人设兜底：必须是 {形象id: 文本} 字典
    if not isinstance(cfg.get("ai_personas"), dict):
        cfg["ai_personas"] = {}
    # 插件禁用列表兜底：必须是字符串列表
    if not isinstance(cfg.get("plugins_disabled"), list):
        cfg["plugins_disabled"] = []
    cfg["plugins_pet_menu"] = bool(cfg.get("plugins_pet_menu", True))
    cfg["roam_enabled"] = bool(cfg.get("roam_enabled", True))
    # 外观主题兜底
    cfg["theme_radius"] = int(_clamp(cfg.get("theme_radius", 8), 0, 20))
    cfg["panel_opacity"] = round(_clamp(float(cfg.get("panel_opacity", 1.0)), 0.5, 1.0), 2)
    cfg["pet_opacity"] = round(_clamp(float(cfg.get("pet_opacity", 1.0)), 0.3, 1.0), 2)
    cfg["cursor_size"] = int(_clamp(cfg.get("cursor_size", 75), 30, 150))
    tc = str(cfg.get("theme_color", "#ff96bb"))
    cfg["theme_color"] = tc if tc.startswith("#") and len(tc) in (7, 9) else "#ff96bb"
    # 开机自启：让注册表与配置保持一致（设置里的勾选框以注册表为准）。
    # 默认 autostart=True，全新安装首次启动就会真正写入注册表、勾选框默认勾上；
    # 用户在设置里取消会同时写回 config=False + 删注册表，之后保持关闭。
    try:
        from . import winapi
        _want = bool(cfg.get("autostart", False))
        if winapi.is_autostart() != _want:
            winapi.set_autostart(_want)
    except Exception:
        pass
    return cfg


def save_config(cfg):
    """原子写入；首选路径写失败时自动改投其它可写候选并记住，
    绝不让"改了不保存"静默发生（桌宠也不能因存配置崩溃）。"""
    global _RESOLVED_CONFIG
    path = _config_path()
    paths = [path] + [p for p in _candidate_paths() if p != path]
    for p in paths:
        try:
            os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fp:
                json.dump(cfg, fp, ensure_ascii=False, indent=1)
            os.replace(tmp, p)
            _RESOLVED_CONFIG = p   # 下次读写都用这个成功的路径
            return
        except Exception:
            continue
    try:
        with open(os.path.join(tempfile.gettempdir(), "smartpet_crash.log"), "a", encoding="utf-8") as fp:
            fp.write("\n--- save_config 全部失败 ---\n" + traceback.format_exc())
    except Exception:
        pass


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def app_icon_path():
    for name in ("logo.png", "logo.ico"):
        p = os.path.join(APP_DIR, name)
        if os.path.exists(p):
            return p
    return ""

def data_dir():
    """用户数据目录（跨版本更新稳定不变），用于 diary.db、chat_history.db、
    插件与插件存档等持久化文件。

    固定放在 %APPDATA%\\LineDogPet，与安装目录和运行权限解耦，
    避免覆盖安装 / 解压新版 / 权限跳变导致数据“消失”。
    """
    _migrate_legacy_data()
    d = _stable_data_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        return os.path.dirname(_config_path()) or EXE_DIR
    return d
