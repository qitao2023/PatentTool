"""
专利PDF解析模块 - 将PDF转为结构化Markdown文本
"""
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import fitz  # PyMuPDF

from src.utils.paths import normalize_patent_number


@dataclass
class PatentDocument:
    """结构化专利文档"""
    title: str = ""
    abstract: str = ""
    claims: list[str] = field(default_factory=list)
    description: str = ""
    ipc_classifications: list[str] = field(default_factory=list)
    applicants: list[str] = field(default_factory=list)
    inventors: list[str] = field(default_factory=list)
    publication_number: str = ""
    application_number: str = ""
    priority_date: str = ""
    publication_date: str = ""
    full_text_markdown: str = ""


class PatentPDFExtractor:
    """PDF专利文件提取器"""

    # 中英文专利文档的段落标记
    SECTION_PATTERNS = {
        "title_cn": re.compile(r"发明名称\s*[:：]?\s*(.+)"),
        "title_en": re.compile(r"(?:Title|Invention Name)\s*[:：]?\s*(.+)", re.IGNORECASE),
        "abstract_cn": re.compile(r"摘\s*要"),
        "abstract_en": re.compile(r"ABSTRACT", re.IGNORECASE),
        "claims_cn": re.compile(r"权利要求书"),
        "claims_en": re.compile(r"(?:CLAIMS|What is claimed is)", re.IGNORECASE),
        "description_cn": re.compile(r"说\s*明\s*书"),
        "description_en": re.compile(r"(?:DESCRIPTION|DETAILED DESCRIPTION)", re.IGNORECASE),
        "ipc": re.compile(r"(?:Int\.Cl\.|Int\.Cl\s*:|IPC)\s*([\w/\d.]+)", re.IGNORECASE),
        "publication_number": re.compile(r"(?:CN|US|EP|WO|JP|KR)\s*\d+[A-Z]?\d*"),
        "applicant": re.compile(r"(?:申请人|Applicant)\s*[:：]?\s*(.+)", re.IGNORECASE),
        "inventor": re.compile(r"(?:发明人|Inventor)\s*[:：]?\s*(.+)", re.IGNORECASE),
    }

    # 权利要求号匹配
    CLAIM_NUM = re.compile(r"^\s*(\d+)[.、．]\s*")

    def __init__(self, pdf_path: str | Path):
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF文件不存在: {pdf_path}")

    def extract(self) -> PatentDocument:
        """提取PDF中的专利信息"""
        doc = fitz.open(str(self.pdf_path))

        # 提取全部文本
        full_text_pages = []
        for page in doc:
            text = page.get_text("text")
            full_text_pages.append(text)

        full_text = "\n===== Page {} =====\n".format(1) + \
                    "\n===== Page {} =====\n".join(full_text_pages)

        # 第一页文本（用于提取元数据，避免引文干扰）
        first_page_text = full_text_pages[0] if full_text_pages else ""

        # 逐页格式化（保留页码标记方便LLM理解）
        pages_md = []
        for i, page in enumerate(doc):
            text = page.get_text("text")
            pages_md.append(f"--- (Page {i + 1}) ---\n{text}")
        full_md = "\n".join(pages_md)

        doc.close()

        patent = PatentDocument()
        patent.full_text_markdown = full_md

        # 尝试使用 PyMuPDF4LLM 获取更好的Markdown（如可用）
        try:
            import pymupdf4llm
            md_text = pymupdf4llm.to_markdown(
                str(self.pdf_path),
                page_chunks=False,
                write_images=False,
                ignore_graphics=True,
            )
            if md_text and len(md_text) > len(patent.full_text_markdown):
                patent.full_text_markdown = md_text
        except ImportError:
            pass  # 已使用基础文本

        # 元数据只从第一页提取（公布号/申请号/申请人等不会出现在后续页码）
        self._parse_metadata(first_page_text, patent)
        # 如果第一页没找到公布号，扩大到前两页
        if not patent.publication_number and len(full_text_pages) >= 2:
            self._parse_metadata(first_page_text + "\n" + full_text_pages[1], patent)
        # 段落从全文提取
        self._parse_sections(full_text, patent)

        return patent

    def _parse_metadata(self, text: str, patent: PatentDocument):
        """解析元数据：公布号、申请人等"""
        # 公布号
        for line in text.split("\n"):
            if "公布号" in line or "申请公布号" in line or "Publication Number" in line:
                m = re.search(r"(\w{2}\s*\d+[A-Z]?\d*)", line)
                if m:
                    patent.publication_number = normalize_patent_number(m.group(1))
                    break
        if not patent.publication_number:
            # 放宽匹配
            for m in self.SECTION_PATTERNS["publication_number"].finditer(text):
                patent.publication_number = normalize_patent_number(m.group(0))
                break

        # 申请号
        for line in text.split("\n"):
            if "申请号" in line or "Application Number" in line:
                m = re.search(r"(\w{2}\s*\d+[.X]?\d*)", line)
                if m:
                    patent.application_number = m.group(1).strip()
                    break

        # 申请人
        for line in text.split("\n"):
            m = self.SECTION_PATTERNS["applicant"].search(line)
            if m:
                patent.applicants.append(m.group(1).strip())

        # 发明人
        for line in text.split("\n"):
            m = self.SECTION_PATTERNS["inventor"].search(line)
            if m:
                patent.inventors.append(m.group(1).strip())

        # IPC
        ipc_list = self.SECTION_PATTERNS["ipc"].findall(text)
        patent.ipc_classifications = [ipc.strip() for ipc in ipc_list]

        # 公开日
        for line in text.split("\n"):
            if "公开日" in line or "申请公布日" in line or "Publication Date" in line:
                m = re.search(r"(\d{4}[.\-年]\d{1,2}[.\-月]\d{1,2}[日]?)", line)
                if m:
                    patent.publication_date = m.group(1).strip()
                    break

        # 优先权日
        for line in text.split("\n"):
            if "优先权" in line or "Priority" in line:
                m = re.search(r"(\d{4}[.\-年]\d{1,2}[.\-月]\d{1,2}[日]?)", line)
                if m:
                    patent.priority_date = m.group(1).strip()
                    break

    def _parse_sections(self, text: str, patent: PatentDocument):
        """解析专利各部分：标题、摘要、权利要求、说明书"""
        lines = text.split("\n")
        current_section = None
        claim_texts = []
        in_claims = False
        in_description = False
        in_abstract = False
        description_parts = []

        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue

            # 检测段落边界
            if self.SECTION_PATTERNS["claims_cn"].search(stripped) or \
               self.SECTION_PATTERNS["claims_en"].search(stripped):
                current_section = "claims"
                in_claims = True
                in_description = False
                in_abstract = False
                continue
            elif self.SECTION_PATTERNS["description_cn"].search(stripped) or \
                 self.SECTION_PATTERNS["description_en"].search(stripped):
                current_section = "description"
                in_claims = False
                in_description = True
                in_abstract = False
                continue
            elif self.SECTION_PATTERNS["abstract_cn"].search(stripped) or \
                 self.SECTION_PATTERNS["abstract_en"].search(stripped):
                current_section = "abstract"
                in_claims = False
                in_description = False
                in_abstract = True
                continue

            # 标题（通常在摘要之前的第一页）
            if "发明名称" in stripped or "Title" in stripped:
                m = self.SECTION_PATTERNS["title_cn"].search(stripped) or \
                    self.SECTION_PATTERNS["title_en"].search(stripped)
                if m:
                    patent.title = m.group(1).strip()
                # 也可能在下一行
                elif not patent.title:
                    patent.title = stripped

            # 摘要内容
            if in_abstract:
                # 摘要通常到权利要求书之前结束
                if patent.abstract:
                    patent.abstract += " " + stripped
                else:
                    patent.abstract = stripped
                # 简单的结束检测
                if len(patent.abstract) > 50 and ("权利要求" in stripped or "权利要求书" in stripped):
                    in_abstract = False

            # 权利要求
            if in_claims:
                m = self.CLAIM_NUM.match(stripped)
                if m:
                    num = m.group(1)
                    text_after = stripped[m.end():]
                    claim_texts.append((num, text_after))
                elif claim_texts:
                    # 续行
                    last_num, last_text = claim_texts[-1]
                    claim_texts[-1] = (last_num, last_text + " " + stripped)

            # 说明书
            if in_description and current_section == "description":
                # 跳过页码行
                if not re.match(r"^\d+/\d+页$", stripped) and not re.match(r"^=\s*Page\s+\d+\s*=", stripped):
                    description_parts.append(stripped)

        # 赋值
        if claim_texts:
            patent.claims = [f"{num}. {text}" for num, text in claim_texts]

        if description_parts:
            patent.description = "\n".join(description_parts)

        # 如果title为空，尝试从文件名提取
        if not patent.title:
            patent.title = self.pdf_path.stem
