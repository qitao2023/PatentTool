"""
AI 审查意见通知书撰写模块

基于：
  - 本申请的权利要求书和说明书
  - 检索到的对比文件（含全文）
  - 三步法（创造性评述标准方法）

生成 CNIPA 格式的审查意见通知书。
"""
import json
from typing import Optional

from src.utils.config import Settings
from src.ai_client import AIClient


OA_SYSTEM_PROMPT = """你是一名中国国家知识产权局（CNIPA）专利审查员。
你需要根据本申请和检索到的对比文件，撰写一份正式的审查意见通知书。

## 评述方法：三步法

### 第一步：确定最接近的现有技术
从对比文件中选出与本申请最相关的一篇作为"最接近的现有技术"（通常是对比文件1）。
说明为什么选择它——技术领域相同、公开的技术特征最多等。

### 第二步：确定区别特征和实际解决的技术问题
将本申请的权利要求与最接近的现有技术对比，列出区别技术特征。
基于区别特征确定本申请实际解决的技术问题。

### 第三步：判断是否显而易见
判断区别特征是否被其他对比文件公开，或者是否属于本领域的公知常识。
如果区别特征在对比文件2或其他文件中公开，且作用相同，则认为权利要求不具备创造性。

## 输出格式要求

请严格按照以下 CNIPA 审查意见通知书的格式输出（Markdown）：

---
# 审查意见通知书

## 一、本申请基本信息
- 发明名称: ...
- 申请号: ...
- 申请人: ...

## 二、检索情况
简述检索数据库、检索式、检索结果。

## 三、对比文件列表
| 编号 | 公布号 | 标题 | 相关度 |
|---|---|---|---|
| 1 | ... | ... | 最接近现有技术 |
| 2 | ... | ... | 用于结合评述创造性 |

## 四、权利要求评述

### 4.1 独立权利要求 1

**权利要求 1**:
(引用权利要求原文)

**对比文件 1 公开内容**:
(指出对比文件1公开了哪些特征，对应权利要求1的哪些部分)

**区别技术特征**:
(列出对比文件1未公开的特征)

**该权利要求实际解决的技术问题**:
(基于区别特征确定)

**对比文件 2 公开内容**:
(如果适用，指出对比文件2公开了区别特征，以及其作用)

**结论**:
权利要求1不具备创造性（或具备创造性），不符合专利法第22条第3款的规定。

### 4.2 从属权利要求 2-N
(逐一评述)

## 五、总结

| 权利要求 | 新颖性 | 创造性 | 结论 |
|---|---|---|---|
| 1 | ... | ... | ... |
| 2 | ... | ... | ... |

## 六、审查员建议

(给出修改建议或后续步骤)
---

## 注意事项
1. 每个权利要求都要评述，不能遗漏
2. 对比文件的引用要具体（指出段落、行号）
3. 三步法的每一步逻辑要清晰完整
4. 结论要明确：具备/不具备新颖性/创造性
5. 语言要正式、规范，符合审查员文风"""


