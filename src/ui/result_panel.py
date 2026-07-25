"""
检索结果列表面板 - 按检索式分Tab显示专利列表
"""
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QTabWidget, QTableWidget, QTableWidgetItem,
    QHeaderView, QLabel, QSplitter, QHBoxLayout,
)
from PySide6.QtCore import Qt, Slot


class ResultPanel(QWidget):
    """左下侧检索结果列表"""

    def __init__(self, parent=None):
        super().__init__(parent)
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

        # Tab切换：每个检索式一个Tab + 汇总Tab
        self.tab_widget = QTabWidget()
        self.tab_widget.addTab(self._create_summary_tab(), "📊 全部去重")
        layout.addWidget(self.tab_widget, 1)

    def _create_summary_tab(self) -> QWidget:
        """创建汇总Tab（初始为空）"""
        tab = QWidget()
        layout = QVBoxLayout(tab)
        self.summary_table = QTableWidget(0, 6)
        self.summary_table.setHorizontalHeaderLabels([
            "公开号", "标题", "申请人", "公开日", "IPC分类", "来源检索式"
        ])
        self.summary_table.horizontalHeader().setStretchLastSection(True)
        self.summary_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        self.summary_table.setAlternatingRowColors(True)
        self.summary_table.setSortingEnabled(True)
        self.summary_table.setSelectionBehavior(
            self.summary_table.SelectionBehavior.SelectRows
        )
        layout.addWidget(self.summary_table)
        return tab

    def add_query_tab(self, query_index: int, query_string: str):
        """为某个检索式添加一个Tab"""
        # 如果已存在相同index的Tab则不重复添加
        tab_text = f"检索式{query_index}"
        for i in range(self.tab_widget.count()):
            if self.tab_widget.tabText(i) == tab_text:
                self.tab_widget.setCurrentIndex(i)
                return

        tab = QWidget()
        layout = QVBoxLayout(tab)
        table = QTableWidget(0, 5)
        table.setHorizontalHeaderLabels([
            "公开号", "标题", "申请人", "公开日", "IPC分类"
        ])
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents
        )
        table.setAlternatingRowColors(True)
        table.setSortingEnabled(True)
        table.setSelectionBehavior(table.SelectionBehavior.SelectRows)
        layout.addWidget(table)

        # 存储引用以便后续填充数据
        table.setProperty("query_index", query_index)
        table.setProperty("query_string", query_string)

        idx = self.tab_widget.addTab(tab, tab_text)
        self.tab_widget.setCurrentIndex(idx)

    @Slot(int, int, list)
    def add_query_results(self, query_index: int, total: int, results: list):
        """将某个检索式的结果填入对应Tab"""
        tab_text = f"检索式{query_index}"
        for i in range(self.tab_widget.count()):
            if self.tab_widget.tabText(i) == tab_text:
                tab = self.tab_widget.widget(i)
                table = tab.findChild(QTableWidget)
                if table:
                    self._populate_table(table, results)
                # 更新Tab标题显示数量
                self.tab_widget.setTabText(i, f"{tab_text} ({len(results)})")
                break

    @Slot(list)
    def show_dedup_results(self, results: list):
        """显示去重后的汇总结果"""
        # 切换到汇总Tab
        self.tab_widget.setCurrentIndex(0)
        self._populate_table(self.summary_table, results)
        self.summary_label.setText(f"检索结果: 去重后共 {len(results)} 条")

    def _populate_table(self, table: QTableWidget, results: list):
        """填充表格数据"""
        # results 是 list[dict]，包含 public_number, title, applicant, pub_date, ipc 等
        table.setRowCount(len(results))
        for row_idx, r in enumerate(results):
            table.setItem(row_idx, 0, QTableWidgetItem(r.get("publication_number", "")))
            table.setItem(row_idx, 1, QTableWidgetItem(r.get("title", "")))
            table.setItem(row_idx, 2, QTableWidgetItem(r.get("applicant", "")))
            table.setItem(row_idx, 3, QTableWidgetItem(r.get("publication_date", "")))
            table.setItem(row_idx, 4, QTableWidgetItem(r.get("ipc", "")))
            if table.columnCount() > 5:
                src = r.get("source_queries", "")
                table.setItem(row_idx, 5, QTableWidgetItem(str(src)))
        table.resizeColumnsToContents()

    def reset(self):
        """重置所有Tab"""
        while self.tab_widget.count() > 1:
            self.tab_widget.removeTab(1)
        self.summary_table.setRowCount(0)
        self.summary_label.setText("检索结果: 等待执行...")
