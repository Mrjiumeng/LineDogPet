# -*- coding: utf-8 -*-
"""
小狗日记 + 里程碑《第一次》（全部本地 SQLite，无服务依赖）。

- diary.db 与 config.json 同级，备份该文件即可迁移全部数据。
- events 表：当日交互事件（同类行为当日只记一次，mood/energy 快照用于日记）。
- firsts 表：各类「第一次」事件（同事件仅保存首次发生记录）。
- meta 表：首次启动日（相识基准 Day）、里程碑入口解锁状态、已解锁称号。

日记文案在打开日记页面时实时拼接生成，不做固定预生成。
"""
import os
import sqlite3
import time
import traceback
from datetime import date, datetime, timedelta

from . import config as cfgmod

DB_FILE = "diary.db"

# 交互事件类型 -> 中文名（日记/里程碑通用）
EVENT_LABELS = {
    "pet":   "摸了摸我的头",
    "feed":  "给我投喂美食",
    "play":  "陪我玩耍",
    "chase": "和我追小球",
    "hide":  "和我玩躲猫猫",
    "chin":  "给我挠下巴",
    "spin":  "看我转圈圈",
    "call":  "呼唤我",
    "talk":  "和我说话",
    "chat":  "和我聊天",
    "found": "跟着我捡到小宝贝",
}

# 每天日记最多展示的事件条数（避免一天写太多）
DIARY_MAX_EVENTS = 4

# 事件类型 -> 小狗口吻日记句子（简短自然，渐进式累积）
EVENT_SENTENCES = {
    "pet":   "主人摸了摸我的头，好舒服呀。",
    "feed":  "主人给我喂了好吃的，吧唧吧唧。",
    "play":  "主人陪我玩了一会儿，超开心！",
    "chase": "一起追小球，跑得我舌头都伸出来啦。",
    "hide":  "躲猫猫被主人找到了，嘿嘿~",
    "chin":  "主人挠我下巴，舒服得眯起眼。",
    "spin":  "我转圈圈给主人看，转晕啦。",
    "call":  "主人一叫我，我就蹦过去啦。",
    "talk":  "主人和我说了悄悄话。",
    "chat":  "主人和我聊了天，我摇尾巴回应。",
    "found": "今天在外头捡到了小宝贝，叼回来给主人~",
}

# 里程碑集齐数量 -> 解锁福利称号（按达标数量顺序）
REWARD_TIERS = [
    (3, "贴心小跟班"),
    (5, "最佳拍档"),
    (8, "铲屎官认证"),
    (10, "永远的家人"),
]

_WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]


