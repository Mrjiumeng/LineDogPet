# -*- coding: utf-8 -*-
"""
掉落物系统（小狗走过时偶尔掉下的小宝贝）：
- DROP_LIBRARY：可掉落物品种类（名称 + emoji）
- DroppedItem：置顶透明小窗，鼠标点击后由小狗来叼走；
  长时间没人捡会自动消失（expired 信号通知管理器）。
"""
import random

from PyQt6.QtWidgets import QWidget, QLabel
from PyQt6.QtCore import Qt, QTimer, pyqtSignal

DROP_LIBRARY = [
    ("星星", "⭐"), ("骨头", "🦴"), ("爱心", "❤️"), ("宝石", "💎"),
    ("金币", "🪙"), ("月亮", "🌙"), ("蝴蝶", "🦋"), ("蘑菇", "🍄"),
    ("枫叶", "🍁"), ("小鱼", "🐟"), ("铃铛", "🔔"), ("彩蛋", "🥚"),
    ("蜗牛", "🐌"), ("花朵", "🌸"),
]

LIFETIME_MS = 180000  # 无人捡 3 分钟自动消失


def random_item():
    """随机返回一件物品 (名称, emoji)。"""
    return random.choice(DROP_LIBRARY)


class DroppedItem(QWidget):
    """掉落在地的小东西：点击后触发 picked 信号，由小狗叼回去。"""

    picked = pyqtSignal(object)   # 参数为本窗口
    expired = pyqtSignal(object)  # 超时未被捡走

    _FLOAT_AMP = 3

    def __init__(self, name, emoji, center_x, center_y, parent=None):
        super().__init__(parent)
        self.item = (name, emoji)
        self._picked = False
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.resize(54, 54)
        self._label = QLabel(emoji, self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setGeometry(0, 0, 54, 54)
        # 半透明圆底：既衬托 emoji，又让整个圆形区域 alpha>0 可接收鼠标点击
        # （WA_TranslucentBackground 窗口透明区域会鼠标穿透，只有 alpha>0 的像素能命中）
        self._label.setStyleSheet(
            "font:26pt 'Segoe UI Emoji','Microsoft YaHei';"
            "background: rgba(255,250,245,210); border-radius: 27px;")
        self.move(int(center_x - 27), int(center_y - 27))
        self._anchor_y = self.y()
        self._offset = 0
        self._dir = 1

        self._pulse = QTimer(self)
        self._pulse.setInterval(110)
        self._pulse.timeout.connect(self._float_step)
        self._pulse.start()

        self._life = QTimer(self)
        self._life.setSingleShot(True)
        self._life.timeout.connect(self._expire)
        self._life.start(LIFETIME_MS)

        self.show()
        self.raise_()

    def _float_step(self):
        self._offset += self._dir
        if self._offset > self._FLOAT_AMP or self._offset < -self._FLOAT_AMP:
            self._dir *= -1
            self._offset = max(-self._FLOAT_AMP,
                               min(self._FLOAT_AMP, self._offset))
        self.move(self.x(), self._anchor_y + self._offset)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and not self._picked:
            self._picked = True
            self._pulse.stop()
            self.picked.emit(self)
            e.accept()
            return
        super().mousePressEvent(e)

    def collect(self):
        """被小狗叼走时调用：停止动画并销毁窗口。"""
        self._picked = True
        self._pulse.stop()
        self._life.stop()
        self.hide()
        self.deleteLater()

    def _expire(self):
        self._pulse.stop()
        if not self._picked:
            self.expired.emit(self)
        self.hide()
        self.deleteLater()