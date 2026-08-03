"""
UI 冒烟测试：离屏启动主窗口 + 抓图，验证主题/图标无异常。

用法：python tools/smoke_ui.py
产出（data/ 目录）：
  - ui_smoke.png      主窗口整屏截图
  - icons_smoke.png   全部控件图标拼图
  - appicon_smoke.png 应用图标
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QPainter
from PySide6.QtWidgets import QApplication

from src.utils.config import Settings
from src.ui.main_window import MainWindow
from src.ui.theme import apply_theme
from src.ui.icons import app_icon, icon


def main() -> int:
    app = QApplication(sys.argv)
    apply_theme(app)
    app.setWindowIcon(app_icon())

    shot_dir = Path(__file__).resolve().parent.parent / "data"
    shot_dir.mkdir(exist_ok=True)

    # 主窗口
    win = MainWindow(Settings())
    win.show()
    app.processEvents()

    win.grab().save(str(shot_dir / "ui_smoke.png"))

    # 图标拼图预览
    names = ["play", "stop", "folder", "settings", "calendar", "clock",
             "book", "star", "file_text", "flask", "log_out", "info",
             "robot", "download", "refresh", "trash", "search", "doc"]
    grid = QPixmap(len(names) * 28 + 10, 34)
    grid.fill(Qt.GlobalColor.white)
    painter = QPainter(grid)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    for i, name in enumerate(names):
        painter.drawPixmap(5 + i * 28, 7, icon(name, size=20).pixmap(20, 20))
    painter.end()
    grid.save(str(shot_dir / "icons_smoke.png"))

    app_icon().pixmap(128, 128).save(str(shot_dir / "appicon_smoke.png"))

    win.close()
    print("OK: ui_smoke.png / icons_smoke.png / appicon_smoke.png")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
