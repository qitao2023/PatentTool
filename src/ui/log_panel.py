"""
进度日志面板 - 纯文本日志 + 进度条
"""
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QProgressBar,
    QLabel,
)
from PySide6.QtCore import Slot, Signal
from PySide6.QtGui import QColor


class LogPanel(QWidget):
    """日志 + 进度条面板"""

    progress_clicked = Signal()

    LEVEL_COLORS = {
        "INFO": QColor("#2b6cb0"),
        "WARN": QColor("#c05621"),
        "ERROR": QColor("#c53030"),
        "SUCCESS": QColor("#276749"),
        "DEBUG": QColor("#718096"),
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)

        # 进度条行
        progress_row = QHBoxLayout()
        progress_row.addWidget(QLabel("进度:"))
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(22)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #aaa;
                border-radius: 4px;
                text-align: center;
                font-weight: bold;
                font-size: 13px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #4CAF50, stop:1 #2196F3);
                border-radius: 3px;
            }
        """)
        progress_row.addWidget(self.progress_bar, 1)
        self.status_label = QLabel("就绪")
        progress_row.addWidget(self.status_label)
        layout.addLayout(progress_row)

        # 日志区域
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(2000)
        self.log_text.setStyleSheet("""
            QPlainTextEdit {
                font-family: "Cascadia Code", "Consolas", "Microsoft YaHei", monospace;
                font-size: 12px;
                background-color: #fafbfc;
                border: 1px solid #d0d7de;
                border-radius: 4px;
                padding: 4px;
            }
        """)
        layout.addWidget(self.log_text, 1)

    def set_log_file(self, path):
        """设置日志文件路径，之后每条日志都会同步写入"""
        self._log_file = path

    @Slot(str, str)
    def append_log(self, level: str, message: str):
        """添加一条日志（同时写入 UI 和文件）"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        icon = {"INFO": "i", "WARN": "!", "ERROR": "X", "SUCCESS": "V", "DEBUG": "."}.get(level, ".")
        line = f"[{timestamp}] {icon} {message}"
        self.log_text.appendPlainText(line)
        # 同步写入日志文件
        if getattr(self, "_log_file", None):
            try:
                with open(self._log_file, "a", encoding="utf-8") as f:
                    f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] [{level}] {message}\n")
            except Exception:
                pass

    @Slot(int, str)
    def update_progress(self, percent: int, status: str):
        self.progress_bar.setValue(percent)
        self.status_label.setText(status)

    def clear_log(self):
        """仅清空日志文本，保留进度状态"""
        self.log_text.clear()

    def reset(self):
        self.progress_bar.setValue(0)
        self.status_label.setText("就绪")
        self.log_text.clear()
