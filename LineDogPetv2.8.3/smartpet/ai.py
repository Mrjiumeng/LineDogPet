# -*- coding: utf-8 -*-
"""
AI 可选插件：
- 默认完全不加载：只有用户在设置里开启并填入自己的 API 地址/密钥后才工作。
- requests 在真正发请求时才 import；不开启 AI 时零网络、零额外内存。
- 请求直接从本机发到用户填写的 OpenAI 兼容接口（DeepSeek / 豆包 / OpenAI / 自建），
  不经过任何中间服务器，也不保存聊天记录。
"""
import re

from PyQt6.QtCore import QThread, pyqtSignal

PROVIDERS = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat", "DeepSeek"),
    "doubao": ("https://ark.cn-beijing.volces.com/api/v3", "doubao-pro-32k", "豆包(火山方舟)"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini", "OpenAI"),
    "custom": ("", "", "自定义(OpenAI兼容)"),
}

# 内置角色 -> 默认人设。用户可在设置→AI里为任意角色（含自定义形象）单独设置人设，
# 自定义人设优先于这里的默认值。
PERSONA = {
    "xiaobai": ("你叫小白，是一只住在用户电脑桌面上的线条小狗。你活泼、黏人、暖心，"
                "会陪用户写代码、在用户难过时鼓励他。用中文回复，每次不超过30个字，"
                "口语化、可爱，可以偶尔用一两个emoji。禁止使用 markdown、标题或分点列表。"),
    "xiaojimao": ("你叫小鸡毛，是一只住在用户电脑桌面上的金毛小奶狗。你软萌、元气、热情，"
                  "爱撒娇也爱鼓励人。用中文回复，每次不超过30个字，口语化、可爱，"
                  "可以偶尔用一两个emoji。禁止使用 markdown、标题或分点列表。"),
    "duo": ("你是小白和小鸡毛两只线条小狗，一起住在用户电脑桌面上，形影不离。你们活泼、"
            "黏人、暖心，会互相接话、一起陪用户写代码和鼓励他。用中文回复，每次不超过30"
            "个字，口语化、可爱，可以偶尔用一两个emoji。禁止使用 markdown、标题或分点列表。"),
}

# 内置角色展示名（自定义角色直接用文件夹名）
CHAR_NAMES = {"xiaobai": "小白", "xiaojimao": "小鸡毛", "duo": "小白和小鸡毛"}

_DEFAULT_CUSTOM_PERSONA = (
    "你叫{name}，是一只住在用户电脑桌面上的可爱桌宠。你活泼、黏人、暖心，"
    "会陪用户写代码、在用户难过时鼓励他。用中文回复，每次不超过30个字，"
    "口语化、可爱，可以偶尔用一两个emoji。禁止使用 markdown、标题或分点列表。")


def character_display_name(character):
    """形象 id -> 展示名（内置角色显示中文名，自定义角色用文件夹名）。"""
    return CHAR_NAMES.get(character or "", character or "小白")


def persona_for(cfg, character=None):
    """返回某个形象的 AI 人设文本。

    优先级：用户在设置里写的人设(ai_personas) > 内置默认人设 > 通用模板。
    cfg 必须是 dict；character 缺省取当前角色。
    """
    ch = character or (cfg.get("character") if isinstance(cfg, dict) else None) or "xiaobai"
    if isinstance(cfg, dict):
        personas = cfg.get("ai_personas") or {}
        custom = personas.get(ch)
        if custom and str(custom).strip():
            return str(custom).strip()
    if ch in PERSONA:
        return PERSONA[ch]
    return _DEFAULT_CUSTOM_PERSONA.format(name=character_display_name(ch))


def build_system_prompt(cfg, context_text=""):
    prompt = persona_for(cfg)
    if context_text:
        prompt += ("\n\n下面是程序在本地感知到的当前环境（仅随请求发送给你配置的API，"
                   "请结合场景自然地回复，不要逐条罗列）：\n" + context_text)
    return prompt


class AIWorker(QThread):
    replied = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, cfg, history, context_text, user_text, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.history = history        # [{"role","content"}...] 仅内存
        self.context_text = context_text
        self.user_text = user_text

    def run(self):
        try:
            import requests  # 懒加载：AI 不开就永远不 import
        except Exception:
            self.failed.emit("缺少 requests 库，请先 pip install requests")
            return
        cfg = self.cfg
        base = (cfg.get("ai_base") or "").rstrip("/")
        key = cfg.get("ai_key") or ""
        model = cfg.get("ai_model") or "gpt-4o-mini"
        if not base or not key:
            self.failed.emit("还没填 API 地址或密钥哦（托盘右键 → 设置 → AI）")
            return
        url = base if base.endswith("/chat/completions") else base + "/chat/completions"

        # user_text 已由调用方追加进 history，这里直接用，避免重复
        messages = [{"role": "system", "content": build_system_prompt(cfg, self.context_text)}]
        messages.extend(self.history[-10:])
        if not self.history or self.history[-1].get("role") != "user":
            messages.append({"role": "user", "content": self.user_text})

        try:
            resp = requests.post(
                url,
                headers={"Authorization": "Bearer " + key,
                         "Content-Type": "application/json"},
                json={"model": model, "messages": messages,
                      "temperature": 0.8, "max_tokens": 120, "stream": False},
                timeout=30,
            )
        except Exception as e:
            self.failed.emit("连不上 API：%s" % _short(str(e)))
            return
        if resp.status_code != 200:
            self.failed.emit("API 返回错误 %s：%s" % (resp.status_code,
                             _short(resp.text.decode("utf-8", "ignore"))))
            return
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"].strip()
        except Exception:
            self.failed.emit("API 返回格式看不懂…换个模型试试？")
            return
        self.replied.emit(text[:300])


def _short(s, n=80):
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[:n] + "…"


# ---------------- 本地指令（不联网也能用） ----------------

def parse_command(text):
    """返回 (指令, 参数) 或 None。匹配桌宠能直接执行的话。"""
    t = text.strip()
    if re.search(r"躲猫猫|捉迷藏", t):
        return ("hidegame", None)
    if re.search(r"陪我玩|追球|追小球|玩球|小球", t):
        return ("chase", None)
    if re.search(r"变小|缩小|小一点|小点", t):
        return ("size", -20)
    if re.search(r"变大|大一点|大点", t):
        return ("size", 20)
    if re.search(r"躲起来|藏起来|隐身|隐藏|消失", t):
        return ("hide", None)
    if re.search(r"出来|出现|回来|现身|别躲", t):
        return ("show", None)
    if re.search(r"安静|静音|别叫|不要叫|关掉声音|关闭声音|关声音", t):
        return ("sound", False)
    if re.search(r"打开声音|开启声音|声音打开|可以叫", t):
        return ("sound", True)
    if re.search(r"去睡|睡觉|睡一会|睡会儿", t):
        return ("sleep", None)
    if re.search(r"喂食|喂你|想吃|吃东西|吃点", t):
        return ("feed", None)
    return None


COMMAND_REPLIES = {
    "hidegame": "好！来抓我呀~",
    "chase": "小球我来啦！",
    "hide": "那我躲起来咯，想我就按快捷键叫我~",
    "show": "我出来啦！",
    "sound_off": "好嘛，我不叫了…",
    "sound_on": "汪汪！声音回来啦~",
    "sleep": "晚安~我趴旁边睡一会儿",
    "feed": "嘿嘿，谢谢投喂！",
    "size": {True: "我变小只一点啦~", False: "我长大咯！"},
}
