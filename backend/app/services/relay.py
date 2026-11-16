from __future__ import annotations

import json

from fastapi import HTTPException

from app.db import connect, rows_to_dicts, write_tx
from app.services.handover import (
    _active_handover,
    get_handover,
    insert_handover_row,
    now_iso,
)

# 可继续推进的状态；任一段失败后计划停在「段失败」：失败段未关闭时禁止发起下一段，
# 且因上一段未完成，本计划不能再推进（已完成的段保持原样，另登记计划继续）
PLAN_OPEN_STATUSES = ("待启动", "进行中")
PLAN_FAILED_STATUS = "段失败"
FAILED_SEGMENT_STATUSES = ("失败", "失败已关闭")


def _plan_or_404(conn, plan_id: int):
    plan = conn.execute("SELECT * FROM relay_plans WHERE id = ?", (plan_id,)).fetchone()
    if not plan:
        raise HTTPException(404, "接力计划不存在")
    return plan


def _shift_names(conn, shift_ids: list[int]) -> list[str]:
    marks = ",".join("?" for _ in shift_ids)
    rows = conn.execute(
        f"SELECT id, name FROM shifts WHERE id IN ({marks})", shift_ids
    ).fetchall()
    names = {r["id"]: r["name"] for r in rows}
    missing = [sid for sid in shift_ids if sid not in names]
    if missing:
        raise HTTPException(404, f"班次不存在：{missing}")
    return [names[sid] for sid in shift_ids]


# 总览不做旁路计数器：每段交班单、清单数、最新对账报告全部查询时同源派生
_SEGMENTS_SQL = """
    SELECT h.id, h.relay_plan_id, h.relay_seq, h.from_shift_id, h.to_shift_id,
           h.status, h.batch_count, h.reason, h.started_at, h.finished_at, h.closed_at,
           fs.name AS from_shift_name, ts.name AS to_shift_name,
           (SELECT COUNT(*) FROM handover_items hi WHERE hi.handover_id = h.id) AS item_count,
           r.id AS report_id,
           r.consistent AS report_consistent
    FROM handovers h
    JOIN shifts fs ON fs.id = h.from_shift_id
    JOIN shifts ts ON ts.id = h.to_shift_id
    LEFT JOIN reconcile_reports r ON r.id = (
        SELECT MAX(rr.id) FROM reconcile_reports rr WHERE rr.handover_id = h.id
    )
    WHERE h.relay_plan_id = ?
    ORDER BY h.relay_seq
"""


def _shape_segment(row) -> dict:
    d = dict(row)
    d["清单数"] = int(d.pop("item_count") or 0)
    report_id = d.pop("report_id")
    consistent = d.pop("report_consistent")
    d["对账结果"] = "未对账" if report_id is None else ("一致" if consistent else "不一致")
    d["段"] = d.pop("relay_seq")
    d["交班单"] = d.pop("id")
    d["失败原因"] = d["reason"] if d["status"] in FAILED_SEGMENT_STATUSES else ""
    d["可完成"] = d["status"] == "进行中"
    d["可关闭"] = d["status"] == "失败"
    return d


def _relay_segments(conn, plan_id: int) -> list[dict]:
    return [_shape_segment(r) for r in conn.execute(_SEGMENTS_SQL, (plan_id,)).fetchall()]


def _same_route_unclosed_failed(conn, from_shift_id: int, to_shift_id: int):
    """同一交接方向是否有失败未关闭的单（跨计划也算）。"""
    return conn.execute(
        """
        SELECT id FROM handovers
        WHERE status = '失败' AND from_shift_id = ? AND to_shift_id = ?
        LIMIT 1
        """,
        (from_shift_id, to_shift_id),
    ).fetchone()


def _build_overview(conn, plan) -> dict:
    shift_ids = json.loads(plan["shift_ids_json"])
    names = _shift_names(conn, shift_ids)
    total_segments = len(shift_ids) - 1
    segs = _relay_segments(conn, plan["id"])

    completed = sum(1 for s in segs if s["status"] == "已完成")
    failed = sum(1 for s in segs if s["status"] in FAILED_SEGMENT_STATUSES)
    unclosed_failed = [s["段"] for s in segs if s["status"] == "失败"]
    consistent_segments = sum(1 for s in segs if s["对账结果"] == "一致")
    inconsistent_segments = sum(1 for s in segs if s["对账结果"] == "不一致")

    current_seq = int(plan["current_seq"])
    next_seq = current_seq + 1
    active = _active_handover(conn)
    current_seg = next((s for s in segs if s["段"] == current_seq), None)
    prev_ready = current_seq == 0 or (current_seg is not None and current_seg["status"] == "已完成")
    next_blocked = (
        next_seq <= total_segments
        and _same_route_unclosed_failed(
            conn, shift_ids[next_seq - 1], shift_ids[next_seq]
        )
        is not None
    )
    can_start = (
        plan["status"] in PLAN_OPEN_STATUSES
        and next_seq <= total_segments
        and active is None
        and prev_ready
        and not unclosed_failed
        and not next_blocked
    )

    if plan["status"] == "已完成":
        overall = (
            "全部一致"
            if consistent_segments == total_segments and not inconsistent_segments
            else "存在不一致"
        )
    elif plan["status"] == PLAN_FAILED_STATUS:
        overall = "段失败"
    elif plan["status"] == "已中止":
        overall = "已中止"
    elif plan["status"] == "待启动":
        overall = "未开始"
    else:
        overall = "进行中"

    return {
        "id": plan["id"],
        "name": plan["name"],
        "status": plan["status"],
        "reason": plan["reason"],
        "created_at": plan["created_at"],
        "finished_at": plan["finished_at"],
        "current_seq": current_seq,
        "班次序列": names,
        "段数": total_segments,
        "已完成段数": completed,
        "失败段数": failed,
        "未关闭失败段": unclosed_failed,
        "一致段数": consistent_segments,
        "不一致段数": inconsistent_segments,
        "合计清单数": sum(s["清单数"] for s in segs),
        "可发起下一段": can_start,
        "下一段序号": next_seq if next_seq <= total_segments else None,
        "总对账": overall,
        "段": segs,
    }


def create_plan(name: str | None, shift_ids: list[int]) -> dict:
    if len(shift_ids) < 3:
        raise HTTPException(400, "接力计划至少需要三班，即至少两段交班")
    for a, b in zip(shift_ids, shift_ids[1:]):
        if a == b:
            raise HTTPException(400, "相邻班次不能相同")
    with write_tx() as conn:
        names = _shift_names(conn, shift_ids)
        label = (name or "→".join(names)).strip() or "→".join(names)
        cur = conn.execute(
            """
            INSERT INTO relay_plans(name, shift_ids_json, status, current_seq, created_at)
            VALUES (?, ?, '待启动', 0, ?)
            """,
            (label, json.dumps(shift_ids), now_iso()),
        )
        plan_id = int(cur.lastrowid)
    return get_plan(plan_id)


def get_plan(plan_id: int) -> dict:
    with connect() as conn:
        plan = _plan_or_404(conn, plan_id)
        return _build_overview(conn, plan)


def list_plans() -> list[dict]:
    with connect() as conn:
        plans = rows_to_dicts(
            conn.execute("SELECT * FROM relay_plans ORDER BY id DESC").fetchall()
        )
        out = []
        for plan in plans:
            overview = _build_overview(conn, plan)
            out.append(
                {
                    "id": overview["id"],
                    "name": overview["name"],
                    "status": overview["status"],
                    "reason": overview["reason"],
                    "班次序列": overview["班次序列"],
                    "段数": overview["段数"],
                    "已完成段数": overview["已完成段数"],
                    "失败段数": overview["失败段数"],
                    "合计清单数": overview["合计清单数"],
                    "总对账": overview["总对账"],
                    "可发起下一段": overview["可发起下一段"],
                    "current_seq": overview["current_seq"],
                    "created_at": overview["created_at"],
                }
            )
        return out


