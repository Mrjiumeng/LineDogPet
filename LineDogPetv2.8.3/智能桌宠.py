# -*- coding: utf-8 -*-
"""
LineDogPet —— 双击启动入口。
纯本地运行，AI 可选（用你自己的 API Key）。
"""
import os
import sys
import traceback
import tempfile


def _setup_crash_log():
    def hook(exc_type, exc, tb):
        try:
            p = os.path.join(tempfile.gettempdir(), "smartpet_crash.log")
            with open(p, "a", encoding="utf-8") as f:
                f.write("\n--- 未捕获异常 ---\n")
                traceback.print_exception(exc_type, exc, tb, file=f)
        except Exception:
            pass
    sys.excepthook = hook


def _single_instance():
    """同名互斥体：已在运行就直接退出，避免开一堆。"""
    if sys.platform != "win32":
        return True
    import ctypes
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Global\\SmartPet_SingleInstance")
    if ctypes.windll.kernel32.GetLastError() == 183:
        return False
    # 句柄故意不释放，进程退出系统自动回收
    return True


def main():
    _setup_crash_log()
    if not _single_instance():
        sys.exit(0)
    from smartpet.app import main as app_main
    app_main()


if __name__ == "__main__":
    main()
