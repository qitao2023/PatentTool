"""
专利详情和对比分析报告面板
"""
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextBrowser, QPushButton, QLabel,
    QFileDialog, QMessageBox,
)
from PySide6.QtCore import Slot
import markdown


class ReportPanel(QWidget):
    """右下侧专利详情和对比分析报告"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_markdown: str = ""  # 保存原始 markdown 用于导出
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("📋 对比分析报告"))
        title_row.addStretch(1)

        self.export_word_btn = QPushButton("📄 导出 Word")
        self.export_word_btn.setObjectName("exportBtn")
        self.export_word_btn.setEnabled(False)
        self.export_word_btn.clicked.connect(self._export_word)
        title_row.addWidget(self.export_word_btn)

        layout.addLayout(title_row)

        # 报告浏览器
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setObjectName("reportBrowser")
        self.browser.setStyleSheet("""
            QTextBrowser#reportBrowser {
                font-family: "Microsoft YaHei", "Segoe UI", sans-serif;
                font-size: 13px;
                background-color: #ffffff;
                border: 1px solid #d0d7de;
                border-radius: 4px;
                padding: 12px;
                line-height: 1.8;
            }
        """)
        layout.addWidget(self.browser, 1)

        self._show_placeholder()

    def _show_placeholder(self):
        self.browser.setHtml("""
        <div style="text-align: center; color: #8b949e; padding: 60px 20px;">
            <h2>等待分析...</h2>
            <p>完成检索和去重后，<b>点击左侧某篇专利</b>即可查看其与本申请的详细对比分析。</p>
        </div>
        """)

    def show_loading(self, patent_number: str):
        self.browser.setHtml(f"""
        <div style="text-align: center; color: #8b949e; padding: 60px 20px;">
            <h2>⏳ AI 正在分析...</h2>
            <p>正在对 <b>{patent_number}</b> 与本申请进行详细对比，请稍候...</p>
        </div>
        """)

    def show_single_comparison(self, patent_number: str, markdown_text: str):
        if not markdown_text or not markdown_text.strip():
            self.browser.setHtml("<p>对比结果为空</p>")
            return
        self._current_markdown = markdown_text
        self._show_markdown(markdown_text)
        self.export_word_btn.setEnabled(True)

    @Slot(object)
    def show_report(self, report):
        """显示分析报告（保留兼容旧流程）"""
        if hasattr(report, "html_content") and report.html_content:
            self.browser.setHtml(report.html_content)
        elif hasattr(report, "markdown_content") and report.markdown_content:
            self._current_markdown = report.markdown_content
            self._show_markdown(report.markdown_content)
        else:
            self.browser.setHtml("<p>报告内容为空</p>")
        self.export_word_btn.setEnabled(True)

    def _show_markdown(self, md: str):
        # 使用 safe_mode 防止 HTML 注入
        html = markdown.markdown(
            md, extensions=["tables", "fenced_code"],
            output_format="xhtml")
        styled = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
body {{ font-family: "Microsoft YaHei", sans-serif; line-height: 1.8; padding: 10px; }}
h1 {{ border-bottom: 2px solid #333; padding-bottom: 8px; }}
h2 {{ border-bottom: 1px solid #999; padding-bottom: 5px; margin-top: 24px; }}
h3 {{ margin-top: 18px; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
th, td {{ border: 1px solid #ccc; padding: 8px; text-align: left; }}
th {{ background: #f0f0f0; }}
blockquote {{ border-left: 3px solid #0078D4; padding-left: 15px; color: #555; background: #f5f9ff; }}
code {{ background: #f5f5f5; padding: 1px 4px; border-radius: 3px; }}
</style></head><body>{html}</body></html>"""
        self.browser.setHtml(styled)

    def _export_word(self):
        """导出为 Word 文档"""
        if not self._current_markdown:
            QMessageBox.warning(self, "提示", "没有可导出的内容")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "导出 Word 报告", "对比分析报告.docx",
            "Word 文档 (*.docx)"
        )
        if not path:
            return

        try:
            from docx import Document
            from docx.shared import Pt, Cm, Inches, RGBColor
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            import re

            doc = Document()
            # 设置默认字体
            style = doc.styles['Normal']
            font = style.font
            font.name = 'Microsoft YaHei'
            font.size = Pt(11)

            # 逐行解析 markdown
            lines = self._current_markdown.split('\n')
            in_table = False
            table_data = []

            def flush_table(data, document):
                if not data:
                    return
                rows = len(data)
                cols = max(len(r) for r in data)
                table = document.add_table(rows=rows, cols=cols, style='Table Grid')
                for i, row in enumerate(data):
                    for j, cell_text in enumerate(row):
                        cell = table.cell(i, j)
                        cell.text = cell_text.strip()
                        # 表头加粗
                        if i == 0:
                            for p in cell.paragraphs:
                                for run in p.runs:
                                    run.bold = True
                document.add_paragraph()  # 表后空行

            for line in lines:
                # 表格
                if line.startswith('|') and line.strip().endswith('|'):
                    if '---' in line:
                        continue
                    cells = [c.strip() for c in line.split('|')[1:-1]]
                    table_data.append(cells)
                    in_table = True
                    continue
                elif in_table:
                    flush_table(table_data, doc)
                    table_data = []
                    in_table = False

                # 标题
                if line.startswith('# '):
                    h = doc.add_heading(line[2:].strip(), level=1)
                elif line.startswith('## '):
                    h = doc.add_heading(line[3:].strip(), level=2)
                elif line.startswith('### '):
                    h = doc.add_heading(line[4:].strip(), level=3)
                elif line.startswith('**') and '**' in line[2:]:
                    p = doc.add_paragraph()
                    run = p.add_run(re.sub(r'\*\*(.*?)\*\*', r'\1', line))
                    run.bold = True
                elif line.strip().startswith('- ') or line.strip().startswith('* '):
                    doc.add_paragraph(line.strip()[2:], style='List Bullet')
                elif line.strip().startswith('> '):
                    p = doc.add_paragraph()
                    run = p.add_run(line.strip()[2:])
                    run.italic = True
                    run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
                elif line.strip():
                    doc.add_paragraph(line.strip())
                else:
                    doc.add_paragraph()

            # 剩余表格
            if table_data:
                flush_table(table_data, doc)

            doc.save(path)
            QMessageBox.information(self, "导出成功", f"报告已导出到:\n{path}")

        except Exception as e:
            QMessageBox.warning(self, "导出失败", f"无法生成 Word 文档:\n{e}")

    def reset(self):
        self._current_markdown = ""
        self.export_word_btn.setEnabled(False)
        self._show_placeholder()
