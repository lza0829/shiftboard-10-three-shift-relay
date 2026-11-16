from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import HTTPException

from app.db import connect, rows_to_dicts, write_tx


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _active_handover(conn):
    return conn.execute(
        "SELECT id, relay_plan_id, relay_seq FROM handovers WHERE status = '进行中' LIMIT 1"
    ).fetchone()


def insert_handover_row(
    conn,
    from_shift_id: int,
    to_shift_id: int,
    batch_ids: list[int],
    started_at: str,
    relay_plan_id: int | None = None,
    relay_seq: int | None = None,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO handovers(
          from_shift_id, to_shift_id, status, batch_count, started_at,
          relay_plan_id, relay_seq
        ) VALUES (?, ?, '进行中', ?, ?, ?, ?)
        """,
        (from_shift_id, to_shift_id, len(batch_ids), started_at, relay_plan_id, relay_seq),
    )
    hid = int(cur.lastrowid)
    for bid in batch_ids:
        conn.execute(
            "INSERT INTO handover_items(handover_id, batch_id) VALUES (?, ?)",
            (hid, bid),
        )
    conn.execute("UPDATE shifts SET status = '交班中' WHERE id = ?", (from_shift_id,))
    conn.execute("UPDATE shifts SET status = '接班中' WHERE id = ?", (to_shift_id,))
    return hid


def list_shifts() -> list[dict]:
    with connect() as conn:
        return rows_to_dicts(conn.execute("SELECT * FROM shifts ORDER BY id").fetchall())


def list_batches(owner_shift_id: int | None = None) -> list[dict]:
    with connect() as conn:
        if owner_shift_id is None:
            rows = conn.execute(
                """
                SELECT b.*, s.name AS owner_shift_name
                FROM batches b
                LEFT JOIN shifts s ON s.id = b.owner_shift_id
                ORDER BY b.id
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT b.*, s.name AS owner_shift_name
                FROM batches b
                LEFT JOIN shifts s ON s.id = b.owner_shift_id
                WHERE b.owner_shift_id = ?
                ORDER BY b.id
                """,
                (owner_shift_id,),
            ).fetchall()
        return rows_to_dicts(rows)


def list_handovers(relay_plan_id: int | None = None) -> list[dict]:
    with connect() as conn:
        sql = """
            SELECT h.*,
                   fs.name AS from_shift_name,
                   ts.name AS to_shift_name,
                   rp.name AS relay_plan_name
            FROM handovers h
            JOIN shifts fs ON fs.id = h.from_shift_id
            JOIN shifts ts ON ts.id = h.to_shift_id
            LEFT JOIN relay_plans rp ON rp.id = h.relay_plan_id
        """
        params: tuple = ()
        if relay_plan_id is not None:
            sql += " WHERE h.relay_plan_id = ?"
            params = (relay_plan_id,)
        sql += " ORDER BY h.id DESC"
        rows = conn.execute(sql, params).fetchall()
        return rows_to_dicts(rows)


def get_handover(handover_id: int) -> dict:
    with connect() as conn:
        h = conn.execute(
            """
            SELECT h.*,
                   fs.name AS from_shift_name,
                   ts.name AS to_shift_name,
                   rp.name AS relay_plan_name
            FROM handovers h
            JOIN shifts fs ON fs.id = h.from_shift_id
            JOIN shifts ts ON ts.id = h.to_shift_id
            LEFT JOIN relay_plans rp ON rp.id = h.relay_plan_id
            WHERE h.id = ?
            """,
            (handover_id,),
        ).fetchone()
        if not h:
            raise HTTPException(404, "交班单不存在")
        items = conn.execute(
            """
            SELECT b.*
            FROM handover_items hi
            JOIN batches b ON b.id = hi.batch_id
            WHERE hi.handover_id = ?
            ORDER BY b.id
            """,
            (handover_id,),
        ).fetchall()
        out = dict(h)
        out["items"] = rows_to_dicts(items)
        out["清单数"] = len(out["items"])
        out["可完成"] = out["status"] == "进行中"
        out["可关闭"] = out["status"] == "失败" and not out.get("closed_at")
        return out


def dashboard() -> dict:
    with connect() as conn:
        shifts = rows_to_dicts(conn.execute("SELECT * FROM shifts ORDER BY id").fetchall())
        inflight = conn.execute(
            "SELECT COUNT(*) AS c FROM batches WHERE status = '在途'"
        ).fetchone()["c"]
        active = conn.execute(
            "SELECT COUNT(*) AS c FROM handovers WHERE status = '进行中'"
        ).fetchone()["c"]
        failed_open = conn.execute(
            "SELECT COUNT(*) AS c FROM handovers WHERE status = '失败'"
        ).fetchone()["c"]
        last = conn.execute(
            "SELECT * FROM handovers ORDER BY id DESC LIMIT 1"
        ).fetchone()
        relay_total = conn.execute("SELECT COUNT(*) AS c FROM relay_plans").fetchone()["c"]
        relay_live = conn.execute(
            "SELECT COUNT(*) AS c FROM relay_plans WHERE status IN ('待启动', '进行中')"
        ).fetchone()["c"]
        return {
            "班次数": len(shifts),
            "在途批次数": inflight,
            "进行中交班": active,
            "失败待关闭段": failed_open,
            "接力计划数": relay_total,
            "进行中接力": relay_live,
            "最近交班": dict(last) if last else None,
            "班次": shifts,
        }


def start_handover(from_shift_id: int, to_shift_id: int) -> dict:
    if from_shift_id == to_shift_id:
        raise HTTPException(400, "交班双方不能是同一班次")
    with write_tx() as conn:
        fs = conn.execute("SELECT * FROM shifts WHERE id = ?", (from_shift_id,)).fetchone()
        ts = conn.execute("SELECT * FROM shifts WHERE id = ?", (to_shift_id,)).fetchone()
        if not fs or not ts:
            raise HTTPException(404, "班次不存在")
        active = _active_handover(conn)
        if active:
            raise HTTPException(409, f"已有进行中的交班单#{active['id']}，禁止并行交班")
        unclosed_same = conn.execute(
            """
            SELECT id FROM handovers
            WHERE status = '失败' AND from_shift_id = ? AND to_shift_id = ?
            LIMIT 1
            """,
            (from_shift_id, to_shift_id),
        ).fetchone()
        if unclosed_same:
            raise HTTPException(
                409, f"同一交接存在失败未关闭的交班单#{unclosed_same['id']}，请先关闭"
            )
        batches = conn.execute(
            "SELECT id FROM batches WHERE owner_shift_id = ? AND status = '在途' ORDER BY id",
            (from_shift_id,),
        ).fetchall()
        if not batches:
            raise HTTPException(400, "交班方没有在途批次可交接")
        hid = insert_handover_row(
            conn, from_shift_id, to_shift_id, [b["id"] for b in batches], now_iso()
        )
    return get_handover(hid)


class _SegmentReconcileFailed(Exception):
    """本段过户后对账不一致：由外层回滚本段，另起事务落失败记录。"""

    def __init__(self, before: int, after_from: int, after_to: int, reason: str):
        self.before = before
        self.after_from = after_from
        self.after_to = after_to
        self.reason = reason


def _finish_relay_segment(conn, handover, seq: int, total: int) -> None:
    if seq == total:
        conn.execute(
            "UPDATE relay_plans SET status = '已完成', current_seq = ?, finished_at = ? WHERE id = ?",
            (seq, now_iso(), handover["relay_plan_id"]),
        )
    else:
        conn.execute(
            "UPDATE relay_plans SET status = '进行中', current_seq = ? WHERE id = ?",
            (seq, handover["relay_plan_id"]),
        )


def _check_relay_order(conn, handover) -> tuple[int, int]:
    """完成前再验一次接力顺序，返回（当前段序号, 总段数）。"""
    plan = conn.execute(
        "SELECT * FROM relay_plans WHERE id = ?", (handover["relay_plan_id"],)
    ).fetchone()
    if not plan:
        raise HTTPException(409, "接力计划不存在，禁止完成该段")
    if plan["status"] != "进行中":
        raise HTTPException(409, f"接力计划当前为「{plan['status']}」，禁止完成该段")
    shift_ids = json.loads(plan["shift_ids_json"])
    total = len(shift_ids) - 1
    seq = int(handover["relay_seq"])
    if seq < 1 or seq > total:
        raise HTTPException(409, "段序号超出接力计划范围")
    earlier = conn.execute(
        """
        SELECT relay_seq, status FROM handovers
        WHERE relay_plan_id = ? AND relay_seq < ?
        ORDER BY relay_seq
        """,
        (handover["relay_plan_id"], seq),
    ).fetchall()
    if len(earlier) != seq - 1 or any(r["status"] != "已完成" for r in earlier):
        raise HTTPException(409, "前序段尚未全部完成，禁止完成本段")
    return seq, total


def complete_handover(handover_id: int) -> dict:
    failed: _SegmentReconcileFailed | None = None
    try:
        with write_tx() as conn:
            h = conn.execute(
                "SELECT * FROM handovers WHERE id = ?", (handover_id,)
            ).fetchone()
            if not h:
                raise HTTPException(404, "交班单不存在")
            if h["status"] != "进行中":
                raise HTTPException(400, "只能完成进行中的交班单")

            seq = total = None
            if h["relay_plan_id"] is not None:
                seq, total = _check_relay_order(conn, h)

            items = conn.execute(
                "SELECT batch_id FROM handover_items WHERE handover_id = ?",
                (handover_id,),
            ).fetchall()
            before = len(items)
            # 段级独立事务：本段过户与对账同进同退，绝不触碰已提交的前段
            for it in items:
                conn.execute(
                    "UPDATE batches SET owner_shift_id = ? WHERE id = ? AND status = '在途'",
                    (h["to_shift_id"], it["batch_id"]),
                )
            after_from = conn.execute(
                "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = '在途'",
                (h["from_shift_id"],),
            ).fetchone()["c"]
            after_to = conn.execute(
                "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = '在途'",
                (h["to_shift_id"],),
            ).fetchone()["c"]
            consistent = 1 if after_from == 0 and after_to >= before else 0
            detail = {
                "交班前在途数": before,
                "交班后交班方在途": after_from,
                "交班后接班方在途": after_to,
            }
            if not consistent:
                reason = (
                    f"交班后对账不一致：交班方仍有 {after_from} 批在途，"
                    f"接班方接住 {after_to}/{before} 批；本段已回滚，前段已完成过户保持不变"
                )
                raise _SegmentReconcileFailed(before, after_from, after_to, reason)

            conn.execute(
                """
                INSERT INTO reconcile_reports(
                  handover_id, before_count, after_from_count, after_to_count,
                  consistent, detail_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    handover_id,
                    before,
                    after_from,
                    after_to,
                    1,
                    json.dumps(detail, ensure_ascii=False),
                    now_iso(),
                ),
            )
            conn.execute(
                """
                UPDATE handovers
                SET status = '已完成', finished_at = ?, batch_count = ?, reason = ''
                WHERE id = ?
                """,
                (now_iso(), before, handover_id),
            )
            conn.execute(
                "UPDATE shifts SET status = '已交班' WHERE id = ?", (h["from_shift_id"],)
            )
            conn.execute(
                "UPDATE shifts SET status = '值班中' WHERE id = ?", (h["to_shift_id"],)
            )
            if h["relay_plan_id"] is not None:
                _finish_relay_segment(conn, h, int(seq), int(total))
    except _SegmentReconcileFailed as exc:
        failed = exc

    if failed is None:
        return get_handover(handover_id)

    # 本段已回滚；另起事务只关闭本段、解冻本段双方，并中止接力计划
    with write_tx() as conn:
        h = conn.execute("SELECT * FROM handovers WHERE id = ?", (handover_id,)).fetchone()
        if not h:
            raise HTTPException(404, "交班单不存在")
        if h["status"] != "进行中":
            # 状态已被其他请求推进，失败记录不重复落
            return get_handover(handover_id)
        detail = {
            "交班前在途数": failed.before,
            "交班后交班方在途": failed.after_from,
            "交班后接班方在途": failed.after_to,
            "已回滚本段": True,
            "失败来源": "过户后对账不一致",
        }
        conn.execute(
            """
            INSERT INTO reconcile_reports(
              handover_id, before_count, after_from_count, after_to_count,
              consistent, detail_json, created_at
            ) VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (
                handover_id,
                failed.before,
                failed.after_from,
                failed.after_to,
                json.dumps(detail, ensure_ascii=False),
                now_iso(),
            ),
        )
        conn.execute(
            "UPDATE handovers SET status = '失败', reason = ?, finished_at = ? WHERE id = ?",
            (failed.reason, now_iso(), handover_id),
        )
        # 只解冻本段双方；上一段接班方已固化为新的交班方资产，不受影响
        conn.execute("UPDATE shifts SET status = '值班中' WHERE id = ?", (h["from_shift_id"],))
        conn.execute("UPDATE shifts SET status = '空闲' WHERE id = ?", (h["to_shift_id"],))
        if h["relay_plan_id"] is not None:
            seg = h["relay_seq"]
            conn.execute(
                "UPDATE relay_plans SET status = '段失败', reason = ? WHERE id = ?",
                (f"段{seg}失败：{failed.reason}", h["relay_plan_id"]),
            )
    return get_handover(handover_id)


def close_failed_handover(handover_id: int) -> dict:
    with write_tx() as conn:
        h = conn.execute("SELECT * FROM handovers WHERE id = ?", (handover_id,)).fetchone()
        if not h:
            raise HTTPException(404, "交班单不存在")
        if h["status"] != "失败":
            raise HTTPException(400, "只有失败的段可以关闭")
        if h["closed_at"]:
            raise HTTPException(400, "该失败段已关闭")
        conn.execute(
            "UPDATE handovers SET status = '失败已关闭', closed_at = ? WHERE id = ?",
            (now_iso(), handover_id),
        )
    return get_handover(handover_id)


def fail_handover(handover_id: int, reason: str) -> dict:
    """人工判段失败：只关闭本段并解冻本段双方。

    本段尚未过户，无需回滚批次归属；前序已完成段已在各自事务中提交，
    这里不触碰它们的单据、清单与批次归属。
    """
    with write_tx() as conn:
        h = conn.execute("SELECT * FROM handovers WHERE id = ?", (handover_id,)).fetchone()
        if not h:
            raise HTTPException(404, "交班单不存在")
        if h["status"] != "进行中":
            raise HTTPException(400, "只能判进行中的交班单失败")
        items = conn.execute(
            "SELECT COUNT(*) AS c FROM handover_items WHERE handover_id = ?",
            (handover_id,),
        ).fetchone()["c"]
        detail = {
            "交班前在途数": items,
            "交班后交班方在途": conn.execute(
                "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = '在途'",
                (h["from_shift_id"],),
            ).fetchone()["c"],
            "交班后接班方在途": conn.execute(
                "SELECT COUNT(*) AS c FROM batches WHERE owner_shift_id = ? AND status = '在途'",
                (h["to_shift_id"],),
            ).fetchone()["c"],
            "本段未过户": True,
            "失败来源": "人工判定",
        }
        conn.execute(
            """
            INSERT INTO reconcile_reports(
              handover_id, before_count, after_from_count, after_to_count,
              consistent, detail_json, created_at
            ) VALUES (?, ?, ?, ?, 0, ?, ?)
            """,
            (
                handover_id,
                detail["交班前在途数"],
                detail["交班后交班方在途"],
                detail["交班后接班方在途"],
                json.dumps(detail, ensure_ascii=False),
                now_iso(),
            ),
        )
        conn.execute(
            "UPDATE handovers SET status = '失败', reason = ?, finished_at = ? WHERE id = ?",
            (f"人工判定失败：{reason}", now_iso(), handover_id),
        )
        conn.execute("UPDATE shifts SET status = '值班中' WHERE id = ?", (h["from_shift_id"],))
        conn.execute("UPDATE shifts SET status = '空闲' WHERE id = ?", (h["to_shift_id"],))
        if h["relay_plan_id"] is not None:
            conn.execute(
                "UPDATE relay_plans SET status = '段失败', reason = ? WHERE id = ?",
                (f"段{h['relay_seq']}失败：人工判定 {reason}", h["relay_plan_id"]),
            )
    return get_handover(handover_id)



def list_reconcile() -> list[dict]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT r.*,
                   h.relay_plan_id,
                   h.relay_seq,
                   rp.name AS relay_plan_name
            FROM reconcile_reports r
            LEFT JOIN handovers h ON h.id = r.handover_id
            LEFT JOIN relay_plans rp ON rp.id = h.relay_plan_id
            ORDER BY r.id DESC
            """
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["detail"] = json.loads(d.pop("detail_json"))
            d["一致"] = bool(d.pop("consistent"))
            out.append(d)
        return out
