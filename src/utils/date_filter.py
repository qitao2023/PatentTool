"""
对比文件日期淘汰 — 公开日 >= 本申请截止日的对比文件直接淘汰。

依据《专利审查指南》：现有技术指"申请日以前"为公众所知的技术，
公开日等于申请日（同日公开）也不构成现有技术，因此淘汰条件是 **公开日 >= 截止日**。

截止日取 min(申请日, 优先权日)：本申请声明优先权时，现有技术的判定
以优先权日为准（《审查指南》第二部分第三章）。
公开日缺失/无法解析 → 无法判定，一律保留，不误杀。

调用时机：
  1. 阶段1 摘要合并后（下载前）—— 主力过滤，省下载与 AI 广筛
  2. 阶段3 下载后（广筛前）—— 二次校验，兜住搜索时公开日缺失的漏网之鱼
"""
import re
from datetime import date

# 英文月份表（3字母小写 → 月份号）
_EN_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _try_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def parse_patent_date(text) -> date | None:
    """宽容解析专利日期文本，失败返回 None（调用方按"公开日未知"保留）。

    支持格式：
      2023-05-01 / 2023.05.01 / 2023/05/01 / 2023年05月01日 / 2023年5月1日
      20230501（8位纯数字）
      May 1, 2023 / 1 May 2023 / May 1 2023
    """
    if not text:
        return None
    s = str(text).strip()
    if not s:
        return None

    # 中文/连字符/点/斜杠分隔的完整日期（年份在前）
    m = re.search(r"(\d{4})\s*[-/.年]\s*(\d{1,2})\s*[-/.月]\s*(\d{1,2})", s)
    if m:
        return _try_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # 8 位纯数字：20230501
    m = re.fullmatch(r"(\d{4})(\d{2})(\d{2})", s)
    if m:
        return _try_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    # 英文月份：May 1, 2023
    m = re.search(r"([A-Za-z]{3,9})\s+(\d{1,2})[,\s]+(\d{4})", s)
    if m:
        month = _EN_MONTHS.get(m.group(1).lower()[:3])
        if month:
            return _try_date(int(m.group(3)), month, int(m.group(2)))

    # 英文月份在前：1 May 2023
    m = re.search(r"(\d{1,2})\s+([A-Za-z]{3,9})[,\s]+(\d{4})", s)
    if m:
        month = _EN_MONTHS.get(m.group(2).lower()[:3])
        if month:
            return _try_date(int(m.group(3)), month, int(m.group(1)))

    return None


def effective_cutoff_date(application_date: str = "",
                          priority_date: str = "") -> date | None:
    """本申请截止日：min(申请日, 优先权日)；两者都无 → None（不启用淘汰）。"""
    ad = parse_patent_date(application_date)
    pd = parse_patent_date(priority_date)
    candidates = [d for d in (ad, pd) if d is not None]
    if not candidates:
        return None
    return min(candidates)


def _get_publication_date(patent) -> str:
    """兼容 dict 与对象两种数据形态地取公开日。"""
    if isinstance(patent, dict):
        return patent.get("publication_date") or ""
    return getattr(patent, "publication_date", "") or ""


def is_eliminated_by_date(patent, cutoff_date: date | None,
                          publication_date_key: str = "publication_date") -> bool:
    """True = 该对比文件应淘汰（公开日明确 >= 截止日）。

    公开日缺失 / 无法解析 / 截止日未知 → False（保留，不误杀）。
    """
    if cutoff_date is None:
        return False
    pub_text = _get_publication_date(patent)
    d = parse_patent_date(pub_text)
    if d is None:
        return False
    return d >= cutoff_date


def filter_by_application_date(patents: list, cutoff_date: date | None,
                               publication_date_key: str = "publication_date"):
    """淘汰公开日 >= 截止日的对比文件，其余保留。

    Returns:
        (kept, eliminated): 保留列表、淘汰列表
    """
    kept, eliminated = [], []
    for p in patents:
        if is_eliminated_by_date(p, cutoff_date, publication_date_key):
            eliminated.append(p)
        else:
            kept.append(p)
    return kept, eliminated
