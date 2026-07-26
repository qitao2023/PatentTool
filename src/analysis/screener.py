"""
AI 对比文件粗筛模块 — 从大量摘要中筛选出最相关的 10-20 篇
"""
import json
from typing import Optional

from src.utils.config import Settings
from src.ai_client import AIClient


SCREENING_SYSTEM_PROMPT = """你是一名中国专利审查员。你需要从一批检索结果中，快速筛选出与本申请最相关、最可能用于评述新颖性或创造性的对比文件。

## 筛选标准

对每篇对比文件，综合考虑：
1. **技术领域相关性**：是否与本申请属于同一或相近技术领域（IPC 分类）
2. **技术特征重叠度**：摘要中描述的技术方案与本申请的核心发明点有多少重合
3. **预期可用性**：是否可能作为最接近的现有技术（用于三步法分析）

## 输出格式

纯 JSON 数组，按相关度从高到低排序，最多输出 {top_n} 篇：

```json
[
  {{
    "doc_id": "专利号",
    "title": "专利标题",
    "relevance_score": 95,
    "relevance_reason": "为什么相关（一句话）",
    "suggested_use": "novelty" 或 "inventive_step" 或 "background"
  }}
]
```

- relevance_score: 0-100, 高于 70 才值得看全文
- suggested_use: novelty=可用于新颖性评述, inventive_step=创造性三步法, background=背景技术"""


