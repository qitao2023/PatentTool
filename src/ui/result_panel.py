"""
检索结果列表面板 - 按检索式分Tab显示专利列表
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTableWidget, QTableWidgetItem,
    QHeaderView, QLabel, QHBoxLayout,
)
from PySide6.QtCore import Qt, Slot, Signal


class ResultPanel(QWidget):
    """左下侧检索结果列表"""

    patent_selected = Signal(dict)  # 用户点击某篇专利时发射

    def __init__(self, parent=None):
        super().__init__(parent)
        self._all_results = []  # 保存去重后的结果，供点击时查找
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # 顶部信息
        info_row = QHBoxLayout()
        self.summary_label = QLabel("检索结果: 等待执行...")
        info_row.addWidget(self.summary_label)
        info_row.addStretch(1)
        layout.addLayout(info_row)

        # 结果表格
        self.summary_table = QTableWidget(0, 7)
        self.summary_table.setHorizontalHeaderLabels([
            "公开号", "标题", "相关度", "申请人", "公开日", "IPC分类", "来源检索式"
        ])
        self.summary_table.horizontalHeader().setStretchLastSection(True)
        self.summary_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.summary_table.setAlternatingRowColors(True)
        self.summary_table.setSelectionBehavior(
            self.summary_table.SelectionBehavior.SelectRows
        )
        layout.addWidget(self.summary_table, 1)

    @Slot(int, int, list)
    def add_query_results(self, query_index: int, total: int, results: list):
        """检索完成后更新结果表格"""
        self._populate_table(self.summary_table, results)

    @Slot(list)
    def show_dedup_results(self, results: list):
        """显示去重后的结果"""
        self._all_results = results
        self._populate_table(self.summary_table, results)
        self.summary_label.setText(f"检索结果: 去重后共 {len(results)} 条 | 点击某篇查看对比分析")
        self.summary_table.cellClicked.connect(self._on_cell_clicked)

    def _populate_table(self, table: QTableWidget, results: list):
        """填充表格数据"""
        table.setRowCount(len(results))
        for row_idx, r in enumerate(results):
            table.setItem(row_idx, 0, QTableWidgetItem(r.get("publication_number", "")))
            table.setItem(row_idx, 1, QTableWidgetItem(r.get("title", "")))
            # 相关度：优先用全文评分，否则用摘要阶段评分
            score = r.get("fulltext_score") or r.get("relevance_score", "")
            score_item = QTableWidgetItem(str(score) if score != "" else "-")
            if isinstance(score, (int, float)) and score > 0:
                if score >= 80:
                    score_item.setForeground(Qt.GlobalColor.darkGreen)
                elif score >= 60:
                    score_item.setForeground(Qt.GlobalColor.darkYellow)
                else:
                    score_item.setForeground(Qt.GlobalColor.darkRed)
            table.setItem(row_idx, 2, score_item)
            table.setItem(row_idx, 3, QTableWidgetItem(r.get("applicant", "")))
            table.setItem(row_idx, 4, QTableWidgetItem(
                (r.get("publication_date") or "").replace("\n", " ").replace("\t", " ")))
            table.setItem(row_idx, 5, QTableWidgetItem(r.get("ipc", "")))
            if table.columnCount() > 6:
                src = r.get("source_queries", "")
                table.setItem(row_idx, 6, QTableWidgetItem(str(src)))
        table.resizeColumnsToContents()

    def _on_cell_clicked(self, row: int, col: int):
        """表格行点击 → 通过公开号匹配发射专利数据"""
        item = self.summary_table.item(row, 0)
        if item is None:
            return
        pub_num = item.text().strip()
        match = next(
            (r for r in self._all_results
             if r.get("publication_number", "") == pub_num),
            None)
        if match:
            self.patent_selected.emit(match)

    def reset(self):
        """重置"""
        self._all_results = []
        self.summary_table.setRowCount(0)
        self.summary_label.setText("检索结果: 等待执行...")
