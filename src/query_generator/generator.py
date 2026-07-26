"""
AI检索式生成模块 - 调用AI API(DeepSeek/Kimi)生成PATENTSCOPE高级检索式
"""
import json
from typing import Optional

from src.utils.config import Settings
from src.pdf_extractor.extractor import PatentDocument
from src.query_generator.prompts import build_system_prompt, build_user_prompt
from src.ai_client import AIClient


class QueryGenerator:
    """使用DeepSeek/Kimi生成PATENTSCOPE高级检索式"""

    def __init__(self, settings: Settings, provider: str | None = None):
        self.settings = settings
        self._provider = provider
        self._client: Optional[AIClient] = None

    def _get_client(self) -> AIClient:
        if self._client is None:
            self._client = AIClient(self.settings, provider=self._provider)
        return self._client

    def generate(self, patent: PatentDocument,
                 max_queries: int = 10) -> list[dict]:
        """生成检索式列表"""
        client = self._get_client()

        system_prompt = build_system_prompt()
        user_prompt = build_user_prompt(patent, max_queries)

        content = client.chat(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_tokens=self.settings.query_max_tokens,
        )

        # 解析JSON响应
        queries = self._parse_response(content, max_queries)
        return queries

    def _parse_response(self, content: str, max_queries: int) -> list[dict]:
        """解析AI返回的JSON"""
        content = content.strip()

        # 处理 ```json ... ``` 包裹
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        # 去掉开头的非JSON字符
        while content and content[0] not in "[{":
            content = content[1:]
        # 去掉结尾的非JSON字符
        while content and content[-1] not in "]}":
            content = content[:-1]

        try:
            queries = json.loads(content)
        except json.JSONDecodeError as e:
            # 尝试修正常见JSON错误
            try:
                import re
                content = re.sub(r",\s*\]", "]", content)
                content = re.sub(r",\s*\}", "}", content)
                queries = json.loads(content)
            except json.JSONDecodeError:
                raise ValueError(f"AI返回的JSON无法解析: {e}\n原始内容:\n{content[:500]}")

        if isinstance(queries, dict) and "queries" in queries:
            queries = queries["queries"]

        if not isinstance(queries, list):
            raise ValueError(f"返回格式错误，期望数组，得到: {type(queries)}")

        # 验证和清理
        validated = []
        for i, q in enumerate(queries):
            if not isinstance(q, dict):
                continue
            q.setdefault("query_string", "")
            q.setdefault("search_angle", f"检索式{i+1}")
            q.setdefault("rationale", "")
            q.setdefault("priority", i + 1)
            if q["query_string"]:
                validated.append(q)

        return validated[:max_queries]
