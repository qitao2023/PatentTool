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

    # 匹配文件路径：含盘符或目录分隔符，以已知扩展名结尾
    _PATH_CHAR = r'[^\s,，。；;\n<>"|?*]'  # 路径中允许的字符（排除空白和标点）
    FILE_PATH_RE = re.compile(
        r'('
        r'(?:[A-Za-z]:[/\\]' + _PATH_CHAR + r'*\.\w{2,5})'  # 绝对路径
        r'|'
        r'(?:' + _PATH_CHAR + r'*[/\\]' + _PATH_CHAR + r'*\.\w{2,5})'  # 相对路径
        r')',
        re.IGNORECASE
    )

    # 常见的可打开文件扩展名
    OPENABLE_EXTS = {'.json', '.html', '.htm', '.md', '.txt', '.csv', '.xml', '.log'}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 2, 10, 2)
        layout.setSpacing(2)

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
        import urllib.parse

        def replacer(m):
            path = m.group(1).strip()
            ext = Path(path).suffix.lower()
            if ext not in self.OPENABLE_EXTS:
                return m.group(0)  # 不转换，保持原样
            try:
                abs_path = str(Path(path).resolve()).replace('\\', '/')
            except Exception:
                return m.group(0)
            icon = "🌐" if ext in (".html", ".htm") else "📄"
            encoded = urllib.parse.quote(abs_path, safe='/:')
            return f'<a href="open://{encoded}" style="color:#0550ae;text-decoration:underline;">{icon} {m.group(1)}</a>'

        return self.FILE_PATH_RE.sub(replacer, message)

    def _on_link_clicked(self, url: QUrl):
        """点击文件链接 → 用系统默认程序打开"""
        import urllib.parse
        scheme = url.scheme()
        if scheme == "open":
            # 自定义 open:// 协议，路径需要 URL 解码
            path = urllib.parse.unquote(url.path())
            if path and Path(path).exists():
                ext = Path(path).suffix.lower()
                if ext in (".html", ".htm"):
                    QDesktopServices.openUrl(QUrl.fromLocalFile(path))
                elif ext == ".json":
                    import subprocess
                    subprocess.Popen(["notepad", path])
                else:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(path))
                self.file_clicked.emit(path)
        elif scheme == "file":
            path = url.toLocalFile()
            if path and Path(path).exists():
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))
                self.file_clicked.emit(path)
            else:
                QDesktopServices.openUrl(url)
        else:
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
