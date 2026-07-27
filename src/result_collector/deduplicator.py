"""
去重模块 - 跨检索式去重
"""
from typing import Sequence

from src.utils.config import Settings


class Deduplicator:
    """专利结果去重器"""

    def __init__(self, settings: Settings):
        self.settings = settings

    def deduplicate(self, all_results: Sequence[Sequence[dict]]) -> tuple[list[dict], int]:
        """
        对所有检索式结果去重
        返回: (去重后列表, 移除数量)
        """
        seen_numbers: set[str] = set()
        seen_titles: set[str] = set()
        unique_results: list[dict] = []
        removed = 0

        for query_idx, results in enumerate(all_results):
            for result in results:
                is_dup = False

                # 主键去重：公布号
                pn = result.get("publication_number", "").strip()
                if pn:
                    normalized_pn = self._normalize_pn(pn)
                    if normalized_pn in seen_numbers:
                        # 更新source_queries
                        self._add_source_to_existing(unique_results, pn, query_idx + 1)
                        removed += 1
                        is_dup = True
                    else:
                        seen_numbers.add(normalized_pn)

                # 辅助去重：标题相似度（当无公布号时）
                if not is_dup and not pn:
                    title = result.get("title", "").strip()
                    if title:
                        normalized_title = title[:100]  # 截断长度
                        if self._is_title_duplicate(normalized_title, seen_titles):
                            removed += 1
                            is_dup = True
                        else:
                            seen_titles.add(normalized_title)

                if not is_dup:
                    # 标记来源检索式
                    result["source_queries"] = [query_idx + 1]
                    unique_results.append(result)

        return unique_results, removed

    def _normalize_pn(self, pn: str) -> str:
        """标准化公布号"""
        # 去除空格和特殊字符
        return pn.replace(" ", "").replace("-", "").replace("/", "").upper()

    def _is_title_duplicate(self, title: str, seen_titles: set[str]) -> bool:
        """检查标题是否重复（基于简单相似度）"""
        for seen in seen_titles:
            score = self._similarity(title, seen)
            if score >= self.settings.dedup_threshold:
                return True
        return False

    def _similarity(self, s1: str, s2: str) -> float:
        """计算两个字符串的相似度 (0-1)"""
        import difflib
        return difflib.SequenceMatcher(None, s1, s2).ratio()

    def _add_source_to_existing(self, results: list[dict], pn: str, query_idx: int):
        """为已存在的去重结果添加来源检索式标记"""
        normalized_new = self._normalize_pn(pn)
        for r in results:
            existing_pn = self._normalize_pn(r.get("publication_number", ""))
            if existing_pn == normalized_new:
                sources = r.setdefault("source_queries", [])
                if query_idx not in sources:
                    sources.append(query_idx)
                break
