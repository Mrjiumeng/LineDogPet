# -*- coding: utf-8 -*-
"""v2.2.0 双软件联动感知：通过命名互斥体探测对方进程是否在线。

零协议、零端口、零依赖：互斥体由系统在进程退出（含崩溃）时自动回收，
检测立即变 False，无需心跳/超时。桌宠探测 PawLiveWall，PawLiveWall 探测桌宠，
各自独立、互不依赖。
"""
from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from . import winapi

PAWLIVEWALL_MUTEX = "Local\\PawLiveWall_SingleInstance"
PET_MUTEX = "Global\\SmartPet_SingleInstance"


class LinkMonitor(QObject):
    """轮询探测目标互斥体，状态变化时发信号。"""

    linked = pyqtSignal()      # 目标从无到有（或启动时已在线）
    unlinked = pyqtSignal()    # 目标从有到无

    def __init__(self, target_mutex, interval_ms=2000, parent=None):
        super().__init__(parent)
        self._name = target_mutex
        self._online = False
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._tick)

    def start(self):
        # 启动即检测：若对方已在线，立即发 linked（满足"不管先开哪个都要提醒"）
        self._online = winapi.mutex_exists(self._name)
        self._timer.start()
        if self._online:
            self.linked.emit()

    def stop(self):
        self._timer.stop()

    def online(self):
        return self._online

    def _tick(self):
        now = winapi.mutex_exists(self._name)
        if now and not self._online:
            self._online = True
            self.linked.emit()
        elif not now and self._online:
            self._online = False
            self.unlinked.emit()