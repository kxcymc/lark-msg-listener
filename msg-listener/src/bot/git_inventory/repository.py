"""SQLite persistence for local git inventory."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime

from ...common.index import IndexDB
from .scanner import GitRemote, RepoInfo


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class RepoInventoryRepository:
    def __init__(self, index_db: IndexDB) -> None:
        self.index_db = index_db

    def replace_all(self, repos: list[RepoInfo]) -> None:
        stamp = now_iso()
        with closing(self.index_db.connect()) as conn:
            conn.execute("DELETE FROM repo_inventory")
            conn.executemany(
                """
                INSERT INTO repo_inventory(
                  repo_id, label, local_path, remotes_json, branches_json, scanned_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        repo.repo_id,
                        repo.label,
                        repo.local_path,
                        json.dumps([r.__dict__ for r in repo.remotes], ensure_ascii=False),
                        json.dumps(repo.branches, ensure_ascii=False),
                        stamp,
                    )
                    for repo in repos
                ],
            )
            conn.commit()

    def list_all(self) -> list[RepoInfo]:
        with closing(self.index_db.connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT repo_id, label, local_path, remotes_json, branches_json
                FROM repo_inventory
                ORDER BY label ASC, repo_id ASC
                """
            ).fetchall()
        repos: list[RepoInfo] = []
        for row in rows:
            remotes_raw = _loads(row["remotes_json"])
            branches_raw = _loads(row["branches_json"])
            repos.append(
                RepoInfo(
                    repo_id=str(row["repo_id"]),
                    label=str(row["label"]),
                    local_path=str(row["local_path"]),
                    remotes=[
                        GitRemote(
                            name=str(item.get("name") or ""),
                            url=str(item.get("url") or ""),
                        )
                        for item in remotes_raw
                        if isinstance(item, dict)
                    ],
                    branches=[str(item) for item in branches_raw if item],
                )
            )
        return repos

    def get_by_repo_id(self, repo_id: str) -> RepoInfo | None:
        with closing(self.index_db.connect()) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                """
                SELECT repo_id, label, local_path, remotes_json, branches_json
                FROM repo_inventory
                WHERE repo_id = ?
                """,
                (repo_id,),
            ).fetchone()
        if row is None:
            return None
        remotes_raw = _loads(row["remotes_json"])
        branches_raw = _loads(row["branches_json"])
        return RepoInfo(
            repo_id=str(row["repo_id"]),
            label=str(row["label"]),
            local_path=str(row["local_path"]),
            remotes=[
                GitRemote(
                    name=str(item.get("name") or ""),
                    url=str(item.get("url") or ""),
                )
                for item in remotes_raw
                if isinstance(item, dict)
            ],
            branches=[str(item) for item in branches_raw if item],
        )

    def latest_scanned_at(self) -> str:
        with closing(self.index_db.connect()) as conn:
            row = conn.execute("SELECT MAX(scanned_at) FROM repo_inventory").fetchone()
        return str(row[0] or "") if row else ""


def _loads(text: str) -> list:
    try:
        data = json.loads(text or "[]")
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []
