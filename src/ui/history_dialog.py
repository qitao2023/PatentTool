"""
历史记录浏览对话框 — 扫描所有运行目录，浏览和加载历史分析结果
"""
import json
from datetime import datetime
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget,
    QTableWidgetItem, QHeaderView, QLabel, QAbstractItemView,
    QMessageBox,
)
from PySide6.QtCore import Qt, Signal

from src.utils.paths import STAGE_FILES


def _scan_all_runs(base_dir: str | Path = None) -> list[dict]:
    """扫描所有历史运行目录，返回按时间倒序的列表。

    扫描策略：
      1. data/output/ 根目录下 <专利号>_<时间戳> 文件夹
      2. data/output/<专利号>/<时间戳>/ 二级嵌套
    """
    if base_dir is None:
        base_dir = Path.cwd() / "data" / "output"
    base = Path(base_dir)
    if not base.exists():
        return []

    runs = []
    seen = set()

    # 策略1: 直接子目录（<专利号>_<时间戳> 格式）
    for entry in sorted(base.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        info = _parse_run_dir(entry)
        if info:
            key = str(entry.resolve())
            if key not in seen:
                seen.add(key)
                runs.append(info)

    # 策略2: 二级嵌套（<专利号>/<时间戳> 格式）
    for patent_dir in sorted(base.iterdir(), reverse=True):
        if not patent_dir.is_dir():
            continue
        for entry in sorted(patent_dir.iterdir(), reverse=True):
            if not entry.is_dir():
                continue
            info = _parse_run_dir(entry)
            if info:
                key = str(entry.resolve())
                if key not in seen:
                    seen.add(key)
                    runs.append(info)

    runs.sort(key=lambda r: r.get("_sort_key", ""), reverse=True)
    return runs


def _parse_run_dir(path: Path) -> dict | None:
    """解析单个运行目录，提取元数据。返回 None 表示不是有效运行目录。"""
    # 必须有至少一个阶段文件
    has_stage = any((path / f).exists() for f in STAGE_FILES.values())
    if not has_stage:
        return None

    info = {
        "path": str(path),
        "folder_name": path.name,
        "stages": {},
    }

    # 检查各阶段
    for key, filename in STAGE_FILES.items():
        info["stages"][key] = (path / filename).exists()

    # 从 03_ai_screened.json 提取元数据
    screened_path = path / "03_ai_screened.json"
    if screened_path.exists():
        try:
            data = json.loads(screened_path.read_text(encoding="utf-8"))
            info["candidate_count"] = data.get("total_scored", data.get("total_downloaded", 0))
            results = data.get("results", [])
            if results:
                # 取第一篇的标题作为代表性专利
                info["sample_title"] = results[0].get("title", "")[:60]
        except Exception:
            pass

    # 从 comparison_cache.json 获取对比缓存数量
    cache_path = path / "comparison_cache.json"
    if cache_path.exists():
        try:
            cache = json.loads(cache_path.read_text(encoding="utf-8"))
            info["cache_count"] = len(cache)
        except Exception:
            pass

    # 解析文件夹名中的时间戳
    name = path.name
    import re
    ts_match = re.search(r'(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})$', name)
    if ts_match:
        info["timestamp"] = ts_match.group(1).replace("_", " ").replace("-", ":", 2).replace("-", ":")
        info["_sort_key"] = ts_match.group(1)
    else:
        # 用文件夹修改时间兜底
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        info["timestamp"] = mtime.strftime("%Y-%m-%d %H:%M:%S")
        info["_sort_key"] = mtime.strftime("%Y-%m-%d_%H-%M-%S")

    # 提取专利号（文件夹名中时间戳之前的部分）
    patent_name = re.sub(r'_\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}.*$', '', name)
    info["patent_label"] = patent_name

    # 从 04_analysis_report.md 提取标题
    report_path = path / "04_analysis_report.md"
    if report_path.exists():
        try:
            first_line = report_path.read_text(encoding="utf-8").split("\n")[0].strip()
            if first_line.startswith("# "):
                first_line = first_line[2:]
            info["report_title"] = first_line[:80]
        except Exception:
            pass

    return info


class HistoryDialog(QDialog):
    """历史记录浏览对话框"""

    run_selected = Signal(str)  # 发出选中的运行目录路径

    def __init__(self, base_dir: str | Path = None, parent=None):
        super().__init__(parent)
        self._base_dir = base_dir
        self._runs = []
        self._setup_ui()
        self._refresh()

    def _setup_ui(self):
        self.setWindowTitle("历史运行记录")
        self.setMinimumSize(900, 500)

        layout = QVBoxLayout(self)

        # 标题行
        header = QHBoxLayout()
        header.addWidget(QLabel("📋 历史分析记录（双击加载）"))
        header.addStretch()
        refresh_btn = QPushButton("🔄 刷新")
        refresh_btn.clicked.connect(self._refresh)
        header.addWidget(refresh_btn)
        layout.addLayout(header)

        # 表格
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "专利名称", "运行时间", "候选数", "对比缓存",
            "搜索", "筛选", "报告",
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(self._on_double_click)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        for i in range(2, 7):
            self.table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(i, 60)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)

        # 底部按钮
        footer = QHBoxLayout()
        footer.addStretch()
        open_btn = QPushButton("📂 加载所选运行")
        open_btn.clicked.connect(self._on_load)
        footer.addWidget(open_btn)
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        footer.addWidget(close_btn)
        layout.addLayout(footer)

    def _refresh(self):
        self._runs = _scan_all_runs(self._base_dir)
        self.table.setRowCount(len(self._runs))

        for i, run in enumerate(self._runs):
            # 专利名称
            label = run.get("patent_label", run.get("folder_name", "?"))
            self.table.setItem(i, 0, QTableWidgetItem(label))

            # 运行时间
            self.table.setItem(i, 1, QTableWidgetItem(run.get("timestamp", "?")))

            # 候选数
            count = run.get("candidate_count", run.get("cache_count", "?"))
            self.table.setItem(i, 2, QTableWidgetItem(str(count)))

            # 对比缓存
            cached = "✅" if run.get("cache_count", 0) > 0 else "❌"
            self.table.setItem(i, 3, QTableWidgetItem(cached))

            # 各阶段状态
            stages = run.get("stages", {})
            for j, key in enumerate(["search", "screen", "analysis"]):
                ok = stages.get(key, False)
                item = QTableWidgetItem("✅" if ok else "⬜")
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(i, 4 + j, item)

    def _on_double_click(self, index):
        self._load_run(index.row())

    def _on_load(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一条记录")
            return
        self._load_run(row)

    def _load_run(self, row: int):
        if row < 0 or row >= len(self._runs):
            return
        run_path = self._runs[row].get("path", "")
        if run_path:
            self.run_selected.emit(run_path)
            self.accept()
