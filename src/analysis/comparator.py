"""
专利对比分析器 - 使用AI API(DeepSeek/Kimi)进行专利对比分析
"""
import json
from typing import Optional

from src.utils.config import Settings
from src.pdf_extractor.extractor import PatentDocument
from src.ai_client import AIClient
from src.utils.prompts import (
    load_prompt,
    render_template,
    COMPARISON_FALLBACK_SYSTEM_PROMPT,
    COMPARISON_FALLBACK_USER_PROMPT,
)


class PatentComparator:
    """专利对比分析引擎"""

    def __init__(self, settings: Settings, provider: str | None = None):
        self.settings = settings
        self._provider = provider
        self._client: Optional[AIClient] = None

    def _get_client(self) -> AIClient:
        if self._client is None:
            self._client = AIClient(self.settings, provider=self._provider)
        return self._client

    def compare_batch(self, patent: PatentDocument,
                      results: list[dict]) -> list[dict]:
        """批量对比分析：直接用已有评分排序，对 Top-N 做详细对比。

        评分来源（上游已计算，不再重复调 AI）：
          - fulltext_score（Claims 广筛 / 阶段4）
          - 降级用 relevance_score
        """
        client = self._get_client()
        comparisons = []

        # 按已有评分排序（上游 Claims 广筛已算好，不重复调 AI）
        top_n = self.settings.analysis_top_n
        top_results = sorted(results,
            key=lambda x: x.get("fulltext_score", x.get("relevance_score", 0)),
            reverse=True)[:top_n]

        for r in top_results:
            detail = self._detailed_comparison(client, patent, r)
            if detail:
                comparisons.append(detail)

        return comparisons

    def _detailed_comparison(self, client: AIClient,
                              patent: PatentDocument,
                              result: dict) -> Optional[dict]:
        """阶段2: 对单个结果做详细对比（使用完整全文）"""

        # 候选专利：完整全文，不截断
        cand_claims = result.get("claims") or ""
        cand_desc = result.get("description") or ""
        cand_abstract = result.get("abstract") or result.get("abstract_snippet") or ""

        prompt = render_template(
            load_prompt(self.settings, "comparison", "user",
                        COMPARISON_FALLBACK_USER_PROMPT),
            application_title=patent.title,
            application_ipc=", ".join(patent.ipc_classifications),
            application_abstract=patent.abstract,
            application_claims="\n".join(patent.claims[:10]),
            application_description=(patent.description[:1500]
                                     if patent.description else "(无)"),
            comparison_publication_number=result.get("publication_number", "N/A"),
            comparison_title=result.get("title", "N/A"),
            comparison_applicant=result.get("applicant", "N/A"),
            comparison_publication_date=result.get("publication_date", "N/A"),
            comparison_ipc=result.get("ipc", "N/A"),
            comparison_abstract=cand_abstract,
            comparison_claims=cand_claims if cand_claims else "(无)",
            comparison_description=cand_desc if cand_desc else "(无)")

        try:
            content = client.chat(
                system_prompt=load_prompt(
                    self.settings, "comparison", "system",
                    COMPARISON_FALLBACK_SYSTEM_PROMPT),
                user_prompt=prompt,
                max_tokens=4096,
                temperature=0.3,
                model=self.settings.ai_analysis_model,
            )
            detail = self._parse_detail(content)
            if detail:
                detail["source_raw"] = result
                return detail
        except Exception as e:
            print(f"详细对比失败 ({result.get('publication_number', '')}): {e}")

        return None

    def _parse_detail(self, content: str) -> Optional[dict]:
        try:
            content = self._clean_json(content)
            return json.loads(content)
        except json.JSONDecodeError:
            return None

    def _clean_json(self, content: str) -> str:
        content = content.strip()
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()
        while content and content[0] not in "[{":
            content = content[1:]
        while content and content[-1] not in "]}":
            content = content[:-1]
        return content
