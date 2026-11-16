from __future__ import annotations

import json
from pathlib import Path

from app.db import connect, fixtures_dir


SCHEMA = """
CREATE TABLE IF NOT EXISTS shifts (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  keeper TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '空闲'
);
CREATE TABLE IF NOT EXISTS batches (
  id INTEGER PRIMARY KEY,
  biz_key TEXT NOT NULL UNIQUE,
  title TEXT NOT NULL,
  owner_shift_id INTEGER REFERENCES shifts(id),
  status TEXT NOT NULL DEFAULT '在途',
  note TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS relay_plans (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  shift_ids_json TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '待启动',
  current_seq INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  finished_at TEXT
);
CREATE TABLE IF NOT EXISTS handovers (
  id INTEGER PRIMARY KEY,
  from_shift_id INTEGER NOT NULL REFERENCES shifts(id),
  to_shift_id INTEGER NOT NULL REFERENCES shifts(id),
  status TEXT NOT NULL,
  batch_count INTEGER NOT NULL DEFAULT 0,
  reason TEXT NOT NULL DEFAULT '',
  started_at TEXT NOT NULL,
  finished_at TEXT,
  closed_at TEXT,
  relay_plan_id INTEGER REFERENCES relay_plans(id),
  relay_seq INTEGER
);
CREATE TABLE IF NOT EXISTS handover_items (
  handover_id INTEGER NOT NULL REFERENCES handovers(id),
  batch_id INTEGER NOT NULL REFERENCES batches(id),
  PRIMARY KEY (handover_id, batch_id)
);
CREATE TABLE IF NOT EXISTS reconcile_reports (
  id INTEGER PRIMARY KEY,
  handover_id INTEGER REFERENCES handovers(id),
  before_count INTEGER NOT NULL,
  after_from_count INTEGER NOT NULL,
  after_to_count INTEGER NOT NULL,
  consistent INTEGER NOT NULL,
  detail_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
"""


# 段级独立事务是主隔离手段；以下钩子再兜底：已完成段禁止回滚/改挂接，
# 已完成段清单禁止增删，已完成段交出的批次只能经后继「进行中」段再过户。
TRIGGERS = """
DROP TRIGGER IF EXISTS trg_handover_completed_no_status_change;
CREATE TRIGGER trg_handover_completed_no_status_change
BEFORE UPDATE OF status ON handovers
WHEN OLD.status = '已完成' AND NEW.status <> '已完成'
BEGIN
  SELECT RAISE(ABORT, '已完成段禁止回滚状态');
END;

DROP TRIGGER IF EXISTS trg_handover_completed_no_rewire;
CREATE TRIGGER trg_handover_completed_no_rewire
BEFORE UPDATE OF from_shift_id, to_shift_id, relay_plan_id, relay_seq ON handovers
WHEN OLD.status = '已完成'
BEGIN
  SELECT RAISE(ABORT, '已完成段禁止改写双方或接力挂接');
END;

DROP TRIGGER IF EXISTS trg_handover_items_completed_no_delete;
CREATE TRIGGER trg_handover_items_completed_no_delete
BEFORE DELETE ON handover_items
WHEN (SELECT status FROM handovers WHERE id = OLD.handover_id) = '已完成'
BEGIN
  SELECT RAISE(ABORT, '已完成段清单禁止删除');
END;

DROP TRIGGER IF EXISTS trg_handover_items_completed_no_insert;
CREATE TRIGGER trg_handover_items_completed_no_insert
BEFORE INSERT ON handover_items
WHEN (SELECT status FROM handovers WHERE id = NEW.handover_id) = '已完成'
BEGIN
  SELECT RAISE(ABORT, '已完成段清单禁止追加');
END;

DROP TRIGGER IF EXISTS trg_batch_transfer_requires_active_segment;
CREATE TRIGGER trg_batch_transfer_requires_active_segment
BEFORE UPDATE OF owner_shift_id ON batches
WHEN OLD.owner_shift_id <> NEW.owner_shift_id
 AND EXISTS (
   SELECT 1
   FROM handover_items hi
   JOIN handovers h ON h.id = hi.handover_id
   WHERE hi.batch_id = OLD.id
     AND h.status = '已完成'
     AND h.to_shift_id = OLD.owner_shift_id
 )
 AND NOT EXISTS (
   SELECT 1
   FROM handovers h2
   JOIN handover_items hi2 ON hi2.handover_id = h2.id
   WHERE h2.status = '进行中'
     AND h2.from_shift_id = OLD.owner_shift_id
     AND h2.to_shift_id = NEW.owner_shift_id
     AND hi2.batch_id = OLD.id
 )
BEGIN
  SELECT RAISE(ABORT, '已完成段资产只能经后继进行中段过户');
END;
"""


def _columns(conn, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _migrate(conn) -> None:
    cols = _columns(conn, "handovers")
    if "relay_plan_id" not in cols:
        conn.execute(
            "ALTER TABLE handovers ADD COLUMN relay_plan_id INTEGER REFERENCES relay_plans(id)"
        )
    if "relay_seq" not in cols:
        conn.execute("ALTER TABLE handovers ADD COLUMN relay_seq INTEGER")
    if "closed_at" not in cols:
        conn.execute("ALTER TABLE handovers ADD COLUMN closed_at TEXT")
    # 同一计划内段序号唯一；独立单段交班的两个字段均为 NULL，不参与唯一约束
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_handovers_relay_seq "
        "ON handovers(relay_plan_id, relay_seq)"
    )


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)
        conn.executescript(TRIGGERS)
        conn.commit()
        n = conn.execute("SELECT COUNT(*) AS c FROM shifts").fetchone()["c"]
        if n:
            return
        seed_path = fixtures_dir() / "seed.json"
        data = json.loads(Path(seed_path).read_text(encoding="utf-8"))
        for s in data["shifts"]:
            conn.execute(
                "INSERT INTO shifts(id, name, keeper, status) VALUES (?, ?, ?, ?)",
                (s["id"], s["name"], s["keeper"], s["status"]),
            )
        for b in data["batches"]:
            conn.execute(
                "INSERT INTO batches(id, biz_key, title, owner_shift_id, status, note) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    b["id"],
                    b["biz_key"],
                    b["title"],
                    b["owner_shift_id"],
                    b["status"],
                    b.get("note", ""),
                ),
            )
        conn.commit()
