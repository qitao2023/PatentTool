"""
进度日志面板 - 实时显示运行日志和进度条
"""
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextEdit, QProgressBar,
    QLabel,
)
from PySide6.QtCore import Slot
from PySide6.QtGui import QColor, QTextCursor


class LogPanel(QWidget):
    """中间进度日志区域"""

    LEVEL_COLORS = {
        "INFO": QColor("#1a7f37"),
        "WARN": QColor("#9a6700"),
        "ERROR": QColor("#cf222e"),
        "SUCCESS": QColor("#0550ae"),
        "DEBUG": QColor("#656d76"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)

        # 进度条
        progress_row = QHBoxLayout()
        progress_row.addWidget(QLabel("进度:"))
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        progress_row.addWidget(self.progress_bar, 1)
        self.status_label = QLabel("就绪")
        progress_row.addWidget(self.status_label)
        layout.addLayout(progress_row)

        # 日志区域
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.document().setMaximumBlockCount(2000)
        self.log_text.setStyleSheet("""
            QTextEdit {
                font-family: "Consolas", "Microsoft YaHei Mono", monospace;
                font-size: 12px;
                background-color: #f6f8fa;
                border: 1px solid #d0d7de;
                border-radius: 4px;
                padding: 4px;
            }
        """)
        layout.addWidget(self.log_text, 1)

    @Slot(str, str)
    def append_log(self, level: str, message: str):
        """添加一条日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        color = self.LEVEL_COLORS.get(level, QColor("#24292f"))

        prefix_icons = {
            "INFO": "ℹ",
            "WARN": "⚠",
            "ERROR": "✗",
            "SUCCESS": "✓",
            "DEBUG": "·",
        }
        icon = prefix_icons.get(level, "·")

        self.log_text.setTextColor(color)
        self.log_text.append(f"[{timestamp}] {icon} {message}")
        # 滚动到底部
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        self.log_text.setTextCursor(cursor)

    @Slot(int, str)
    def update_progress(self, percent: int, status: str):
        """更新进度条和状态"""
        self.progress_bar.setValue(percent)
        self.status_label.setText(status)

    def reset(self):
        self.progress_bar.setValue(0)
        self.status_label.setText("就绪")
        self.log_text.clear()
