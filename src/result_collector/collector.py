"""
结果收集器 - 管理检索结果的提取、去重和存储
"""
from pathlib import Path
from typing import Optional

from src.utils.config import Settings
from src.result_collector.database import PatentDatabase
from src.result_collector.deduplicator import Deduplicator


class ResultCollector:
    """专利结果收集和存储管理器"""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.db = PatentDatabase()
        self.deduplicator = Deduplicator(settings)
        self.current_session_id: Optional[int] = None

    def start_session(self, patent_filename: str, patent_title: str = "",
                      patent_number: str = "", total_queries: int = 0) -> int:
        """创建新的检索会话"""
        self.current_session_id = self.db.create_session(
            patent_filename=patent_filename,
            patent_title=patent_title,
            patent_number=patent_number,
            total_queries=total_queries,
        )
        return self.current_session_id

    def save_generated_queries(self, queries: list[dict]):
        """保存生成的检索式"""
        if self.current_session_id:
            self.db.save_queries(self.current_session_id, queries)

    def save_search_results(self, query_id: int, results: list[dict]):
        """保存单个检索式的结果"""
        if not self.current_session_id:
            return
        for result in results:
            self.db.save_result(self.current_session_id, result, query_id)

    def process_all(self, all_results: list[list[dict]],
                    queries: list[dict]) -> tuple[list[dict], int]:
        """处理所有结果：保存到数据库+去重"""
        # 保存查询
        if self.current_session_id:
            self.db.save_queries(self.current_session_id, queries)

        # 保存每个检索式的结果
        for q_idx, results in enumerate(all_results):
            query_id = q_idx + 1
            self.save_search_results(query_id, results)

        # 去重
        deduped, removed = self.deduplicator.deduplicate(all_results)

        # 更新会话统计
        if self.current_session_id:
            total_raw = sum(len(r) for r in all_results)
            self.db.update_session(
                self.current_session_id,
                total_raw_results=total_raw,
                total_deduped_results=len(deduped),
                completed_at="datetime('now', 'localtime')",
                status="completed",
            )

        return deduped, removed
