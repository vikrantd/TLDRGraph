"""
Hash Gate Engine for CodeChakra: Zero-token incremental change detection using SHA-256 and SQLite.
"""

import os
import sqlite3
import hashlib
from typing import Dict, Any, Optional, Tuple, Set

class HashGate:
    #: Bumped whenever the node_cache layout changes. Migrations run on open.
    SCHEMA_VERSION = 2

    def __init__(self, db_path: str = ".codechakra/codechakra.db"):
        self.db_path = db_path
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS node_cache (
                    node_id TEXT PRIMARY KEY,
                    file_path TEXT,
                    content_hash TEXT,
                    layer TEXT,
                    summary TEXT,
                    fields_json TEXT,
                    intent TEXT DEFAULT '',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_file_path ON node_cache(file_path)")
            conn.commit()
        self._migrate()

    def _columns(self, conn: sqlite3.Connection) -> Set[str]:
        return {row[1] for row in conn.execute("PRAGMA table_info(node_cache)")}

    def _migrate(self):
        """
        Self-healing migration driven by PRAGMA user_version.

        v1 -> v2: adds the `intent` column and purges rows that only ever held the
        un-enriched placeholder summary (those rows made nodes look permanently
        "clean" so they could never be re-enriched).
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            current_version = cursor.execute("PRAGMA user_version").fetchone()[0]
            if current_version >= self.SCHEMA_VERSION:
                return
            if "intent" not in self._columns(conn):
                cursor.execute("ALTER TABLE node_cache ADD COLUMN intent TEXT DEFAULT ''")
            conn.commit()

        self.purge_placeholders()

        with sqlite3.connect(self.db_path) as conn:
            conn.execute(f"PRAGMA user_version = {int(self.SCHEMA_VERSION)}")
            conn.commit()

    def purge_placeholders(self) -> int:
        """
        Deletes cache rows whose summary is the generated
        "<Layer>: <label> located at <path>" placeholder and which carry no real
        enrichment. Returns the number of rows deleted.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            conditions = [
                "summary LIKE '% located at %'",
                "(fields_json IS NULL OR fields_json IN ('', '[]'))",
            ]
            if "intent" in self._columns(conn):
                conditions.append("(intent IS NULL OR intent = '')")
            cursor.execute("DELETE FROM node_cache WHERE " + " AND ".join(conditions))
            deleted = cursor.rowcount or 0
            conn.commit()
        return deleted

    @staticmethod
    def compute_hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def check_node(self, node_id: str, current_content: str) -> Tuple[bool, Optional[Dict[str, Any]]]:
        """
        Returns (is_dirty, cached_data).
        If content hash matches, is_dirty=False and cached_data is returned.
        """
        current_hash = self.compute_hash(current_content)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT content_hash, layer, summary, fields_json, intent FROM node_cache WHERE node_id = ?",
                (node_id,)
            )
            row = cursor.fetchone()
            if row:
                cached_hash, layer, summary, fields_json, intent = row
                if cached_hash == current_hash:
                    return False, {
                        "layer": layer,
                        "summary": summary,
                        "fields_json": fields_json,
                        "intent": intent or ""
                    }
        return True, None

    def update_node(self, node_id: str, file_path: str, content: str, layer: str,
                    summary: str = "", fields_json: str = "[]", intent: str = ""):
        content_hash = self.compute_hash(content)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO node_cache (node_id, file_path, content_hash, layer, summary, fields_json, intent, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(node_id) DO UPDATE SET
                    file_path=excluded.file_path,
                    content_hash=excluded.content_hash,
                    layer=excluded.layer,
                    summary=excluded.summary,
                    fields_json=excluded.fields_json,
                    intent=excluded.intent,
                    updated_at=CURRENT_TIMESTAMP
            """, (node_id, file_path, content_hash, layer, summary, fields_json, intent))
            conn.commit()
