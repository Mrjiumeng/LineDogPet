# -*- coding: utf-8 -*-
"""Bongo 帧播放的"图片 -> 覆盖按键"映射。

运行期(bongopet.py)与编辑页(bongo_frames_editor.py)共用本模块：
- 默认分类：按左右手/排布自动把每个键分到对应帧（与老版 FRAME_MAP 手感一致）。
- 自定义：编辑页保存 pets/bongo/bongo_frames.json，形如
      {"idle": "idle.png",
       "frames": {"hit_left.png": ["A", "S", ...], "my_pic.png": ["Q"], ...}}
  frames 按字典顺序优先匹配：按下的键落在哪张图的列表里就播那张图。
- 没被任何图认领的键 -> 回退到该键默认分类的帧；文件都不存在则回退 idle。
- load() 按文件 mtime 判断变化才重读，运行期每帧调用近零开销，
  编辑页改完保存，桌宠无需重启即时生效。
"""
import json
import os

from . import config as cfgmod
from . import winapi

DIR = os.path.join(cfgmod.APP_DIR, "pets", "bongo")
MAP_PATH = os.path.join(DIR, "bongo_frames.json")


def _default_frames():
    """按键表自动分类：(手,排) -> 候选帧，取每帧实际存在的文件。"""
    groups = {}
    for name, side, row in winapi.KEYS_INFO:
        if name == "space":
            continue
        groups.setdefault((side, row), []).append(name)
    groups[("both", "space")] = ["space"]
    cand = {
        ("left", "top"): "hit_left_top.png",
        ("left", "home"): "hit_left.png",
        ("left", "bottom"): "hit_left_bottom.png",
        ("right", "top"): "hit_right_top.png",
        ("right", "home"): "hit_right.png",
        ("right", "bottom"): "hit_right_bottom.png",
        ("both", "space"): "hit_space.png",
    }
    fallback = {
        ("left", "top"): "hit_left.png", ("left", "home"): "hit_left.png",
        ("left", "bottom"): "hit_left.png",
        ("right", "top"): "hit_right.png", ("right", "home"): "hit_right.png",
        ("right", "bottom"): "hit_right.png",
        ("both", "space"): "hit_right.png",
    }

    def pick(g):
        # 仅当该组对应的标准帧存在才算数；都不存在则此组不自动分类
        # （自定义命名的素材交给编辑页手动分配，绝不兜底塞给 idle 冒充"拥有全部键"）
        for fn in (cand[g], fallback[g]):
            if os.path.exists(os.path.join(DIR, fn)):
                return fn
        return None

    frames = {}
    for g in (("left", "top"), ("left", "home"), ("left", "bottom"),
              ("right", "top"), ("right", "home"), ("right", "bottom"),
              ("both", "space")):
        fn = pick(g)
        if fn:
            frames.setdefault(fn, [])
            frames[fn].extend(groups[g])
    return frames


IDLE_DEFAULT = "idle.png"

_map = None                      # 当前生效映射 {"idle":..., "frames": {...}}
_mtime = None                    # bongo_frames.json 的 mtime（None=不存在）


def load(force=False):
    """读取映射；JSON 不存在用默认分类；损坏回退默认。mtime 变化才重读。"""
    global _map, _mtime
    try:
        mt = os.path.getmtime(MAP_PATH)
    except OSError:
        mt = None
    if _map is not None and not force and mt == _mtime:
        return _map
    _mtime = mt
    if mt is None:
        _map = {"idle": IDLE_DEFAULT, "frames": _default_frames()}
    else:
        try:
            with open(MAP_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            frames = {str(k): [str(n) for n in (v or [])]
                      for k, v in (data.get("frames") or {}).items()}
            _map = {"idle": str(data.get("idle") or IDLE_DEFAULT),
                    "frames": frames}
        except Exception:
            _map = {"idle": IDLE_DEFAULT, "frames": _default_frames()}
    return _map


def save(idle, frames):
    """编辑页保存自定义映射，桌宠按 mtime 即时拾取。"""
    global _map
    data = {"idle": idle, "frames": frames}
    tmp = MAP_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, MAP_PATH)
    load(force=True)


def reset_to_default():
    """删除自定义文件，回到自动分类。"""
    try:
        os.remove(MAP_PATH)
    except OSError:
        pass
    load(force=True)


def default_frames():
    return _default_frames()


def keys_of(frame):
    """某张图当前认领的键列表（供编辑页显示）。"""
    return list(load().get("frames", {}).get(frame, []))


def frame_for(name):
    """键名 -> 认领它的图；未被认领 -> 默认分类的图。"""
    m = load()
    for fn, keys in m["frames"].items():
        if name in keys:
            return fn
    for fn, keys in _default_frames().items():
        if name in keys:
            return fn
    return None


def held(names):
    """按下的键名列表 -> 应播放的帧文件名（空格优先，其余按映射先后）。"""
    if not names:
        return None
    m = load()
    if "space" in names:
        fn = frame_for("space")
        if fn:
            return fn
    for name in names:
        if name != "space":
            fn = frame_for(name)
            if fn:
                return fn
    return None


def idle_frame():
    return load()["idle"]


def all_keys():
    """全部可分配的键名（含 space）。"""
    return [n for n, _s, _r in winapi.KEYS_INFO]
