"""
专利检索分析工具 - 程序入口
"""
import sys
import os
from pathlib import Path

# 确保项目根目录在 sys.path 中
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt

from src.utils.config import Settings
from src.ui.main_window import MainWindow


def main():
    # 高DPI支持
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )

    app = QApplication(sys.argv)
    app.setApplicationName("专利检索分析工具")
    app.setOrganizationName("PatentTool")

    # 加载配置
    try:
        settings = Settings()
    except FileNotFoundError as e:
        from PySide6.QtWidgets import QMessageBox
        msg = QMessageBox()
        msg.critical(None, "配置错误", str(e))
        sys.exit(1)

    # 检查 API Key（DeepSeek 或 Kimi 至少配置一个）
    has_deepseek = bool(settings.deepseek_api_key)
    has_kimi = bool(os.getenv("KIMI_API_KEY"))
    if not has_deepseek and not has_kimi:
        from PySide6.QtWidgets import QMessageBox
        reply = QMessageBox.warning(
            None, "API Key 未配置",
            "未检测到 AI API Key。\n\n"
            "请配置 config/.env 文件，至少填入以下之一：\n"
            "  • DEEPSEEK_API_KEY=sk-...  （推荐，国内速度快）\n"
            "  • KIMI_API_KEY=sk-...       （月之暗面）\n\n"
            "需要现在编辑 .env 文件吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            import subprocess
            subprocess.Popen(["notepad", str(settings.config_dir / ".env")])

    # 启动主窗口
    window = MainWindow(settings)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
