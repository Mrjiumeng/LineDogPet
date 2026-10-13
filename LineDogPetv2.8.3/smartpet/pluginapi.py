# -*- coding: utf-8 -*-
"""
插件系统（v2.5.x）：把「小游戏 / 增值功能」做成可单独下载的增量包，
本体只内置这套加载框架，不含任何插件代码与素材，因此本体体积不增长。

设计要点
--------
- 插件安装目录：``<data_dir>/plugins/<id>/``，每个插件是一个文件夹，
  内含清单 ``plugin.json`` 与入口 Python 模块（默认 ``main.py``）。
- 插件运行数据：``<data_dir>/plugin_data/<id>/``（卸载插件不删存档，
  除非用户勾选“同时删除存档”）。
- 插件协议：入口模块需暴露 ``create_view(ctx) -> QWidget``。返回的
  控件由本体负责作为独立窗口显示；插件通过 ``ctx.request_exit()``
  请求退出并回到桌宠。插件不直接碰桌宠窗口显隐，切换逻辑集中在本体。
- 「入口」：本体内置一份插件目录清单 ``KNOWN_PLUGINS``（可后续改为
  从服务器拉取）。未安装的插件在设置里显示为“可下载的入口”，点下载/
  安装后才把真正的增量包落到 plugins/ 目录，实现“不玩就不下”。

清单 plugin.json 字段
---------------------
    {
      "id":            "farm_game",        # 唯一标识（英文，作目录名）
      "name":          "种菜小游戏",        # 显示名
      "version":       "1.0.0",
      "author":        "…",
      "description":   "…",
      "entry":         "main.py",          # 入口模块（相对插件目录）
      "icon":          "icon.png",         # 可选，插件图标（相对插件目录）
      "menu_label":    "🥕 玩种菜小游戏",   # 右键小狗菜单里的文字
      "download_url":  "",                 # 可选，增量包 zip 直链
      "min_app":       "2.5.0"             # 可选，要求的最低本体版本
    }
"""
import os
import json
import shutil
import zipfile
import tempfile
import importlib.util

from . import config as cfgmod

MANIFEST = "plugin.json"

# 本体内置的“可下载插件目录”（只有元信息，不含代码/素材）。
# download_url 留空时，设置里会引导用户用“从本地文件安装”来装增量包。
KNOWN_PLUGINS = [
    {
        "id": "farm_game",
        "name": "种菜小游戏",
        "version": "1.1.0",
        "author": "LineDogPet",
        "description": "QQ 农场风格的桌面种菜小游戏：播种、浇水、收获、"
                       "卖钱升级。右键小狗即可从桌宠切换到游戏，随时返回。",
        "entry": "farmgame.py",
        "menu_label": "🥕 玩种菜小游戏",
        "download_url": "",   # TODO: 填你的增量包 zip 直链
    },
]


def plugins_root():
    return os.path.join(cfgmod.data_dir(), "plugins")


def plugin_data_root():
    return os.path.join(cfgmod.data_dir(), "plugin_data")


def fetch_remote_index(timeout=12):
    """从 Gitee 插件仓库拉取 index.json（社区插件索引）。

    返回 (entries, base_url)：entries 为条目列表，download_url 已补全为
    绝对地址（相对路径按索引所在目录解析）。失败抛出最后一次异常。
    """
    import urllib.request
    last_err = None
    for url in cfgmod.PLUGIN_INDEX_URLS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "LineDogPet"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(bytes(r.read()).decode("utf-8", "ignore"))
            base = url.rsplit("/", 1)[0] + "/"
            items = data.get("plugins") if isinstance(data, dict) else data
            out = []
            for it in items or []:
                if not isinstance(it, dict) or not it.get("id"):
                    continue
                e = dict(it)
                dl = str(e.get("file") or e.get("download_url") or "")
                if dl and not dl.startswith("http"):
                    dl = base + dl.lstrip("./")
                e["download_url"] = dl
                out.append(e)
            return out, base
        except Exception as ex:
            last_err = ex
    raise last_err or RuntimeError("无法获取插件索引")


def _safe_id(pid):
    """目录名安全过滤：只保留字母数字下划线连字符。"""
    return "".join(ch for ch in (pid or "") if ch.isalnum() or ch in "-_") or ""


