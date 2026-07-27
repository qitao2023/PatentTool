"""
文本清洗工具：去除 PATENTSCOPE 页面抓取文本中的导航垃圾和多余空白。
"""

import re


def clean_patent_html_text(text: str) -> str:
    """清洗从 WIPO PATENTSCOPE 页面抓取的专利文本。

    移除内容：
    - 页面导航（永久链接面包屑）
    - 语言选择器（机器翻译WIPO Translate / Machine translation）
    - OCR 提示
    - [ZH] / [EN] 语言标记
    - 所有多余空白字符（\\xa0, \\n, \\t, 连续空格）

    适用于 claims 和 description 字段。
    清洗后文本为紧凑单行，给 AI 消费以节省 token。
    """
    if not text:
        return ""

    # ── 步骤 1：移除 WIPO 页面导航垃圾 ──────────────────────────────
    # 匹配从可选 "永久链接" 开始，经过语言选择器、OCR 提示，到 [ZH ] 标记为止
    # 处理中英文两种界面：
    #   中文：永久链接...机器翻译WIPO Translate...注：相关文本通过自动光符识别... [ZH ]
    #   英文：Machine translation... [ZH ]
    text = re.sub(
        r'(?:永久链接[\s\S]*?)?'
        r'(?:机器翻译WIPO\s*Translate|Machine\s*translation)[\s\S]*?'
        r'\[ZH\s*\]\s*',
        '', text, count=1
    )

    # ── 步骤 2：空白字符归一化 ──────────────────────────────────────
    # \xa0（&nbsp;）→ 普通空格
    text = text.replace('\xa0', ' ')
    # 所有连续空白 → 单个空格（JSON 给 AI 消费，不需要段落格式）
    text = re.sub(r'\s+', ' ', text)

    # ── 步骤 3：首尾整理 ────────────────────────────────────────────
    text = text.strip()

    return text


def clean_patent_full_text(text: str) -> str:
    """清洗 full_text 字段（页面 body.innerText）。

    与 clean_patent_html_text 类似，但 full_text 可能包含更多页面 UI 文本。
    """
    if not text:
        return ""
    # 归一化空白
    text = text.replace('\xa0', ' ')
    text = re.sub(r'\s+', ' ', text)
    text = text.strip()
    return text
