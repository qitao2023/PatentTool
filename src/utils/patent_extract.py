"""
专利文本抽取工具 — 从说明书描述中抽取"具体实施方式"（详述/实施例）部分。

依据（实测 Google Patents 下载数据）：
  - CN 专利：`具体实施方式` 是独立小节标题，其后是 实施例1/2/3... 直到文末
  - EN 专利：`DETAILED DESCRIPTION`（或 Detailed Description）同理
  - 该节是说明书的最后一个大节（占全文约 70-90%）
  - 描述文本中该节标题通常以段首/独立行的形式出现

具体实施方式公开了对比文件实际记载的实施方案，审查实践中用于判断
对比文件能否作为现有技术评述本申请的新颖性/创造性。
"""

import re

# 标题命中优先级：CN 标题 / EN 标题（按特异性排列）
_CN_HEADINGS = ["具体实施方式"]
_EN_HEADINGS = [
    "DETAILED DESCRIPTION OF EMBODIMENTS",
    "DETAILED DESCRIPTION",
    "Detailed Description",
]
_ALL_HEADINGS = _CN_HEADINGS + _EN_HEADINGS
_FALLBACK_RATIO = 0.4  # 未命中标题时，取描述后 60%（具体实施方式通常是主体）


def extract_embodiments(description: str, max_chars: int = 0,
                        keep_label: bool = False) -> str:
    """从说明书描述中抽取"具体实施方式"（详述/实施例）部分。

    Args:
        description: 说明书描述文本
        max_chars: 截断长度，0 = 不截断
        keep_label: True 时保留节标题作为标签（如 【具体实施方式】）

    Returns:
        抽取出的具体实施方式文本；无描述返回空串
    """
    if not description:
        return ""
    text = description.strip()

    # 1) 段首优先：标题通常自成一段（\n + 标题）
    start, used_heading = -1, ""
    for head in _ALL_HEADINGS:
        idx = text.find("\n" + head)
        if idx >= 0:
            start = idx + 1
            used_heading = head
            break
    # 2) 次选：全文首次出现（部分描述里标题内联在段落中）
    if start < 0:
        for head in _ALL_HEADINGS:
            idx = text.find(head)
            if idx >= 0:
                start = idx
                used_heading = head
                break
    # 3) 兜底：未命中任何标题，取后 60%
    if start < 0:
        start = int(len(text) * _FALLBACK_RATIO)
        used_heading = ""

    chunk = text[start:]
    if used_heading and chunk.startswith(used_heading):
        chunk = chunk[len(used_heading):]
    chunk = chunk.lstrip("：: \t\n").strip()

    if keep_label and used_heading:
        chunk = f"【{used_heading}】\n{chunk}"

    if max_chars:
        chunk = chunk[:max_chars]
    return chunk.strip()
