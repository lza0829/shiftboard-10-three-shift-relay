from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
os.environ.setdefault("DATA_DIR", str(ROOT / "backend" / "data_test_relay"))
os.environ.setdefault("FIXTURES_DIR", str(ROOT / "fixtures"))

from app.db import connect  # noqa: E402
from app.main import app  # noqa: E402
from app import seed  # noqa: E402


@pytest.fixture(scope="module")
def client():
    db_path = Path(os.environ["DATA_DIR"]) / "app.db"
    if db_path.exists():
        db_path.unlink()
    with TestClient(app) as c:
        yield c


def _reset_to_seed() -> None:
    """把库恢复到初始种子，供各用例独立跑整条接力链。

    先摘掉禁回滚触发器再清表（触发器保护的是运行期回滚，不是测试重置），
    随后 seed.init_db() 会把触发器原样装回。
    """
    with connect() as conn:
        for trg in (
            "trg_handover_completed_no_status_change",
            "trg_handover_completed_no_rewire",
            "trg_handover_items_completed_no_delete",
            "trg_handover_items_completed_no_insert",
            "trg_batch_transfer_requires_active_segment",
        ):
            conn.execute(f"DROP TRIGGER IF EXISTS {trg}")
        for table in (
            "reconcile_reports",
            "handover_items",
            "handovers",
            "relay_plans",
            "batches",
            "shifts",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    seed.init_db()


def _start_plan(client, shift_ids=(1, 2, 3)):
    r = client.post("/api/relay-plans", json={"shift_ids": list(shift_ids)})
    assert r.status_code == 200, r.text
    return r.json()


def test_plan_requires_three_shifts(client):
    _reset_to_seed()
    r = client.post("/api/relay-plans", json={"shift_ids": [1, 2]})
    assert r.status_code == 400
    r = client.post("/api/relay-plans", json={"shift_ids": [1, 1, 2]})
    assert r.status_code == 400


def test_sequential_segments_and_failure_isolation(client):
    _reset_to_seed()
    plan = _start_plan(client)
    pid = plan["id"]
    assert plan["段数"] == 2
    assert plan["可发起下一段"] is True

    # 第一段发起
    first = client.post(f"/api/relay-plans/{pid}/segments/start")
    assert first.status_code == 200, first.text
    seg1 = first.json()["刚发起段"]
    assert seg1["relay_seq"] == 1
    assert seg1["from_shift_id"] == 1 and seg1["to_shift_id"] == 2

    # 上一段进行中：不得发起下一段，也不得并行开普通交班
    assert client.post(f"/api/relay-plans/{pid}/segments/start").status_code == 409
    assert (
        client.post("/api/handovers/start", json={"from_shift_id": 2, "to_shift_id": 3}).status_code
        == 409
    )

    # 完成第一段：整批过户到第二班
    done1 = client.post(f"/api/handovers/{seg1['id']}/complete")
    assert done1.status_code == 200, done1.text
    assert done1.json()["status"] == "已完成"
    owner_after_1 = {
        b["id"]: b["owner_shift_id"] for b in client.get("/api/batches").json()
    }
    assert owner_after_1[1] == 2 and owner_after_1[2] == 2

    # 第二段：发起后制造对账不一致（交班方多出一批在途），完成时应失败并回滚本段
    second = client.post(f"/api/relay-plans/{pid}/segments/start")
    assert second.status_code == 200, second.text
    seg2 = second.json()["刚发起段"]
    assert seg2["relay_seq"] == 2
    with connect() as conn:
        conn.execute(
            "INSERT INTO batches(biz_key, title, owner_shift_id, status) VALUES (?, ?, ?, '在途')",
            ("B-EXTRA", "交接窗口内新到的批次", 2),
        )
        conn.commit()

    failed = client.post(f"/api/handovers/{seg2['id']}/complete")
    assert failed.status_code == 200, failed.text
    failed_body = failed.json()
    assert failed_body["status"] == "失败"
    assert "已回滚" in failed_body["reason"] or "不一致" in failed_body["reason"]

    # 失败只关本段：上一段仍是已完成，其过户结果未被回滚
    assert client.get(f"/api/handovers/{seg1['id']}").json()["status"] == "已完成"
    owners = {b["id"]: b["owner_shift_id"] for b in client.get("/api/batches").json()}
    assert owners[1] == 2 and owners[2] == 2
    assert owners[3] == 2, "第二段失败后批次仍应留在第二段交班方，未被移交第三班"

    # 接力总览：分段列出、状态、清单数、对账、失败原因
    overview = client.get(f"/api/relay-plans/{pid}").json()
    assert overview["status"] == "段失败"
    assert overview["已完成段数"] == 1 and overview["失败段数"] == 1
    assert overview["未关闭失败段"] == [2]
    assert overview["总对账"] == "段失败"
    segs = {s["段"]: s for s in overview["段"]}
    assert segs[1]["status"] == "已完成"
    assert segs[1]["对账结果"] == "一致"
    assert segs[1]["清单数"] == 4
    assert segs[2]["status"] == "失败"
    assert segs[2]["对账结果"] == "不一致"
    assert "已回滚本段" in segs[2]["失败原因"] or "不一致" in segs[2]["失败原因"]

    # 失败未关闭：禁止续段；关闭后计划停在段失败，需另登记计划继续
    assert client.post(f"/api/relay-plans/{pid}/segments/start").status_code == 409
    closed = client.post(f"/api/handovers/{seg2['id']}/close")
    assert closed.status_code == 200, closed.text
    assert closed.json()["status"] == "失败已关闭"
    after_close = client.post(f"/api/relay-plans/{pid}/segments/start")
    assert after_close.status_code == 409
    assert "另登记" in after_close.json()["detail"]
    assert client.get(f"/api/relay-plans/{pid}").json()["未关闭失败段"] == []

    # 刷新后仍在：重新查询仍能看到失败原因与两段清单
    again = client.get(f"/api/relay-plans/{pid}").json()
    assert again["段"][1]["失败原因"]
    assert again["段"][0]["清单数"] == 4


def test_manual_fail_keeps_previous_segment(client):
    _reset_to_seed()
    plan = _start_plan(client)
    pid = plan["id"]
    seg1 = client.post(f"/api/relay-plans/{pid}/segments/start").json()["刚发起段"]
    assert client.post(f"/api/handovers/{seg1['id']}/complete").status_code == 200
    seg2 = client.post(f"/api/relay-plans/{pid}/segments/start").json()["刚发起段"]

    r = client.post(f"/api/handovers/{seg2['id']}/fail", json={"reason": "夜班未到位"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "失败"
    assert "夜班未到位" in r.json()["reason"]

    assert client.get(f"/api/handovers/{seg1['id']}").json()["status"] == "已完成"
    owners = {b["id"]: b["owner_shift_id"] for b in client.get("/api/batches").json()}
    assert owners[1] == 2

    overview = client.get(f"/api/relay-plans/{pid}").json()
    assert overview["status"] == "段失败"
    assert "夜班未到位" in overview["reason"]
    # 段2双方解冻：交班方回到值班中，接班方空闲
    shifts = {s["id"]: s["status"] for s in client.get("/api/shifts").json()}
    assert shifts[2] == "值班中" and shifts[3] == "空闲"


def test_completed_segment_cannot_be_rolled_back(client):
    _reset_to_seed()
    plan = _start_plan(client)
    pid = plan["id"]
    seg1 = client.post(f"/api/relay-plans/{pid}/segments/start").json()["刚发起段"]
    assert client.post(f"/api/handovers/{seg1['id']}/complete").status_code == 200

    with pytest.raises(sqlite3.IntegrityError):
        with connect() as conn:
            conn.execute("UPDATE handovers SET status = '进行中' WHERE id = ?", (seg1["id"],))

    with pytest.raises(sqlite3.IntegrityError):
        with connect() as conn:
            conn.execute(
                "DELETE FROM handover_items WHERE handover_id = ?", (seg1["id"],)
            )


def test_segment_seq_unique_per_plan(client):
    _reset_to_seed()
    plan = _start_plan(client)
    pid = plan["id"]
    seg1 = client.post(f"/api/relay-plans/{pid}/segments/start").json()["刚发起段"]
    with pytest.raises(sqlite3.IntegrityError):
        with connect() as conn:
            conn.execute(
                """
                INSERT INTO handovers(
                  from_shift_id, to_shift_id, status, batch_count, started_at,
                  relay_plan_id, relay_seq
                ) VALUES (2, 3, '进行中', 1, 'x', ?, 1)
                """,
                (pid,),
            )


def test_overview_counts_are_derived_not_counter(client):
    _reset_to_seed()
    plan = _start_plan(client)
    pid = plan["id"]
    seg1 = client.post(f"/api/relay-plans/{pid}/segments/start").json()["刚发起段"]
    assert client.post(f"/api/handovers/{seg1['id']}/complete").status_code == 200
    overview = client.get(f"/api/relay-plans/{pid}").json()
    item_sum = sum(s["清单数"] for s in overview["段"])
    assert overview["合计清单数"] == item_sum
    assert item_sum == len(client.get(f"/api/handovers/{seg1['id']}").json()["items"])
