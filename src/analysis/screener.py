"""
AI 对比文件粗筛模块 — 分层筛选 + 批量全文评分
"""
import json as json_module
import re as re_module
from typing import Optional

from src.utils.config import Settings
from src.ai_client import AIClient


SCREENING_SYSTEM_PROMPT = """你是一名中国专利审查员。你需要从一批检索结果中，筛选出与本申请最相关、最可能用于评述新颖性或创造性的对比文件。

## 筛选标准（宁可多留，不可漏掉）

1. **技术领域相关性（权重最高）**：相同 IPC 大类 或 相同材料/器件体系（如IGZO、GaN、SiC等）的专利，即使摘要关键词不完全匹配，至少给 60 分
2. **技术特征重叠度**：摘要中描述的技术方案与本申请的核心发明点有多少重合
3. **预期可用性**：是否可能作为最接近的现有技术

## 评分指导
- 相同 IPC 大类 + 相同器件类型（如都是IGZO TFT）：≥70分
- 相同 IPC 大类 + 相关器件类型：≥60分
- 不同 IPC 但技术方案相关：50-70分
- 完全不相关：<40分

## 输出格式
纯 JSON 数组，按相关度降序，最多 {top_n} 篇：
```json
[{"publication_number":"公开号","title":"标题","relevance_score":95,"relevance_reason":"原因","suggested_use":"novelty/inventive_step/background"}]
```"""


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
               top_n: int = 15, max_batch: int = 40) -> list[dict]:
        """分层筛选。批量超过 max_batch 时分批。"""
        if not abstracts:
            return []

        client = self._get_client()
        patent_summary = self._build_patent_summary(patent_doc)
        total = len(abstracts)

        all_scored = []
        batch_size = min(max_batch, total)
        num_batches = (total + batch_size - 1) // batch_size

        for batch_idx in range(num_batches):
            start = batch_idx * batch_size
            end = min(start + batch_size, total)
            batch = abstracts[start:end]
            batch_target = min(top_n, len(batch))
            batch_min = max(3, batch_target // 2)

            candidates_text = self._build_candidates(batch)
            user_prompt = f"""## 本申请
{patent_summary}

## 检索结果 第{batch_idx+1}/{num_batches}批
{candidates_text}

---
必须输出接近 {batch_target} 篇（至少 {batch_min} 篇），按相关度降序。
评分规则：相同 IPC 大类 + 相同器件类型 ≥70分，相同 IPC 大类 ≥60分。"""

            system_prompt = SCREENING_SYSTEM_PROMPT.replace("{top_n}", str(batch_target))
            response = client.chat(system_prompt=system_prompt, user_prompt=user_prompt,
                                   max_tokens=8192, temperature=0.3)
            scored = self._parse_response(response, batch)
            all_scored.extend(scored)

        all_scored.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
        return all_scored[:top_n]

    def score_full_text(self, patent_doc, enriched_patents: list[dict],
                        signals=None) -> list[dict]:
        """批量评分：一次提交所有对比文件摘要，AI 返回所有评分。"""
        if not enriched_patents:
            return []

        client = self._get_client()
        patent_summary = self._build_patent_summary(patent_doc)
        total = len(enriched_patents)

        all_docs = []
        for i, p in enumerate(enriched_patents):
            pub = p.get("publication_number", "?")
            title = p.get("title", "")
            abstract = (p.get("abstract") or p.get("abstract_snippet") or "")[:500]
            all_docs.append(f"[{i+1}] {pub}: {title}\n   摘要: {abstract}")
            if signals:
                signals.log.emit("INFO", f"  AI 通读全文 [{i+1}/{total}]: {pub} ...")

        batch_prompt = f"## 本申请\n{patent_summary}\n\n## 对比文件（共{total}篇）\n\n{chr(10).join(all_docs)}\n\n---\n请给每篇评分。输出JSON数组：[{{\"publication_number\":\"公开号\",\"relevance_score\":85,\"relevance_reason\":\"原因\"}}]。必须包含全部{total}篇。"

        try:
            response = client.chat(
                system_prompt="你是专利评分机器。只输出JSON数组，别无其他。",
                user_prompt=batch_prompt, max_tokens=8192, temperature=0.2)
            resp = response.strip()
            if resp.startswith("```"):
                resp = re_module.sub(r'^```\w*\n?', '', resp)
                resp = re_module.sub(r'\n?```$', '', resp)
            match = re_module.search(r'\[[\s\S]*\]', resp)
            if match:
                scores = json_module.loads(match.group(0))
                score_map = {s.get("publication_number", ""): s for s in scores if isinstance(s, dict)}
                for p in enriched_patents:
                    pub = p.get("publication_number", "")
                    s = score_map.get(pub, {})
                    p["fulltext_score"] = s.get("relevance_score", p.get("relevance_score", 30))
                    p["fulltext_reason"] = s.get("relevance_reason", "评分完成")
            else:
                for p in enriched_patents:
                    p["fulltext_score"] = p.get("relevance_score", 30)
                    p["fulltext_reason"] = f"JSON解析失败: {resp[:80]}"
        except Exception as e:
            for p in enriched_patents:
                p["fulltext_score"] = p.get("relevance_score", 30)
                p["fulltext_reason"] = f"评分异常: {str(e)[:40]}"

        if signals:
            for i, p in enumerate(enriched_patents):
                signals.log.emit("INFO",
                    f"  [{i+1}/{total}] {p.get('publication_number','?')}: "
                    f"相关度 {p.get('fulltext_score',0)}分")

        enriched_patents.sort(key=lambda x: x.get("fulltext_score", 0), reverse=True)
        return enriched_patents

    # ================================================================
    # 内部方法
    # ================================================================

    def _build_patent_summary(self, patent_doc) -> str:
        parts = []
        if patent_doc.title:
            parts.append(f"**发明名称**: {patent_doc.title}")
        if patent_doc.ipc_classifications:
            parts.append(f"**IPC分类**: {', '.join(patent_doc.ipc_classifications)}")
        if patent_doc.abstract:
            parts.append(f"**摘要**: {patent_doc.abstract}")
        if patent_doc.claims:
            parts.append(f"**核心权利要求**:\n" + "\n".join(
                f"  {i+1}. {c}" for i, c in enumerate(patent_doc.claims[:3])))
        return "\n\n".join(parts)

    def _build_candidates(self, abstracts: list[dict]) -> str:
        lines = []
        for i, a in enumerate(abstracts):
            pub = a.get("publication_number", "?")
            title = a.get("title", "无标题")
            abs_text = a.get("abstract_snippet", "")[:300]
            ipc = a.get("ipc", "")
            applicant = a.get("applicant", "")
            lines.append(f"### [{i+1}] {pub}\n- 标题: {title}\n- IPC: {ipc}\n- 申请人: {applicant}\n- 摘要: {abs_text}\n")
        return "\n".join(lines)

    def _parse_response(self, response: str, abstracts: list[dict]) -> list[dict]:
        json_str = response.strip()
        if json_str.startswith("```"):
            lines = json_str.split("\n")
            json_str = "\n".join(lines[1:-1])
        try:
            selected = json_module.loads(json_str)
        except json_module.JSONDecodeError:
            match = re_module.search(r"\[[\s\S]*\]", response, re_module.DOTALL)
            if match:
                try:
                    selected = json_module.loads(match.group(0))
                except json_module.JSONDecodeError:
                    return []
            else:
                return []
        if not isinstance(selected, list):
            return []

        idx = {}
        for a in abstracts:
            idx[a.get("publication_number", "")] = a
            idx[a.get("publication_number", "")] = a

        result = []
        for item in selected:
            if not isinstance(item, dict):
                continue
            publication_number = item.get("publication_number", "")
            matched = idx.get(publication_number)
            if not matched:
                for key in idx:
                    if publication_number and publication_number in key:
                        matched = idx[key]
                        break
            if matched:
                result.append({**matched, "relevance_score": item.get("relevance_score", 0),
                               "relevance_reason": item.get("relevance_reason", ""),
                               "suggested_use": item.get("suggested_use", "")})
        result.sort(key=lambda x: x.get("relevance_score", 0), reverse=True)
        return result