# 打包发布时排除的内容（缓存/临时文件绝不进增量包）
_SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode", "temp", "tmp"}
_SKIP_EXTS = {".pyc", ".pyo", ".tmp", ".log"}


def make_release_zip(plugin_dir, dest_zip):
    """把插件文件夹打成发布用 zip（zip 根目录即插件内容，含 plugin.json）。

    返回 dest_zip 绝对路径。排除 __pycache__ / *.pyc 等。
    """
    plugin_dir = os.path.abspath(plugin_dir)
    if not os.path.isfile(os.path.join(plugin_dir, MANIFEST)):
        raise ValueError("该文件夹缺少 plugin.json，不是合法插件目录")
    dest_zip = os.path.abspath(dest_zip)
    d = os.path.dirname(dest_zip)
    if d:
        os.makedirs(d, exist_ok=True)
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(plugin_dir):
            dirs[:] = [x for x in dirs if x not in _SKIP_DIRS]
            for fn in files:
                if os.path.splitext(fn)[1].lower() in _SKIP_EXTS:
                    continue
                full = os.path.join(root, fn)
                arc = os.path.relpath(full, plugin_dir)
                z.write(full, arc)
    return dest_zip


class PluginInfo:
    """一个已安装插件的元信息 + 磁盘路径。"""

    def __init__(self, path, meta):
        self.path = path
        self.id = str(meta.get("id") or os.path.basename(path))
        self.name = str(meta.get("name") or self.id)
        self.version = str(meta.get("version") or "")
        self.author = str(meta.get("author") or "")
        self.description = str(meta.get("description") or "")
        self.entry = str(meta.get("entry") or "main.py")
        self.icon = str(meta.get("icon") or "")
        self.menu_label = str(meta.get("menu_label") or ("▶ " + self.name))
        self.download_url = str(meta.get("download_url") or "")
        self.min_app = str(meta.get("min_app") or "")

    @property
    def icon_path(self):
        return os.path.join(self.path, self.icon) if self.icon else ""

    @property
    def entry_path(self):
        return os.path.join(self.path, self.entry)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "version": self.version,
            "author": self.author, "description": self.description,
            "menu_label": self.menu_label, "download_url": self.download_url,
        }


class PluginContext:
    """传给插件的运行时上下文：只暴露必要能力，隔离桌宠内部实现。"""

    def __init__(self, manager, info, cfg, hooks):
        self._manager = manager
        self.info = info
        self.cfg = cfg
        self.id = info.id
        self.name = info.name
        # hooks 由本体注入：say / play_sound / request_exit / theme
        self._hooks = hooks

    # —— 供插件使用的公共 API ——
    @property
    def state_dir(self):
        d = os.path.join(plugin_data_root(), _safe_id(self.id))
        os.makedirs(d, exist_ok=True)
        return d

    @property
    def asset_dir(self):
        return os.path.join(self.info.path, "assets")

    @property
    def theme_color(self):
        return self.cfg.get("theme_color", "#ff96bb")

    @property
    def dark(self):
        return bool(self.cfg.get("dark_mode", False))

    @property
    def radius(self):
        return int(self.cfg.get("theme_radius", 8))

    def say(self, text):
        """让桌宠冒一句话泡（游戏运行时桌宠通常隐藏，会静默忽略）。"""
        fn = self._hooks.get("say")
        if fn:
            try:
                fn(text)
            except Exception:
                pass

    def play_sound(self, name="bark"):
        fn = self._hooks.get("play_sound")
        if fn:
            try:
                fn(name)
            except Exception:
                pass

    def request_exit(self):
        """插件请求退出游戏、回到桌宠。"""
        fn = self._hooks.get("request_exit")
        if fn:
            fn(self.id)