def _check_can_start(conn, plan) -> int:
    """逐段发起校验；返回本段序号。"""
    shift_ids = json.loads(plan["shift_ids_json"])
    total = len(shift_ids) - 1
    if plan["status"] == "已完成":
        raise HTTPException(409, "接力计划已完成，不能继续发起")
    if plan["status"] == "已中止":
        raise HTTPException(409, f"接力计划已中止：{plan['reason'] or '人工中止'}")
    if plan["status"] == PLAN_FAILED_STATUS:
        unclosed = conn.execute(
            "SELECT relay_seq FROM handovers WHERE relay_plan_id = ? AND status = '失败' LIMIT 1",
            (plan["id"],),
        ).fetchone()
        if unclosed:
            raise HTTPException(409, f"段{unclosed['relay_seq']}失败未关闭，禁止发起下一段")
        raise HTTPException(
            409, f"接力计划已有段失败：{plan['reason'] or '失败段已关闭'}；请另登记接力计划继续"
        )
    current_seq = int(plan["current_seq"])
    seq = current_seq + 1
    if seq > total:
        raise HTTPException(409, "接力计划没有下一段可发起")

    active = _active_handover(conn)
    if active:
        raise HTTPException(409, f"已有进行中的交班单#{active['id']}，禁止并行交班")

    if current_seq > 0:
        prev = conn.execute(
            "SELECT status FROM handovers WHERE relay_plan_id = ? AND relay_seq = ?",
            (plan["id"], current_seq),
        ).fetchone()
        if not prev or prev["status"] != "已完成":
            raise HTTPException(409, "上一段尚未完成，禁止发起下一段")
    return seq


def start_next_segment(plan_id: int) -> dict:
    with write_tx() as conn:
        plan = _plan_or_404(conn, plan_id)
        seq = _check_can_start(conn, plan)
        shift_ids = json.loads(plan["shift_ids_json"])
        from_shift_id = shift_ids[seq - 1]
        to_shift_id = shift_ids[seq]

        for sid in (from_shift_id, to_shift_id):
            if conn.execute("SELECT id FROM shifts WHERE id = ?", (sid,)).fetchone() is None:
                raise HTTPException(404, f"班次 {sid} 不存在")

        # 同一对班次的上一段失败未关闭时，新计划也不得抢跑
        unclosed_same = _same_route_unclosed_failed(conn, from_shift_id, to_shift_id)
        if unclosed_same:
            raise HTTPException(
                409, f"同一交接（班次{from_shift_id}→{to_shift_id}）存在失败未关闭的段，禁止启动"
            )

        prev_handover = conn.execute(
            """
            SELECT id, to_shift_id FROM handovers
            WHERE relay_plan_id = ? AND relay_seq = ?
            """,
            (plan_id, seq - 1),
        ).fetchone()
        if seq > 1 and (not prev_handover or prev_handover["to_shift_id"] != from_shift_id):
            raise HTTPException(409, "接续段交班方与上一段接班方不一致")

        batches = conn.execute(
            "SELECT id FROM batches WHERE owner_shift_id = ? AND status = '在途' ORDER BY id",
            (from_shift_id,),
        ).fetchall()
        if not batches:
            raise HTTPException(400, f"段{seq}交班方没有在途批次可交接")

        hid = insert_handover_row(
            conn,
            from_shift_id,
            to_shift_id,
            [b["id"] for b in batches],
            now_iso(),
            relay_plan_id=plan_id,
            relay_seq=seq,
        )
        conn.execute(
            "UPDATE relay_plans SET status = '进行中', current_seq = ?, reason = '' WHERE id = ?",
            (seq, plan_id),
        )
    out = get_plan(plan_id)
    out["刚发起段"] = get_handover(hid)
    return out


def abort_plan(plan_id: int) -> dict:
    with write_tx() as conn:
        plan = _plan_or_404(conn, plan_id)
        if plan["status"] not in (*PLAN_OPEN_STATUSES, PLAN_FAILED_STATUS):
            raise HTTPException(400, f"接力计划当前为「{plan['status']}」，无需中止")
        active = _active_handover(conn)
        if active and active["relay_plan_id"] == plan_id:
            raise HTTPException(409, "本计划仍有进行中的段，请先完成或关闭该段")
        # 已失败的段保留原失败原因，人工中止只追加说明
        reason = "人工中止"
        if plan["status"] == PLAN_FAILED_STATUS and plan["reason"]:
            reason = f"{plan['reason']}；人工中止"
        conn.execute(
            "UPDATE relay_plans SET status = '已中止', reason = ?, finished_at = ? WHERE id = ?",
            (reason, now_iso(), plan_id),
        )
    return get_plan(plan_id)
