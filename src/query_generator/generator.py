"""
AI检索式生成模块 - 调用AI API(DeepSeek/Kimi)生成PATENTSCOPE高级检索式
"""
import json
import time
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
        """生成检索式列表，带重试（deepseek 偶发返回 null/格式错误）。"""
        client = self._get_client()
        system_prompt = build_system_prompt(self.settings, patent)
        user_prompt = build_user_prompt(patent, max_queries, self.settings)

        last_err: Exception | None = None
        for attempt in range(3):
            try:
                content = client.chat(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_tokens=self.settings.query_max_tokens,
                    model=self.settings.ai_query_model,
                    json_mode=True,
                )
                # 解析JSON响应
                return self._parse_response(content, max_queries)
            except Exception as e:
                last_err = e
                time.sleep(2 * (attempt + 1))  # 退避：2s, 4s

        raise ValueError(f"检索式生成重试3次仍失败: {last_err}")

    def _parse_response(self, content: str, max_queries: int) -> list[dict]:
        """解析AI返回的JSON，失败时尝试修复截断"""
        import re

        content = content.strip()

        # 处理 ```json ... ``` 包裹
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        # 去掉开头的非JSON字符
        while content and content[0] not in "[{":
            content = content[1:]

        queries = None
        errors = []

        # 尝试1: 直接解析
        try:
            queries = json.loads(content)
        except json.JSONDecodeError as e1:
            errors.append(str(e1))
            # 尝试2: 修复尾部截断 → 找最后一个完整 } 的位置
            try:
                # 去掉尾部截断的不完整条目
                last_complete = content.rfind('"}')
                if last_complete > 0:
                    fixed = content[:last_complete + 2] + "\n]"
                    queries = json.loads(fixed)
            except json.JSONDecodeError as e2:
                errors.append(str(e2))
                # 尝试3: 修正常见JSON错误
                try:
                    content = re.sub(r",\s*\]", "]", content)
                    content = re.sub(r",\s*\}", "}", content)
                    queries = json.loads(content)
                except json.JSONDecodeError as e3:
                    errors.append(str(e3))
                    raise ValueError(
                        f"AI返回的JSON无法解析: {'; '.join(errors)}\n"
                        f"原始内容(前500字):\n{content[:500]}")

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

        if not validated:
            raise ValueError(f"JSON解析成功但无有效检索式: {content[:300]}")

        return validated[:max_queries + 3]  # +3 为宽泛兜底检索式预留