class PatentScreener:
    """AI 专利粗筛器"""

    def __init__(self, settings: Settings, provider: str | None = None):
        self.settings = settings
        self._provider = provider
        self._client: Optional[AIClient] = None

    def _get_client(self) -> AIClient:
        if self._client is None:
            self._client = AIClient(self.settings, provider=self._provider)
        return self._client

    def screen(self, patent_doc, abstracts: list[dict],
               top_n: int = 15) -> list[dict]:
        """
        从检索结果中筛选最相关的对比文件。

        Args:
            patent_doc: 本申请的 PatentDocument
            abstracts: 阶段1 检索到的摘要列表 (含 doc_id, title, abstract_snippet, ipc, applicant)
            top_n: 最多保留多少篇

        Returns:
            list[dict]: 筛选后的结果，附 relevance_score 和 relevance_reason
        """
        if not abstracts:
            return []

        client = self._get_client()

        # 构建本申请摘要
        patent_summary = self._build_patent_summary(patent_doc)

        # 构建对比文件列表
        candidates_text = self._build_candidates(abstracts)

        user_prompt = f"""## 本申请

{patent_summary}

## 检索结果（共 {len(abstracts)} 篇）

{candidates_text}

---

请筛选出与本申请最相关的对比文件，最多 {top_n} 篇。"""

        system_prompt = SCREENING_SYSTEM_PROMPT.replace("{top_n}", str(top_n))

        response = client.chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=4096,
            temperature=0.3,
        )

        return self._parse_response(response, abstracts)

    def _build_patent_summary(self, patent_doc) -> str:
        """构建本申请的技术方案摘要"""
        parts = []
        if patent_doc.title:
            parts.append(f"**发明名称**: {patent_doc.title}")
        if patent_doc.ipc_classifications:
            parts.append(f"**IPC分类**: {', '.join(patent_doc.ipc_classifications)}")
        if patent_doc.abstract:
            parts.append(f"**摘要**: {patent_doc.abstract}")
        if patent_doc.claims:
            # 只取前3项权利要求
            parts.append(f"**核心权利要求**:\n" + "\n".join(
                f"  {i+1}. {c}" for i, c in enumerate(patent_doc.claims[:3])
            ))
        return "\n\n".join(parts)

    def _build_candidates(self, abstracts: list[dict]) -> str:
        """构建候选文件列表文本"""
        lines = []
        for i, a in enumerate(abstracts):
            pub = a.get("publication_number", "?")
            title = a.get("title", "无标题")
            abs_text = a.get("abstract_snippet", "")[:300]
            ipc = a.get("ipc", "")
            applicant = a.get("applicant", "")

            lines.append(
                f"### [{i+1}] {pub}\n"
                f"- 标题: {title}\n"
                f"- IPC: {ipc}\n"
                f"- 申请人: {applicant}\n"
                f"- 摘要: {abs_text}\n"
            )
        return "\n".join(lines)

    def _parse_response(self, response: str, abstracts: list[dict]) -> list[dict]:
        """解析 AI 返回的筛选结果，映射回原始数据"""
        # 提取 JSON
        json_str = response.strip()
        if json_str.startswith("```"):
            lines = json_str.split("\n")
            json_str = "\n".join(lines[1:-1])

        try:
            selected = json.loads(json_str)
        except json.JSONDecodeError:
            # 尝试宽松解析
            import re
            match = re.search(r"\[.*\]", response, re.DOTALL)
            if match:
                try:
                    selected = json.loads(match.group(0))
                except json.JSONDecodeError:
                    return []
            else:
                return []

        if not isinstance(selected, list):
            return []

        # 构建 doc_id → abstract 的索引
        idx = {}
        for a in abstracts:
            doc_id = a.get("doc_id", "")
            pub_num = a.get("publication_number", "")
            idx[doc_id] = a
            idx[pub_num] = a

        result = []
        for item in selected:
            if not isinstance(item, dict):
                continue
            doc_id = item.get("doc_id", "")
            # 用 doc_id 匹配原始数据
            matched = idx.get(doc_id)
            if not matched:
                # 模糊匹配
                for key in idx:
                    if doc_id and doc_id in key:
                        matched = idx[key]
                        break

            if matched:
                result.append({
                    **matched,
                    "relevance_score": item.get("relevance_score", 0),
                    "relevance_reason": item.get("relevance_reason", ""),
                    "suggested_use": item.get("suggested_use", ""),
                })

        # 按相关度降序
        result.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)

        return result[:self.settings.analysis_top_n]

    FULLTEXT_SCORING_PROMPT = """你是一名中国专利审查员。请通读以下对比文件的全文，评估其与本申请的相关度。

## 评分标准

1. **技术领域** (0-25分)：对比文件是否属于相同或相近技术领域
2. **技术问题** (0-25分)：对比文件是否解决相同或类似的技术问题
3. **技术方案** (0-25分)：对比文件的技术手段与本申请的重叠程度
4. **证据强度** (0-25分)：对比文件的权利要求/说明书内容是否能清晰用于评述新颖性或创造性

## 输出格式

纯 JSON，不要其他文字：
```json
{"relevance_score": 85, "relevance_reason": "一句话说明相关原因"}
```"""

    def score_full_text(self, patent_doc, enriched_patents: list[dict],
                        signals=None) -> list[dict]:
        """
        逐篇通读全文，给出 0-100 相关度评分。

        Args:
            patent_doc: 本申请
            enriched_patents: Phase 3 获取全文后的专利列表（含 claims, description）

        Returns:
            附 fulltext_score 和 fulltext_reason 的专利列表
        """
        if not enriched_patents:
            return []

        client = self._get_client()
        patent_summary = self._build_patent_summary(patent_doc)
        total = len(enriched_patents)

        for i, p in enumerate(enriched_patents):
            pub = p.get("publication_number", "?")
            title = p.get("title", "")
            claims = p.get("claims", "")[:5000]
            description = p.get("description", "")[:5000]
            abstract = p.get("abstract", "")[:2000]

            full_text = f"""## 对比文件 [{i+1}/{total}]

**公开号**: {pub}
**标题**: {title}
**摘要**: {abstract}

**权利要求**:
{claims if claims else '(无)'}

**说明书摘要**:
{description if description else '(无)'}"""

            if signals:
                signals.log.emit("INFO",
                    f"  AI 通读全文 [{i+1}/{total}]: {pub} ...")

            try:
                response = client.chat(
                    system_prompt=self.FULLTEXT_SCORING_PROMPT,
                    user_prompt=f"## 本申请\n\n{patent_summary}\n\n{full_text}",
                    max_tokens=512,
                    temperature=0.2,
                )
                # 解析 JSON
                import json as json_module
                import re
                resp = response.strip()
                match = re.search(r'\{[^}]+\}', resp)
                if match:
                    data = json_module.loads(match.group(0))
                    p["fulltext_score"] = data.get("relevance_score", 0)
                    p["fulltext_reason"] = data.get("relevance_reason", "")
                else:
                    p["fulltext_score"] = 0
                    p["fulltext_reason"] = "解析失败"
            except Exception as e:
                p["fulltext_score"] = 0
                p["fulltext_reason"] = f"评分失败: {e}"
                if signals:
                    signals.log.emit("WARN",
                        f"  {pub} 全文评分失败: {e}")

            if signals:
                score = p.get("fulltext_score", 0)
                reason = p.get("fulltext_reason", "")[:60]
                signals.log.emit("INFO",
                    f"  [{i+1}/{total}] {pub}: 相关度 {score}分 - {reason}")

        # 按全文评分的相关度降序
        enriched_patents.sort(
            key=lambda x: x.get("fulltext_score", 0), reverse=True)

        return enriched_patents
