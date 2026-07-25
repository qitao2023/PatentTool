"""
SQLite 数据库 - 检索会话、结果和分析的持久化存储
"""
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional


class PatentDatabase:
    """专利检索结果数据库"""

    def __init__(self, db_path: Optional[Path] = None):
        if db_path is None:
            db_path = Path(__file__).parent.parent.parent / "data" / "db" / "patent_tool.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init_schema()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_schema(self):
        conn = self.get_connection()
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS search_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    patent_filename TEXT NOT NULL,
                    patent_title TEXT,
                    patent_number TEXT,
                    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    completed_at TIMESTAMP,
                    total_queries INTEGER,
                    total_raw_results INTEGER DEFAULT 0,
                    total_deduped_results INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'running'
                );

                CREATE TABLE IF NOT EXISTS search_queries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER REFERENCES search_sessions(id),
                    query_string TEXT NOT NULL,
                    search_angle TEXT,
                    priority INTEGER,
                    executed_at TIMESTAMP,
                    raw_result_count INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'pending'
                );

                CREATE TABLE IF NOT EXISTS patent_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER REFERENCES search_sessions(id),
                    publication_number TEXT NOT NULL,
                    title TEXT,
                    abstract TEXT,
                    applicant TEXT,
                    inventor TEXT,
                    publication_date TEXT,
                    ipc_classification TEXT,
                    application_number TEXT,
                    source_queries TEXT,
                    is_duplicate INTEGER DEFAULT 0,
                    relevance_score REAL,
                    novelty_assessment TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(publication_number, session_id)
                );

                CREATE TABLE IF NOT EXISTS query_results (
                    query_id INTEGER REFERENCES search_queries(id),
                    result_id INTEGER REFERENCES patent_results(id),
                    PRIMARY KEY (query_id, result_id)
                );
            """)
            conn.commit()
        finally:
            conn.close()

    def create_session(self, patent_filename: str, patent_title: str = "",
                       patent_number: str = "", total_queries: int = 0) -> int:
        conn = self.get_connection()
        try:
            cur = conn.execute(
                """INSERT INTO search_sessions
                   (patent_filename, patent_title, patent_number, total_queries, started_at)
                   VALUES (?, ?, ?, ?, datetime('now', 'localtime'))""",
                (patent_filename, patent_title, patent_number, total_queries)
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def update_session(self, session_id: int, **kwargs):
        fields = []
        values = []
        for k, v in kwargs.items():
            fields.append(f"{k} = ?")
            values.append(v)
        if fields:
            values.append(session_id)
            conn = self.get_connection()
            try:
                conn.execute(
                    f"UPDATE search_sessions SET {', '.join(fields)} WHERE id = ?",
                    values
                )
                conn.commit()
            finally:
                conn.close()

    def save_queries(self, session_id: int, queries: list[dict]):
        conn = self.get_connection()
        try:
            for q in queries:
                conn.execute(
                    """INSERT INTO search_queries
                       (session_id, query_string, search_angle, priority, status)
                       VALUES (?, ?, ?, ?, 'pending')""",
                    (session_id, q.get("query_string", ""),
                     q.get("search_angle", ""), q.get("priority", 0))
                )
            conn.commit()
        finally:
            conn.close()

    def save_result(self, session_id: int, result: dict,
                    query_id: int) -> int:
        conn = self.get_connection()
        try:
            # 插入或忽略（按公开号去重）
            cur = conn.execute(
                """INSERT OR IGNORE INTO patent_results
                   (session_id, publication_number, title, abstract, applicant,
                    inventor, publication_date, ipc_classification,
                    application_number, source_queries, is_duplicate)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (session_id,
                 result.get("publication_number", ""),
                 result.get("title", ""),
                 result.get("abstract", ""),
                 result.get("applicant", ""),
                 result.get("inventor", ""),
                 result.get("publication_date", ""),
                 result.get("ipc", ""),
                 result.get("application_number", ""),
                 json.dumps([query_id], ensure_ascii=False))
            )
            result_id = cur.lastrowid
            if result_id == 0:
                # 已存在，获取ID并更新source_queries
                row = conn.execute(
                    "SELECT id, source_queries FROM patent_results WHERE publication_number = ? AND session_id = ?",
                    (result.get("publication_number", ""), session_id)
                ).fetchone()
                if row:
                    result_id = row["id"]
                    existing = json.loads(row["source_queries"] or "[]")
                    if query_id not in existing:
                        existing.append(query_id)
                        conn.execute(
                            "UPDATE patent_results SET source_queries = ? WHERE id = ?",
                            (json.dumps(existing, ensure_ascii=False), result_id)
                        )

            # 关联查询和结果
            if result_id:
                conn.execute(
                    "INSERT OR IGNORE INTO query_results (query_id, result_id) VALUES (?, ?)",
                    (query_id, result_id)
                )
            conn.commit()
            return result_id
        finally:
            conn.close()

    def get_session_results(self, session_id: int) -> list[sqlite3.Row]:
        conn = self.get_connection()
        try:
            cur = conn.execute(
                "SELECT * FROM patent_results WHERE session_id = ? AND is_duplicate = 0 ORDER BY relevance_score DESC",
                (session_id,)
            )
            return cur.fetchall()
        finally:
            conn.close()
