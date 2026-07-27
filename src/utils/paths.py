"""
输出路径工具：统一专利号规范化和目录结构。

目录结构（PDF 旁边单层文件夹）：
    D:/专利/
      CN116417058.pdf
      CN116417058_2026-07-27_10-30-00/   ← 一次运行一个文件夹
        01_search_abstracts.json
        ...
"""

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── 阶段文件定义 ──────────────────────────────────────────────────────

STAGE_FILES = {
    "search":    "01_search_abstracts.json",
    "detail":    "02_patent_details",           # 全部专利完整详情目录
    "screen":    "03_ai_screened.json",
    "analysis":  "04_analysis_report.md",
    "oa":        "05_审查意见通知书.md",
}


@dataclass
class RunInfo:
    """一次历史运行的信息"""
    path: Path
    folder_name: str
    timestamp: str               # "2026-07-27 10:30:00"
    stages: dict[str, bool] = field(default_factory=dict)  # {"search": True, ...}

    @property
    def completed_stages(self) -> int:
        return sum(self.stages.values())

    @property
    def is_complete(self) -> bool:
        return self.completed_stages == len(STAGE_FILES)


# ── 专利号规范化 ──────────────────────────────────────────────────────


def normalize_patent_number(raw: str) -> str:
    """规范化专利号：提取数字，格式为 CN<digits>。

    >>> normalize_patent_number("CN 116417058")
    'CN116417058'
    >>> normalize_patent_number("116417058")
    'CN116417058'
    """
    digits = re.sub(r'\D', '', raw)
    if not digits:
        return re.sub(r'\s+', '', raw.strip())
    return f"CN{digits}"


def _patent_name_from_doc(patent_doc) -> str:
    """从 PatentDocument 提取用于目录命名的专利名。"""
    if patent_doc and patent_doc.publication_number:
        return normalize_patent_number(patent_doc.publication_number)
    if patent_doc and patent_doc.title:
        return re.sub(r'[^\w一-鿿\-]', '_', patent_doc.title.strip())[:60]
    return "unknown"


# ── 输出目录 ──────────────────────────────────────────────────────────


def get_output_dir(pdf_path: str, patent_doc) -> Path:
    """在 PDF 同级创建 公布号_时间 文件夹。

    降级策略：
    1. PDF 目录 + 公布号_时间（首选）
    2. PDF 目录不可写 → <cwd>/data/output/公布号_时间
    3. 公布号解析失败 → 用 PDF 文件名（去扩展名）
    """
    pdf = Path(pdf_path)
    pdf_dir = pdf.parent
    patent_name = _patent_name_from_doc(patent_doc)
    if patent_name == "unknown":
        patent_name = pdf.stem

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    folder_name = f"{patent_name}_{timestamp}"

    # 尝试 PDF 旁边
    primary = pdf_dir / folder_name
    if _is_writable(pdf_dir):
        primary.mkdir(parents=True, exist_ok=True)
        return primary

    # 降级到安装目录
    fallback = Path.cwd() / "data" / "output" / folder_name
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


# ── 历史运行扫描 ──────────────────────────────────────────────────────


def scan_runs(pdf_path: str, patent_doc=None) -> list[RunInfo]:
    """扫描 PDF 同级目录下匹配的历史运行文件夹。

    匹配规则：文件夹名以规范化的公布号开头（如 CN116417058_*）
    如果无法获取公布号，用 PDF 文件名匹配。

    Returns:
        按时间倒序排列的 RunInfo 列表
    """
    pdf = Path(pdf_path)
    if not pdf.exists():
        return []

    pdf_dir = pdf.parent
    patent_name = _patent_name_from_doc(patent_doc) if patent_doc else None
    if not patent_name or patent_name == "unknown":
        patent_name = pdf.stem

    runs: list[RunInfo] = []

    for entry in pdf_dir.iterdir():
        if not entry.is_dir():
            continue
        name = entry.name
        # 匹配: <专利名>_<YYYY-MM-DD_HH-MM-SS>
        if not name.startswith(patent_name + "_"):
            continue

        # 解析时间戳
        ts_match = re.search(r'(\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})$', name)
        if not ts_match:
            continue

        ts_str = ts_match.group(1)
        try:
            ts = datetime.strptime(ts_str, "%Y-%m-%d_%H-%M-%S")
        except ValueError:
            continue

        # 检查各阶段完成情况
        stages = {}
        for key, filename in STAGE_FILES.items():
            stages[key] = (entry / filename).exists()

        runs.append(RunInfo(
            path=entry,
            folder_name=name,
            timestamp=ts.strftime("%Y-%m-%d %H:%M:%S"),
            stages=stages,
        ))

    runs.sort(key=lambda r: r.timestamp, reverse=True)
    return runs


# ── 内部工具 ──────────────────────────────────────────────────────────


def _is_writable(directory: Path) -> bool:
    """检查目录是否可写。"""
    try:
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
        test_file = directory / ".write_test"
        test_file.touch()
        test_file.unlink()
        return True
    except (OSError, PermissionError):
        return False
