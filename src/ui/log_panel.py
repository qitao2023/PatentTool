"""
进度日志面板 - 实时显示运行日志和进度条，文件路径可点击打开
"""
import re
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextBrowser, QProgressBar,
    QLabel,
)
from PySide6.QtCore import Slot, Signal, QUrl
from PySide6.QtGui import QColor, QTextCursor, QDesktopServices


class LogPanel(QWidget):
    """中间进度日志区域"""

    file_clicked = Signal(str)  # 用户点击文件路径时发射

    LEVEL_COLORS = {
        "INFO": QColor("#2b6cb0"),
        "WARN": QColor("#c05621"),
        "ERROR": QColor("#c53030"),
        "SUCCESS": QColor("#276749"),
        "DEBUG": QColor("#718096"),
    }

    # 匹配文件路径的模式
    FILE_PATH_RE = re.compile(
        r'(?:已保存[：:]?\s*|输出[：:]?\s*|文件[：:]?\s*|结果[：:]?\s*|'
        r'保存到[：:]?\s*|路径[：:]?\s*)?'
        r'('
        r'(?:[A-Za-z]:[/\\][^\s,，。；;]+\.\w{2,5})'  # 绝对路径
        r'|'
        r'(?:data[/\\]output[/\\][^\s,，。；;]+\.\w{2,5})'  # 相对路径 data/output/...
        r')',
        re.IGNORECASE
    )

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
        self.log_text = QTextBrowser()
        self.log_text.setReadOnly(True)
        self.log_text.document().setMaximumBlockCount(2000)
        self.log_text.setStyleSheet("""
            QTextBrowser {
                font-family: "Cascadia Code", "Consolas", "Microsoft YaHei", monospace;
                font-size: 12px;
                background-color: #fafbfc;
                border: 1px solid #d0d7de;
                border-radius: 4px;
                padding: 4px;
                line-height: 1.5;
            }
        """)
        self.log_text.setOpenExternalLinks(False)  # 手动处理所有链接
        self.log_text.anchorClicked.connect(self._on_link_clicked)
        layout.addWidget(self.log_text, 1)

    def _make_links(self, message: str) -> str:
        """将消息中的文件路径替换为可点击的 HTML 链接"""
        def replacer(m):
            path = m.group(1)
            abs_path = str(Path(path).resolve())
            ext = Path(path).suffix.lower()
            icon = "🌐" if ext in (".html", ".htm") else "📄"
            return f'<a href="file:///{abs_path}" style="color:#0550ae;text-decoration:underline;">{icon} {path}</a>'

        # 只替换文件路径部分，保留其他文本
        result = self.FILE_PATH_RE.sub(replacer, message)

        # 如果消息中包含 已保存/保存到 但没有被正则匹配到，
        # 再尝试匹配末尾的文件路径
        if result == message:
            for pattern in [r'已保存[：:]?\s*', r'保存到[：:]?\s*']:
                m = re.search(pattern, message)
                if m:
                    rest = message[m.end():].strip()
                    if rest and '.' in rest:
                        possible_path = rest.split()[0] if ' ' in rest else rest
                        ext = Path(possible_path).suffix.lower()
                        if ext in ('.json', '.html', '.htm', '.md', '.txt', '.csv'):
                            abs_path = str(Path(possible_path).resolve())
                            icon = "🌐" if ext in (".html", ".htm") else "📄"
                            linked = f'<a href="file:///{abs_path}" style="color:#0550ae;text-decoration:underline;">{icon} {possible_path}</a>'
                            result = message[:m.end()] + linked + message[m.end()+len(possible_path):]
                            break
        return result

    def _on_link_clicked(self, url):
        """点击文件链接 → 用系统默认程序打开"""
        path = url.toLocalFile()
        if path and Path(path).exists():
            ext = Path(path).suffix.lower()
            if ext in (".html", ".htm"):
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            elif ext == ".json":
                # JSON 用记事本打开
                import subprocess
                subprocess.Popen(["notepad", path])
            elif ext == ".md":
                # Markdown 用默认关联程序
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            else:
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            self.file_clicked.emit(path)
        else:
            # 文件不存在，尝试用浏览器打开
            QDesktopServices.openUrl(url)

    @Slot(str, str)
    def append_log(self, level: str, message: str):
        """添加一条日志"""
        import html as html_mod
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

        # 先转义 HTML 特殊字符，防止 <> 等破坏显示
        escaped = html_mod.escape(message)
        # 再转换文件路径为可点击链接
        html_message = self._make_links(escaped)
        html_line = (
            f'<span style="color:{color.name()}">'
            f'[{timestamp}] {icon} {html_message}'
            f'</span>'
        )

        # 用 insertHtml 追加（QTextEdit.append 不接受 HTML）
        cursor = self.log_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(html_line)
        cursor.insertBlock()  # 换行
        # 滚动到底部
        self.log_text.setTextCursor(cursor)
        self.log_text.ensureCursorVisible()

    @Slot(int, str)
    def update_progress(self, percent: int, status: str):
        """更新进度条和状态"""
        self.progress_bar.setValue(percent)
        self.status_label.setText(status)

    def reset(self):
        self.progress_bar.setValue(0)
        self.status_label.setText("就绪")
        self.log_text.clear()
