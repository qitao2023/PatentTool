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

    # 全局样式表
    app.setStyleSheet("""
    /* === 全局 === */
    * {
        font-family: "Microsoft YaHei", "Segoe UI", "Noto Sans SC", sans-serif;
        font-size: 13px;
    }
    QMainWindow {
        background-color: #f5f6f8;
    }

    /* === 分组框 === */
    QGroupBox {
        font-weight: bold;
        font-size: 13px;
        border: 1px solid #d0d7de;
        border-radius: 6px;
        margin-top: 12px;
        padding: 16px 12px 12px 12px;
        background: #ffffff;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 14px;
        padding: 0 8px;
        color: #1a365d;
    }

    /* === 按钮 === */
    QPushButton {
        padding: 6px 16px;
        border: 1px solid #c0c8d0;
        border-radius: 5px;
        background: #ffffff;
        color: #333;
        font-size: 13px;
    }
    QPushButton:hover {
        background: #e8edf2;
        border-color: #8899aa;
    }
    QPushButton:pressed {
        background: #d0d8e0;
    }
    QPushButton:disabled {
        background: #f0f0f0;
        color: #bbb;
        border-color: #ddd;
    }

    /* === 标签 === */
    QLabel {
        color: #333;
    }

    /* === 输入框 === */
    QLineEdit {
        padding: 6px 10px;
        border: 1px solid #c0c8d0;
        border-radius: 4px;
        background: #ffffff;
        font-size: 13px;
    }
    QLineEdit:focus {
        border-color: #0078D4;
        border-width: 1.5px;
    }

    /* === 数字框 === */
    QSpinBox {
        padding: 5px 8px;
        border: 1px solid #c0c8d0;
        border-radius: 4px;
        background: #ffffff;
        min-height: 28px;
    }
    QSpinBox:focus {
        border-color: #0078D4;
    }
    QSpinBox::up-button {
        width: 20px;
    }
    QSpinBox::down-button {
        width: 20px;
    }

    /* === 下拉框 === */
    QComboBox {
        padding: 5px 10px;
        border: 1px solid #c0c8d0;
        border-radius: 4px;
        background: #ffffff;
        min-height: 28px;
    }
    QComboBox:hover { border-color: #8899aa; }
    QComboBox::drop-down {
        width: 24px;
    }
    QComboBox QAbstractItemView {
        border: 1px solid #c0c8d0;
        border-radius: 4px;
        padding: 4px;
        selection-background-color: #d4e4f7;
        selection-color: #1a365d;
    }

    /* === 表格 === */
    QTableWidget {
        border: 1px solid #d0d7de;
        border-radius: 4px;
        background: #ffffff;
        gridline-color: #e8ecf0;
        font-size: 13px;
    }
    QTableWidget::item {
        padding: 5px 8px;
    }
    QTableWidget::item:selected {
        background: #d4e4f7;
        color: #1a365d;
    }
    QHeaderView::section {
        background: #f0f3f7;
        padding: 6px 8px;
        border: none;
        border-bottom: 2px solid #c8d0d8;
        font-weight: bold;
        font-size: 13px;
        color: #4a5568;
    }

    /* === 标签页 === */
    QTabWidget::pane {
        border: 1px solid #d0d7de;
        border-radius: 4px;
        background: #ffffff;
    }
    QTabBar::tab {
        padding: 8px 18px;
        border: 1px solid #d0d7de;
        border-bottom: none;
        border-top-left-radius: 4px;
        border-top-right-radius: 4px;
        background: #e8ecf0;
        margin-right: 2px;
        font-size: 13px;
    }
    QTabBar::tab:selected {
        background: #ffffff;
        border-bottom: 2px solid #0078D4;
        font-weight: bold;
        color: #0078D4;
    }
    QTabBar::tab:hover:!selected {
        background: #dce4ec;
    }

    /* === 文本浏览器 === */
    QTextBrowser {
        border: 1px solid #d0d7de;
        border-radius: 4px;
        background: #ffffff;
    }

    /* === 滑块 === */
    QSlider::groove:horizontal {
        height: 6px;
        background: #d0d7de;
        border-radius: 3px;
    }
    QSlider::handle:horizontal {
        width: 16px;
        height: 16px;
        margin: -5px 0;
        background: #0078D4;
        border-radius: 8px;
    }
    QSlider::handle:horizontal:hover {
        background: #106EBE;
    }

    /* === 分割器 === */
    QSplitter::handle {
        background: #d0d7de;
        width: 2px;
    }

    /* === 状态栏 === */
    QStatusBar {
        background: #f0f3f7;
        border-top: 1px solid #d0d7de;
        font-size: 13px;
        color: #666;
    }

    /* === 滚动条 === */
    QScrollBar:vertical {
        width: 10px;
        background: #f5f6f8;
        border-radius: 5px;
    }
    QScrollBar::handle:vertical {
        background: #c0c8d0;
        border-radius: 5px;
        min-height: 30px;
    }
    QScrollBar::handle:vertical:hover {
        background: #8899aa;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0px;
    }
    QScrollBar:horizontal {
        height: 10px;
        background: #f5f6f8;
    }
    QScrollBar::handle:horizontal {
        background: #c0c8d0;
        border-radius: 5px;
        min-width: 30px;
    }
    QScrollBar::handle:horizontal:hover {
        background: #8899aa;
    }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
        width: 0px;
    }

    /* === 主要操作按钮 === */
    QPushButton#startBtn {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #1a88e0, stop:1 #0078D4);
        color: white;
        font-weight: bold;
        font-size: 13px;
        padding: 8px 28px;
        border: none;
        border-radius: 6px;
    }
    QPushButton#startBtn:hover {
        background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
            stop:0 #2299f2, stop:1 #106EBE);
    }
    QPushButton#startBtn:pressed {
        background: #005a9e;
    }
    QPushButton#startBtn:disabled {
        background: #c8d0d8;
        color: #e8ecf0;
        border: none;
    }

    QPushButton#stopBtn {
        background: #e53e3e;
        color: white;
        font-weight: bold;
        font-size: 13px;
        padding: 8px 20px;
        border: none;
        border-radius: 6px;
    }
    QPushButton#stopBtn:hover {
        background: #c53030;
    }
    QPushButton#stopBtn:pressed {
        background: #9b2c2c;
    }
    QPushButton#stopBtn:disabled {
        background: #c8d0d8;
        color: #e8ecf0;
    }

    QPushButton#exportBtn {
        background: #edf2f7;
        color: #2d3748;
        border: 1px solid #cbd5e0;
        padding: 5px 14px;
    }
    QPushButton#exportBtn:hover {
        background: #e2e8f0;
        border-color: #a0aec0;
    }

    QLabel#hintLabel {
        color: #718096;
    }

    QLabel#aiProviderLabel {
        color: #718096;
        font-weight: bold;
    }

    QPushButton#saveBtn {
        background: #0078D4;
        color: white;
        font-weight: bold;
        padding: 6px 24px;
        border: none;
        border-radius: 5px;
    }
    QPushButton#saveBtn:hover {
        background: #106EBE;
    }

    QPushButton#settingsBtn {
        background: #edf2f7;
        color: #2d3748;
        border: 1px solid #cbd5e0;
        font-size: 13px;
        padding: 6px 16px;
    }
    QPushButton#settingsBtn:hover {
        background: #e2e8f0;
        border-color: #a0aec0;
    }
    """)

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
    window.showMaximized()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
