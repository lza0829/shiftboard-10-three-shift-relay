from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", _ROOT / "data"))


def fixtures_dir() -> Path:
    return Path(os.environ.get("FIXTURES_DIR", _ROOT / "fixtures"))


def db_path() -> Path:
    return data_dir() / "app.db"


def connect() -> sqlite3.Connection:
    data_dir().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def write_tx() -> Iterator[sqlite3.Connection]:
    """段级独立写事务。

    BEGIN IMMEDIATE 让「全局同时仅一单进行中」「上段已完成」等检查与写入
    处于同一个串行化事务内，避免两个请求同时通过检查。
    事务只覆盖当前操作涉及的行：完成一段只提交本段过户与计划游标，
    不回滚、不触碰此前已提交的段。
    """
    conn = connect()
    conn.isolation_level = None
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict]:
    return [dict(r) for r in rows]