class DiaryDB:
    """本地 SQLite：日记 + 里程碑。所有方法内部自开自关连接，单线程 UI 足够。"""

    def __init__(self, path=None):
        self.path = path or os.path.join(cfgmod.data_dir(), DB_FILE)
        self._con = None

    # ---------------- 连接 / 建表 ----------------
    def _conn(self):
        if self._con is None:
            try:
                os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
                self._con = sqlite3.connect(self.path)
                self._con.execute("PRAGMA journal_mode=WAL")
                self._init_tables()
            except Exception as e:
                traceback.print_exc()
                raise e
        return self._con

    def _init_tables(self):
        c = self._con
        c.execute(
            "CREATE TABLE IF NOT EXISTS events("
            " date TEXT NOT NULL, kind TEXT NOT NULL, ts INTEGER NOT NULL,"
            " mood REAL, energy REAL,"
            " PRIMARY KEY(date, kind))")
        c.execute(
            "CREATE TABLE IF NOT EXISTS firsts("
            " kind TEXT PRIMARY KEY, date TEXT NOT NULL, ts INTEGER NOT NULL)")
        c.execute(
            "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)")
        # 狗狗收藏册：每件物品累积计数 + 首次/最近捡到日期
        c.execute(
            "CREATE TABLE IF NOT EXISTS collection("
            " item TEXT PRIMARY KEY, count INTEGER NOT NULL DEFAULT 0,"
            " first_date TEXT NOT NULL, last_date TEXT NOT NULL)")
        c.commit()
        if not self._meta_get("first_day"):
            self._meta_set("first_day", _today_str())

    # ---------------- meta 辅助 ----------------
    def _meta_get(self, key, default=None):
        row = self._conn().execute(
            "SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def _meta_set(self, key, value):
        self._conn().execute(
            "INSERT INTO meta(key,value) VALUES(?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)))
        self._conn().commit()

    # ---------------- 公共接口 ----------------
    def initialize(self):
        """确保库存在且 meta 记录首次启动日（相识基准 Day）。"""
        self._conn()
        return self

    def first_day(self):
        s = self._meta_get("first_day") or _today_str()
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return date.today()

    def day_index(self, date_str=None, _d=None):
        """返回 date_str 是相识后的第几天（Day 1 = 首次启动当天）。"""
        d = _d or _parse_date(date_str)
        return (d - self.first_day()).days + 1

    def record(self, kind, mood=None, energy=None):
        """记录一次交互：
        1) events 按 (date, kind) 去重，同类行为当日只记一次；
        2) firsts 同事件仅保存首次发生记录。
        """
        if kind not in EVENT_LABELS:
            return
        today = _today_str()
        now = int(time.time())
        c = self._conn()
        c.execute(
            "INSERT OR IGNORE INTO events(date,kind,ts,mood,energy)"
            " VALUES(?,?,?,?,?)",
            (today, kind, now, mood, energy))
        c.execute(
            "INSERT OR IGNORE INTO firsts(kind,date,ts) VALUES(?,?,?)",
            (kind, today, now))
        c.commit()

    def events_on(self, date_str):
        """某天发生的事件 kind 列表，按时间排序。"""
        rows = self._conn().execute(
            "SELECT kind, mood, energy FROM events WHERE date=?"
            " ORDER BY ts", (date_str,)).fetchall()
        return [{"kind": r[0], "mood": r[1], "energy": r[2]} for r in rows]

    def has_events_on(self, date_str):
        return bool(self._conn().execute(
            "SELECT 1 FROM events WHERE date=? LIMIT 1", (date_str,)).fetchone())

    def all_event_dates(self):
        rows = self._conn().execute(
            "SELECT DISTINCT date FROM events ORDER BY date").fetchall()
        return [r[0] for r in rows]

    # ---------------- 日记（打开页面时实时拼接） ----------------
    def diary_text(self, date_str):
        """打开日记页面时实时拼接生成小狗口吻的当日日记。
        无交互返回空串（由 UI 显示"今天还没有故事"）；有交互则简短渐进式记录。
        """
        d = _parse_date(date_str)
        day_n = self.day_index(_d=d)
        weekday = _WEEKDAYS[d.weekday()]
        events = self.events_on(date_str)
        if not events:
            return ""  # 没有交互就不写

        title = f"Day {day_n} · {d.year}年{d.month}月{d.day}日 星期{weekday}"
        opening = (f"汪！今天是我来到你身边的第 {day_n} 天。"
                   if date_str == _today_str() else f"那是第 {day_n} 天。")

        # 限制每日条数，多了概括
        shown = events[:DIARY_MAX_EVENTS]
        body_parts = ["· " + EVENT_SENTENCES.get(e["kind"], "今天也有新鲜事。")
                      for e in shown]
        if len(events) > DIARY_MAX_EVENTS:
            body_parts.append(
                f"· 今天还发生了好多别的事（{len(events) - DIARY_MAX_EVENTS} 件）…")

        # 情绪总结（简短）
        mood_vals = [e["mood"] for e in events if e.get("mood") is not None]
        mood_line = "今天心情大好！" if (mood_vals and sum(mood_vals) / len(mood_vals) >= 70) \
            else "今天很满足~"

        return f"{title}\n\n{opening}\n\n" \
               f"{chr(10).join(body_parts)}\n\n{mood_line}\n🐾 你的小狗"

    def has_diary(self, date_str):
        """该日是否有日记内容（有交互才有）。"""
        return self.has_events_on(date_str)

    # ---------------- 里程碑 ----------------
    def firsts(self):
        rows = self._conn().execute(
            "SELECT kind,date,ts FROM firsts ORDER BY ts").fetchall()
        return [{"kind": r[0], "date": r[1], "ts": r[2]} for r in rows]

    def firsts_count(self):
        return self._conn().execute("SELECT COUNT(*) FROM firsts").fetchone()[0]

    def total_kinds(self):
        return len(EVENT_LABELS)

    def milestone_unlocked(self):
        return self._meta_get("milestone_unlocked", "0") == "1"

    def unlock_milestone(self):
        """连续点击【关于】5 次后解锁隐藏入口。"""
        if not self.milestone_unlocked():
            self._meta_set("milestone_unlocked", "1")

    def current_title(self):
        """当前已达标称号；未达标返回 None。"""
        n = self.firsts_count()
        title = None
        for need, name in REWARD_TIERS:
            if n >= need:
                title = name
        return title

    def next_reward(self):
        """下一个待解锁称号 (还需数量, 称号) ；已全部解锁返回 None。"""
        n = self.firsts_count()
        for need, name in REWARD_TIERS:
            if n < need:
                return need - n, name
        return None

    def check_rewards(self):
        """里程碑集齐指定数量后返回本次新解锁的称号列表（自动记录防重复提示）。"""
        n = self.firsts_count()
        got = []
        for need, name in REWARD_TIERS:
            if n >= need and self._meta_get("reward_" + name, "0") != "1":
                self._meta_set("reward_" + name, "1")
                got.append(name)
        return got

    # ---------------- 成就徽章（里程碑页面展示） ----------------
    def streak_days(self):
        """连续陪伴天数：以今天（或昨天）为结尾，往前数连续有陪伴记录的日数。

        陪伴记录来自 events 表的日期去重；若无任何记录返回 0。
        """
        rows = self._conn().execute(
            "SELECT DISTINCT date FROM events ORDER BY date DESC").fetchall()
        if not rows:
            return 0
        days = {r[0] for r in rows}
        start = _today_str()
        if start not in days:
            start = (date.today() - timedelta(days=1)).isoformat()
            if start not in days:
                return 0
        n = 0
        d = _parse_date(start)
        while d.isoformat() in days:
            n += 1
            d -= timedelta(days=1)
        return n

    def egg_count(self):
        """彩蛋触发次数：连续点【关于】达 5 次计 1 次彩蛋。"""
        try:
            return int(self._meta_get("egg_count", "0"))
        except Exception:
            return 0

    def bump_egg(self):
        """记录一次彩蛋触发；若里程碑入口尚未解锁则一并解锁。"""
        n = self.egg_count() + 1
        self._meta_set("egg_count", str(n))
        if not self.milestone_unlocked():
            self._meta_set("milestone_unlocked", "1")
        return n

    def seen_outfits(self):
        """历史使用过的形象（服饰）列表。"""
        s = self._meta_get("outfits_seen", "") or ""
        return [x for x in s.split(",") if x]

    def record_outfit(self, ch):
        """记录一种形象（服饰）被使用过；返回已收集形象数。"""
        ch = str(ch or "").strip() or "xiaobai"
        seen = self.seen_outfits()
        if ch not in seen:
            seen.append(ch)
            self._meta_set("outfits_seen", ",".join(seen))
        return len(seen)

    def outfit_count(self):
        """已收集服饰（形象）件数。"""
        return len(self.seen_outfits())

    # ---------------- 狗狗收藏册（掉落物） ----------------
    def record_found(self, name, emoji=""):
        """记录小狗叼回一件掉落物：
        1) collection 表对应物品计数 +1；
        2) 当日 events 写一条 found（去重），firsts 记首次捡到；
        返回 (今日捡到件数, 该物品累计件数)。
        """
        name = str(name or "").strip() or "未知"
        today = _today_str()
        now = int(time.time())
        c = self._conn()
        c.execute(
            "INSERT INTO collection(item,count,first_date,last_date)"
            " VALUES(?,1,?,?)"
            " ON CONFLICT(item) DO UPDATE SET"
            " count=count+1, last_date=excluded.last_date",
            (name, today, today))
        c.execute(
            "INSERT OR IGNORE INTO events(date,kind,ts,mood,energy)"
            " VALUES(?,?,?,?,?)", (today, "found", now, None, None))
        c.execute(
            "INSERT OR IGNORE INTO firsts(kind,date,ts) VALUES(?,?,?)",
            ("found", today, now))
        c.commit()
        today_n = self.found_count_today()
        item_n = c.execute(
            "SELECT count FROM collection WHERE item=?", (name,)).fetchone()[0]
        return today_n, item_n

    def collection_list(self):
        """收藏册：[(item, count, first_date, last_date), ...] 按累计降序。"""
        rows = self._conn().execute(
            "SELECT item,count,first_date,last_date FROM collection"
            " ORDER BY count DESC, first_date ASC").fetchall()
        return [{"item": r[0], "count": r[1],
                 "first_date": r[2], "last_date": r[3]} for r in rows]

    def collection_total(self):
        """收藏册总件数（所有物品 count 之和）。"""
        row = self._conn().execute(
            "SELECT COALESCE(SUM(count),0) FROM collection").fetchone()
        return int(row[0]) if row else 0

    def found_count_today(self):
        """今天捡到的掉落物件数。"""
        row = self._conn().execute(
            "SELECT COALESCE(SUM(count),0) FROM collection"
            " WHERE last_date=?", (_today_str(),)).fetchone()
        return int(row[0]) if row else 0


# ---------------- 日期工具 ----------------
def _today_str():
    return date.today().isoformat()


def _parse_date(s):
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return date.today()


def _shift(s, delta_days):
    return (_parse_date(s) + timedelta(days=delta_days)).isoformat()


# ---------------- 模块级便捷接口 ----------------
_db = None


def get_db():
    global _db
    if _db is None:
        _db = DiaryDB().initialize()
    return _db


def record(kind, mood=None, energy=None):
    try:
        get_db().record(kind, mood, energy)
    except Exception:
        # 记录失败绝不能影响桌宠本体
        traceback.print_exc()