class PluginManager:
    """扫描/安装/卸载/加载插件；本体在启动时创建一个实例并常驻。"""

    def __init__(self, cfg, hooks=None):
        self.cfg = cfg
        self.hooks = hooks or {}
        self._installed = {}     # id -> PluginInfo
        self._active = {}        # id -> (view, handle) 正在运行的插件窗口
        self._remote = []        # 在线仓库索引条目（refresh_remote 填充）
        self._remote_note = ""   # 最近一次拉取结果说明
        os.makedirs(plugins_root(), exist_ok=True)
        os.makedirs(plugin_data_root(), exist_ok=True)
        self.rescan()

    # ---------------- 在线仓库 ----------------
    def refresh_remote(self):
        """拉取 Gitee 仓库的社区插件索引；成功返回条目数，失败抛异常。"""
        entries, _base = fetch_remote_index()
        self._remote = entries
        self._remote_note = "在线仓库：已加载 %d 个社区插件" % len(entries)
        return len(entries)

    @property
    def remote_entries(self):
        return list(self._remote)

    @property
    def remote_note(self):
        return self._remote_note

    # ---------------- 扫描 ----------------
    def rescan(self):
        self._installed = {}
        root = plugins_root()
        for name in os.listdir(root) if os.path.isdir(root) else []:
            d = os.path.join(root, name)
            mf = os.path.join(d, MANIFEST)
            if not os.path.isdir(d) or not os.path.isfile(mf):
                continue
            try:
                with open(mf, "r", encoding="utf-8") as fp:
                    meta = json.load(fp) or {}
            except Exception:
                continue
            meta.setdefault("id", name)
            info = PluginInfo(d, meta)
            self._installed[info.id] = info
        return self._installed

    def installed(self):
        return list(self._installed.values())

    def get(self, pid):
        return self._installed.get(pid)

    # ---------------- 启用/禁用 ----------------
    def _disabled_set(self):
        return set(self.cfg.get("plugins_disabled") or [])

    def is_enabled(self, pid):
        return pid not in self._disabled_set()

    def set_enabled(self, pid, on):
        dis = self._disabled_set()
        if on:
            dis.discard(pid)
        else:
            dis.add(pid)
        self.cfg["plugins_disabled"] = sorted(dis)

    # ---------------- 目录（在线仓库 + 内置 + 本地已装） ----------------
    def _entry(self, pid, meta, source):
        """把一份元信息 + 本地安装状态合成设置页要用的条目。"""
        info = self._installed.get(pid)
        return {
            "id": pid,
            "name": meta.get("name") or (info.name if info else pid),
            "version": (info.version if info else meta.get("version", "")) or "",
            "author": meta.get("author", "") or (info.author if info else ""),
            "description": meta.get("description", "") or (info.description if info else ""),
            "download_url": meta.get("download_url", "") or (info.download_url if info else ""),
            "installed": info is not None,
            "enabled": (info is not None and self.is_enabled(pid)),
            "icon_path": info.icon_path if info else "",
            "source": source,      # remote 社区 / builtin 内置 / local 本地自装
        }

    def catalog(self):
        """在线仓库条目优先，其次内置清单，最后本地自装但不在清单里的。"""
        entries = []
        seen = set()
        for meta in self._remote:
            pid = _safe_id(meta.get("id"))
            if not pid or pid in seen:
                continue
            seen.add(pid)
            entries.append(self._entry(pid, meta, "remote"))
        for meta in KNOWN_PLUGINS:
            pid = _safe_id(meta.get("id"))
            if not pid or pid in seen:
                continue
            seen.add(pid)
            entries.append(self._entry(pid, meta, "builtin"))
        for pid, info in self._installed.items():
            if pid in seen:
                continue
            seen.add(pid)
            entries.append(self._entry(pid, info.to_dict(), "local"))
        return entries

    def menu_entries(self):
        """右键小狗菜单里可启动的插件（已安装且已启用）。"""
        out = []
        for pid, info in self._installed.items():
            if self.is_enabled(pid):
                out.append((pid, info.menu_label))
        return out

    # ---------------- 安装 ----------------
    def install_from_path(self, src):
        """从本地 zip 或已解压文件夹安装。返回 PluginInfo；失败抛异常。"""
        src = os.path.abspath(src)
        tmp = None
        try:
            if os.path.isdir(src):
                base = src
            elif zipfile.is_zipfile(src):
                tmp = tempfile.mkdtemp(prefix="plugzip_")
                with zipfile.ZipFile(src) as z:
                    z.extractall(tmp)
                base = self._find_manifest_root(tmp)
            else:
                raise ValueError("请选择插件 zip 包或已解压的插件文件夹")
            mf = os.path.join(base, MANIFEST)
            if not os.path.isfile(mf):
                raise ValueError("包里找不到 plugin.json 清单")
            with open(mf, "r", encoding="utf-8") as fp:
                meta = json.load(fp) or {}
            pid = _safe_id(meta.get("id"))
            if not pid:
                raise ValueError("plugin.json 缺少合法 id")
            entry = str(meta.get("entry") or "main.py")
            if not os.path.isfile(os.path.join(base, entry)):
                raise ValueError("清单入口模块 %s 不存在" % entry)
            dest = os.path.join(plugins_root(), pid)
            if os.path.exists(dest):
                shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(base, dest)
            self.rescan()
            return self._installed.get(pid)
        finally:
            if tmp:
                shutil.rmtree(tmp, ignore_errors=True)

    @staticmethod
    def _find_manifest_root(tmp):
        """zip 可能多一层顶级目录，定位含 plugin.json 的那层。"""
        if os.path.isfile(os.path.join(tmp, MANIFEST)):
            return tmp
        for name in os.listdir(tmp):
            d = os.path.join(tmp, name)
            if os.path.isdir(d) and os.path.isfile(os.path.join(d, MANIFEST)):
                return d
        return tmp

    def install_from_url(self, url):
        """下载 zip 直链并安装（同步，调用方放后台线程）。"""
        import urllib.request
        import urllib.error
        if not url:
            raise ValueError("该插件未配置下载地址，请改用“从本地文件安装”")
        tmp = tempfile.mkdtemp(prefix="plugdl_")
        zp = os.path.join(tmp, "pkg.zip")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "LineDogPet"})
            with urllib.request.urlopen(req, timeout=30) as r, open(zp, "wb") as f:
                shutil.copyfileobj(r, f)
            return self.install_from_path(zp)
        except urllib.error.HTTPError as e:
            raise ValueError(
                "下载失败 HTTP %d（%s）\n\n下载地址：\n%s\n\n"
                "常见原因：仓库 plugins/ 目录里还没有这个 zip，或 index.json 里 "
                "file 字段的名字和实际上传的文件名不一致（含版本号）。"
                % (e.code, url.split("/")[2], url))
        except Exception as e:
            raise ValueError("下载失败：%s\n地址：%s" % (e, url))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    # ---------------- 卸载 ----------------
    def uninstall(self, pid, purge_data=False):
        self.close_plugin(pid)
        info = self._installed.get(pid)
        if info and os.path.isdir(info.path):
            shutil.rmtree(info.path, ignore_errors=True)
        if purge_data:
            dd = os.path.join(plugin_data_root(), _safe_id(pid))
            shutil.rmtree(dd, ignore_errors=True)
        dis = self._disabled_set()
        dis.discard(pid)
        self.cfg["plugins_disabled"] = sorted(dis)
        self.rescan()

    # ---------------- 启动 / 关闭 ----------------
    def is_active(self, pid):
        return pid in self._active

    def create_context(self, info):
        return PluginContext(self, info, self.cfg, self.hooks)

    def launch(self, pid):
        """加载插件视图并作为独立窗口显示；返回 True 表示成功。"""
        info = self._installed.get(pid)
        if not info:
            return False
        if pid in self._active:
            self._active[pid][0].show()
            self._active[pid][0].raise_()
            return True
        try:
            spec = importlib.util.spec_from_file_location(
                "smartpet_plugin_%s" % _safe_id(pid), info.entry_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception:
            import traceback
            traceback.print_exc()
            return False
        create = getattr(mod, "create_view", None)
        if not callable(create):
            return False
        ctx = self.create_context(info)
        try:
            view = create(ctx)
        except Exception:
            import traceback
            traceback.print_exc()
            return False
        if view is None:
            return False
        self._active[pid] = (view, ctx)
        # 视图关闭时清理登记（用户点窗口 X / 或 request_exit 触发 close）
        view.destroyed.connect(lambda *_: self._forget(pid))
        view.show()
        view.raise_()
        view.activateWindow()
        return True

    def _forget(self, pid):
        self._active.pop(pid, None)

    def close_plugin(self, pid):
        entry = self._active.get(pid)
        if not entry:
            return
        view = entry[0]
        self._active.pop(pid, None)
        try:
            view.close()
            view.deleteLater()
        except Exception:
            pass

    def close_all(self):
        for pid in list(self._active.keys()):
            self.close_plugin(pid)
