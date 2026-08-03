"""
生成应用图标：assets/icon.png 与 assets/icon.ico（供 PyInstaller 打包 exe 使用）。

用法：python tools/generate_icon.py
源码：src/ui/icons.py 中的 _APP_SVG（蓝色圆角方块 + 专利文件白标）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import Qt, QByteArray
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QApplication

from src.ui.icons import app_svg


def main() -> int:
    app = QApplication(sys.argv)  # noqa: F841  # 需要 QGuiApplication 才能用 QPixmap
    assets_dir = Path(__file__).resolve().parent.parent / "assets"
    assets_dir.mkdir(exist_ok=True)

    renderer = QSvgRenderer(QByteArray(app_svg().encode("utf-8")))
    pix = QPixmap(256, 256)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()

    png_ok = pix.save(str(assets_dir / "icon.png"), "PNG")
    ico_ok = pix.save(str(assets_dir / "icon.ico"), "ICO")
    print(f"icon.png -> {png_ok}")
    print(f"icon.ico -> {ico_ok}")
    print(f"输出目录: {assets_dir}")
    return 0 if (png_ok and ico_ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
