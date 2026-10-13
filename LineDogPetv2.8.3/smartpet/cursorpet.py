# -*- coding: utf-8 -*-
"""
鼠标跟随宠（鼠标皮肤）：把当前角色小狗缩小版固定在光标右下角跟着走。

- 置顶、无边框、无焦点、鼠标穿透，绝不遮挡/拦截鼠标主体；
- 只用角色自己的 idle 素材（不抽双狗共享池），画面是单纯一只狗；
- 靠近屏幕右下边缘时自动翻转到光标左上，避免跑出屏幕；
- 每 30ms 跟随一次，动画与主宠物同为即用即弃 QMovie。
"""
import os
from PyQt6.QtWidgets import QWidget, QLabel, QApplication
from PyQt6.QtCore import Qt, QTimer, QPoint, QSize
from PyQt6.QtGui import QMovie, QCursor


class CursorPet(QWidget):
    def __init__(self, assets, cfg, parent=None):
        super().__init__(parent)
        self.assets = assets
        self.cfg = cfg
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

        self._movie = None
        self._last_path = ""  # 记录上次渲染的路径，避免重复渲染
        self._label = QLabel(self)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._timer = QTimer(self)
        self._timer.setInterval(16)          # ~60fps 跟手，更丝滑
        self._timer.timeout.connect(self._follow)
        self._render()
        self._follow()

    def showEvent(self, ev):
        super().showEvent(ev)
        # 系统级穿透：鼠标皮肤不能挡住光标下的点击
        from . import winapi
        winapi.set_click_through(int(self.winId()), True)

    # ---------------- 动画 ----------------
    def _render(self):
        # 优先使用自定义表情
        custom_gif = self.cfg.get("cursor_custom_gif", "")
        if custom_gif and os.path.exists(custom_gif):
            path = custom_gif
        else:
            # 否则使用主宠物的idle表情
            ch = self.cfg.get("character", "xiaobai")
            path = self.assets.resolve_own("idle", ch)
        
        # 检查路径是否相同，避免不必要的重新渲染
        if path == self._last_path and self._movie is not None:
            return
            
        self._last_path = path
            
        if self._movie is not None:
            old = self._movie
            self._movie = None
            self._label.clear()
            try:
                old.frameChanged.disconnect()
            except Exception:
                pass
            old.stop()
            old.deleteLater()
            
        if not path:
            self.resize(int(self.cfg.get("cursor_size", 75)) + 8, 40)
            return
            
        movie = QMovie(path)
        # 用默认缓存模式（不要 CacheNone）：CacheNone 会破坏带透明/分帧
        # 局部更新的 GIF 帧合成，偶发出现黑底花屏。
        movie.jumpToFrame(0)
        img = movie.currentImage()
        w0, h0 = img.width(), img.height()
        if w0 <= 0 or h0 <= 0:
            w0 = h0 = 300
        le = max(30, int(self.cfg.get("cursor_size", 75)))   # 鼠标跟随宠显示高度
        # 统一高度：固定高度、宽度按比例，与主宠物保持一致的观感
        nh = le
        nw = max(1, int(w0 * le / h0))
        # 高清渲染：超采样+DPR，保证在 125%/150% 缩放下表情依然清晰
        try:
            dpr = self.devicePixelRatioF() or 1.0
        except Exception:
            dpr = 1.0
        ss = max(2, int(round(dpr)))
        movie.setScaledSize(QSize(nw * ss, nh * ss))
        movie.setSpeed(100)  # 恢复正常速度，避免过快导致抽搐
        self._label.setGeometry(0, 0, nw, nh)
        self._label.clear()

        def _apply_frame(_pm=None):
            pm = movie.currentPixmap()
            if not pm.isNull():
                pm.setDevicePixelRatio(ss)
                self._label.setPixmap(pm)

        movie.frameChanged.connect(_apply_frame)
        _apply_frame()
        movie.start()
        self._movie = movie
        self._src_w, self._src_h = w0, h0   # 记住源帧尺寸，_rescale 用
        self._render_ss = ss
        self.resize(nw, nh)

    def _rescale(self):
        """只缩放当前动画显示尺寸，不重新加载 GIF（拖大小滑块时用）。"""
        m = self._movie
        if m is None:
            return
        w0 = getattr(self, "_src_w", 0)
        h0 = getattr(self, "_src_h", 0)
        if w0 <= 0 or h0 <= 0:
            return
        le = max(30, int(self.cfg.get("cursor_size", 75)))
        nh = le
        nw = max(1, int(w0 * le / h0))
        ss = getattr(self, "_render_ss", 2)
        m.setScaledSize(QSize(nw * ss, nh * ss))
        self._label.setGeometry(0, 0, nw, nh)
        pm = m.currentPixmap()
        if not pm.isNull():
            pm.setDevicePixelRatio(ss)
            self._label.setPixmap(pm)
        self.resize(nw, nh)

    def set_character(self, ch):
        """切换角色（设置面板实时预览/保存后调用）。"""
        self.cfg["character"] = ch or "xiaobai"
        self._last_path = ""  # 强制重新渲染
        self._render()

    def set_size(self, size):
        """调整鼠标跟随宠大小（长边像素，30~150）。"""
        size = max(30, min(150, int(size)))
        if size == self.cfg.get("cursor_size", 75):
            return
        self.cfg["cursor_size"] = size
        self._rescale()   # 只缩放不重载，动画不跳
        self._follow()

    def force_rerender(self):
        """强制重新渲染（用于自定义表情上传后）。"""
        self._last_path = ""
        self._render()

    # ---------------- 开关 / 跟随 ----------------
    def set_enabled(self, on):
        if on:
            self._render()
            self.show()
            self.raise_()
            self._follow()
            self._timer.start()
        else:
            self._timer.stop()
            self.hide()

    def _follow(self):
        pos = QCursor.pos()
        screen = QApplication.screenAt(pos) or QApplication.primaryScreen()
        if screen is None:
            return
        geo = screen.availableGeometry()
        w, h = self.width(), self.height()
        dx, dy = 10, 12                       # 右下偏移，不遮住光标热点
        if pos.x() + dx + w > geo.right() - 4:
            dx = -w - 10                      # 靠右边缘：翻到光标左边
        if pos.y() + dy + h > geo.bottom() - 4:
            dy = -h - 12                      # 靠下边缘：翻到光标上面
        x = max(geo.left(), min(geo.right() - w, pos.x() + dx))
        y = max(geo.top(), min(geo.bottom() - h, pos.y() + dy))
        # 使用 setPosition 代替 move，减少重绘
        self.move(x, y)