class OAWriter:
    """审查意见通知书 AI 撰写器"""

    def __init__(self, settings: Settings, provider: str | None = None):
        self.settings = settings
        self._provider = provider
        self._client: Optional[AIClient] = None

    def _get_client(self) -> AIClient:
        if self._client is None:
            self._client = AIClient(self.settings, provider=self._provider)
        return self._client

    def _get_system_prompt(self) -> str:
        """System Prompt：优先使用 office_action 提示词方案，缺失时回退内置硬编码。"""
        prompt = self.settings.get_prompt_text("office_action", "system")
        return prompt if prompt else OA_SYSTEM_PROMPT

    def write(self, patent_doc, comparisons: list[dict],
              dedup_results: list[dict], options: dict | None = None) -> str:
        """
        撰写审查意见通知书。

        Args:
            patent_doc: 本申请的 PatentDocument
            comparisons: AI 对比分析结果（已有相关度评分和对比）
            dedup_results: 去重后的对比文件列表（含全文）
            options: 可选，撰写选项 {keep_contact: bool, mark_changes: bool,
                     d1_pub: str, d2_pub: str}

        Returns:
            str: Markdown 格式的审查意见通知书，末尾附「检索对比文件清单」附录
            （记录检索到的最相关 ≤10 篇对比文件的公布号，供归档备查）
        """
        client = self._get_client()
        options = options or {}

        # 构建本申请信息
        patent_text = self._build_patent_text(patent_doc)

        # 构建对比文件信息（只取有全文、相关度高的）
        top_docs = self._select_top_docs(dedup_results, max_count=5)
        docs_text = self._build_comparison_docs(top_docs)

        # 对比文件角色指定
        d1_pub = options.get("d1_pub", "")
        d2_pub = options.get("d2_pub", "")
        role_text = "（由你判断最接近的现有技术并说明理由）"
        if d1_pub and d2_pub:
            role_text = (f"对比文件 {d1_pub} 为最接近的现有技术；"
                         f"权利要求以对比文件 {d2_pub} 辅助评述创造性。")
        elif d1_pub:
            role_text = f"对比文件 {d1_pub} 为最接近的现有技术。"

        # 输出要求
        keep_contact = options.get("keep_contact", False)
        mark_changes = options.get("mark_changes", False)
        contact_text = "是" if keep_contact else "否"
        mark_text = "是" if mark_changes else "否"

        user_prompt = f"""## 本申请

{patent_text}

## 对比文件（按相关度排序）

{docs_text}

---

## 对比文件角色指定

{role_text}

## 输出要求

- 是否需要 DOCX：由接收方按版式规范生成
- 是否保留联系方式：{contact_text}
- 是否标记修改：{mark_text}

## 撰写要求

请根据以上信息，按照三步法撰写审查意见通知书，并遵循 System Prompt 中的全部规则：

1. 每个权利要求都要评述，结论明确；
2. 对比文件引用要具体（指出段号/页码/附图）；
3. 三步法逻辑要完整（区别特征→实际技术问题→结合动机→法律结论）；
4. 交付前执行引用核验：每一处"对比文件X公开（参见……）"都须与原文核对，不臆造公开内容、段号、公开日。"""

        response = client.chat(
            system_prompt=self._get_system_prompt(),
            user_prompt=user_prompt,
            max_tokens=16384,
            temperature=0.3,
        )

        # 末尾追加检索对比文件清单附录（前 10 篇，按相关度排序）。
        # 直接取检索结果数据，不经 AI 生成，避免编造公布号。
        appendix = self._build_appendix(dedup_results, max_count=10)
        if appendix:
            response = response.rstrip() + appendix

        return response

    def _build_appendix(self, dedup_results: list[dict],
                        max_count: int = 10) -> str:
        """构建通知书末尾的「检索对比文件清单」附录。

        从检索结果中挑出相关度最高的不超过 max_count 篇对比文件，
        记录其公布号（编号）与标题，供审查员归档备查。

        Args:
            dedup_results: 去重后的对比文件列表
            max_count: 最多收录篇数（默认 10）

        Returns:
            str: Markdown 附录段落；无可用对比文件时返回空字符串
        """
        scored = [d for d in dedup_results if d.get("publication_number")]
        scored.sort(key=lambda x: x.get("relevance_score", 0) or 0, reverse=True)
        top = scored[:max_count]
        if not top:
            return ""

        rows = []
        for i, d in enumerate(top, 1):
            pub = d.get("publication_number", "")
            title = (d.get("title", "") or "").replace("|", "\\|")
            title_cell = title if title else "—"
            score = d.get("relevance_score", "")
            score_cell = str(score) if score not in ("", None) else ""
            rows.append(f"| {i} | {pub} | {title_cell} | {score_cell} |")

        header = (
            "\n\n---\n\n"
            f"## 附：检索对比文件清单（前 {len(top)} 篇）\n\n"
            "> 以下为本轮检索中相关度最高的对比文件公布号记录，"
            "供审查员归档备查，不构成通知书正文。\n\n"
            "| 序号 | 公布号 | 标题 | 相关度 |\n"
            "|---|---|---|---|\n"
        )
        return header + "\n".join(rows)

    def _build_patent_text(self, patent_doc) -> str:
        """构建本申请完整文本"""
        parts = []
        if patent_doc.title:
            parts.append(f"## 发明名称\n{patent_doc.title}")
        if patent_doc.publication_number:
            parts.append(f"**公布号**: {patent_doc.publication_number}")
        if patent_doc.application_number:
            parts.append(f"**申请号**: {patent_doc.application_number}")
        if getattr(patent_doc, "application_date", "") or getattr(patent_doc, "publication_date", ""):
            ad = getattr(patent_doc, "application_date", "") or ""
            pd_ = getattr(patent_doc, "publication_date", "") or ""
            parts.append(f"**申请日**: {ad or '未知'} | **公开日**: {pd_ or '未知'}")
        if patent_doc.applicants:
            parts.append(f"**申请人**: {', '.join(patent_doc.applicants)}")
        if patent_doc.inventors:
            parts.append(f"**发明人**: {', '.join(patent_doc.inventors)}")
        if patent_doc.ipc_classifications:
            parts.append(f"**IPC**: {', '.join(patent_doc.ipc_classifications)}")

        if patent_doc.abstract:
            parts.append(f"\n### 摘要\n{patent_doc.abstract}")

        if patent_doc.claims:
            parts.append(f"\n### 权利要求书 ({len(patent_doc.claims)} 项)")
            for i, claim in enumerate(patent_doc.claims):
                parts.append(f"**{i+1}.** {claim}")

        if patent_doc.description:
            desc = patent_doc.description[:3000]
            parts.append(f"\n### 说明书（节选）\n{desc}")

        return "\n\n".join(parts)

    def _select_top_docs(self, dedup_results: list[dict],
                         max_count: int = 5) -> list[dict]:
        """选出最相关的对比文件（有全文 + 相关度高的优先）"""
        # 优先有全文、高相关度的
        with_full = [d for d in dedup_results if d.get("full_text") or d.get("claims")]
        without_full = [d for d in dedup_results
                        if not d.get("full_text") and not d.get("claims")]

        # 按相关度排序
        with_full.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

        selected = with_full[:max_count]
        # 不够则补充
        if len(selected) < max_count:
            without_full.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
            selected.extend(without_full[:max_count - len(selected)])

        return selected

    def _build_comparison_docs(self, docs: list[dict]) -> str:
        """构建对比文件文本"""
        parts = []
        for i, d in enumerate(docs):
            pub = d.get("publication_number", f"未知-{i+1}")
            title = d.get("title", "")
            score = d.get("relevance_score", "?")
            reason = d.get("relevance_reason", "")
            ipc = d.get("ipc", "")
            applicant = d.get("applicant", "")
            abstract = d.get("abstract", "") or d.get("abstract_snippet", "")
            claims = d.get("claims", "")[:5000]
            description = d.get("description", "")[:5000]

            pub_date = d.get("publication_date", "") or ""
            app_num = d.get("application_number", "") or ""
            date_line = f"公开日: {pub_date} | 申请号: {app_num}" if (pub_date or app_num) else "公开日/申请号: 未知"

            parts.append(f"""### 对比文件 {i+1}: {pub} (相关度: {score})

- **标题**: {title}
- **公开日/申请号**: {date_line}
- **IPC**: {ipc}
- **申请人**: {applicant}
- **相关理由**: {reason}

#### 摘要
{abstract[:1000]}

#### 权利要求
{claims if claims else "（未获取）"}

#### 说明书（节选）
{description if description else "（未获取）"}
""")
        return "\n\n---\n\n".join(parts)
