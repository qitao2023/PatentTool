"""
进度日志面板 - 纯文本日志 + 进度条
"""
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPlainTextEdit, QProgressBar,
    QLabel,
)
from PySide6.QtCore import Slot, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor

from src.ui.theme import DEFAULT_THEME, LEVEL_TOKENS


class LogPanel(QWidget):
    """日志 + 进度条面板"""

    progress_clicked = Signal()

    # 日志级别 → 颜色（取自集中主题模块，可整体换肤）
    LEVEL_COLORS = {
        lv: QColor(DEFAULT_THEME[tok])
        for lv, tok in LEVEL_TOKENS.items()
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(4)

        # 进度条行（样式见主题模块 QProgressBar）
        progress_row = QHBoxLayout()
        progress_row.addWidget(QLabel("进度:"))
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(22)
        progress_row.addWidget(self.progress_bar, 1)
        self.status_label = QLabel("就绪")
        progress_row.addWidget(self.status_label)
        layout.addLayout(progress_row)

        # 日志区域（仅指定等宽字体，背景/边框由主题模块统一接管）
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(2000)
        self.log_text.setStyleSheet("""
            QPlainTextEdit {
                font-family: "Cascadia Code", "Consolas", "Microsoft YaHei", monospace;
                font-size: 12px;
            }
        """)
        layout.addWidget(self.log_text, 1)

    def set_log_file(self, path):
        """设置日志文件路径，之后每条日志都会同步写入"""
        self._log_file = path

    @Slot(str, str)
    def append_log(self, level: str, message: str):
        """添加一条日志（按级别着色，同时写入 UI 和文件）"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        icon = {"INFO": "i", "WARN": "!", "ERROR": "X", "SUCCESS": "V", "DEBUG": "."}.get(level, ".")
        line = f"[{timestamp}] {icon} {message}"
        color = self.LEVEL_COLORS.get(level.upper(), QColor(DEFAULT_THEME["text"]))
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(color)
        cursor.insertText(line + "\n", fmt)
        self.log_text.setTextCursor(cursor)
        self.log_text.ensureCursorVisible()
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
