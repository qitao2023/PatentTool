"""
专利详情和对比分析报告面板
"""
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextBrowser, QPushButton,
    QLabel, QFileDialog, QMessageBox,
)
from PySide6.QtCore import Slot
import markdown


class ReportPanel(QWidget):
    """右下侧专利详情和对比分析报告"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_report_html: str | None = None
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 顶部工具栏
        tool_row = QHBoxLayout()
        tool_row.addWidget(QLabel("📋 对比分析报告"))
        tool_row.addStretch(1)

        self.export_html_btn = QPushButton("导出 HTML")
        self.export_html_btn.clicked.connect(self._export_html)
        self.export_html_btn.setEnabled(False)
        tool_row.addWidget(self.export_html_btn)

        self.export_docx_btn = QPushButton("导出 Word")
        self.export_docx_btn.clicked.connect(self._export_docx)
        self.export_docx_btn.setEnabled(False)
        tool_row.addWidget(self.export_docx_btn)

        layout.addLayout(tool_row)

        # 报告浏览器
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setStyleSheet("""
            QTextBrowser {
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
                font-size: 14px;
                background-color: #ffffff;
                border: 1px solid #d0d7de;
                border-radius: 4px;
                padding: 12px;
                line-height: 1.6;
            }
        """)
        layout.addWidget(self.browser, 1)

        # 初始状态
        self._show_placeholder()

    def _show_placeholder(self):
        self.browser.setHtml("""
        <div style="text-align: center; color: #8b949e; padding: 60px 20px;">
            <h2>等待分析...</h2>
            <p>完成检索和去重后，将在此显示专利对比分析报告。</p>
            <p>分析将包括：</p>
            <ul style="display: inline-block; text-align: left;">
                <li>检索式及其检索角度说明</li>
                <li>各对比文献与本申请的新颖性/创造性对比</li>
                <li>特征矩阵和差异分析</li>
                <li>相关度评分</li>
            </ul>
        </div>
        """)

    @Slot(object)
    def show_report(self, report):
        """显示分析报告"""
        # report 应包含 .markdown_content 或 .html_content
        if hasattr(report, "html_content") and report.html_content:
            html = report.html_content
        elif hasattr(report, "markdown_content") and report.markdown_content:
            html = markdown.markdown(
                report.markdown_content,
                extensions=["tables", "fenced_code", "codehilite"]
            )
        else:
            html = "<p>报告内容为空</p>"

        self._current_report_html = html
        self.browser.setHtml(html)
        self.export_html_btn.setEnabled(True)
        self.export_docx_btn.setEnabled(True)

    def _export_html(self):
        if not self._current_report_html:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出HTML报告", "patent_analysis_report.html",
            "HTML文件 (*.html)"
        )
        if path:
            full_html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>专利对比分析报告</title>
<style>
body {{ font-family: "Microsoft YaHei", sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; line-height: 1.8; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
th {{ background-color: #f2f2f2; }}
</style>
</head><body>
{self._current_report_html}
</body></html>"""
            Path(path).write_text(full_html, encoding="utf-8")
            QMessageBox.information(self, "导出成功", f"报告已导出到:\n{path}")

    def _export_docx(self):
        QMessageBox.information(
            self, "提示",
            "Word 导出功能将在后续版本实现。\n当前可导出 HTML 格式。"
        )

    def reset(self):
        self._current_report_html = None
        self.export_html_btn.setEnabled(False)
        self.export_docx_btn.setEnabled(False)
        self._show_placeholder()
