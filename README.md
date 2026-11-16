# 06-shiftboard（交班台）

交班台 — 班次责任与在途批次交接：班次登记 → 在途批次归属 → 交班互斥办理 → 交班前后对账。

**不是** 多源 Feed 归并，**不是** 作业依赖编排，**不是** CDC 变更流，**不是** 质检签发，**不是** 数仓贴源/明细/汇总分层。  
**也不是** OA 考勤排班后台、电商订单、报表 CSV 看板。

## 启动

本地开发：

```bash
# 后端
cd backend
pip install -r requirements.txt
set DATA_DIR=%CD%\data
set FIXTURES_DIR=%CD%\..\fixtures
uvicorn app.main:app --host 127.0.0.1 --port 10600

# 前端
cd frontend
npm install
npm run dev
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://127.0.0.1:5600 |
| 接口 | http://127.0.0.1:10600 |

## 主链

登记班次与值班人 → 在途批次归属当前班次 → 发起交班（全仓同时仅一单进行中）→ 完成交班整批过户 → 交班对账报告与清单/批次同源

## 接力交班（早 → 晚 → 夜）

一条接力计划 = 有序班次列表（至少三班，如 早/晚/夜），相邻两班构成一段交班。
按段逐次发起，上一段已完成才允许开下一段；任一段失败只停本段，不回滚已完成的段。

### 计划与段的关系

采用「独立接力计划表 + 段交班单挂计划 id 与段序号」：

- `relay_plans(id, name, shift_ids_json, loop, status, current_seq, reason, …)`
- `handovers.relay_plan_id → relay_plans(id)`，`handovers.relay_seq` 为段序号
- 唯一索引 `uq_handovers_relay_seq(relay_plan_id, relay_seq)`：同一计划内段序号不可重复；
  独立单段交班两列为 NULL，不参与约束
- 发起下一段时校验：计划未中止/未完成、上一段 `已完成`、无全局进行中单、同一交接无失败未关闭单

### 失败隔离：段级独立事务（择一，本仓采用）

每段的发起/完成/判失败都走 `write_tx()`（`BEGIN IMMEDIATE`），事务边界严格限定在本段：

- 完成一段时，过户与对账在同一事务内提交，**不触碰此前已提交的段**；
- 过户后对账不一致 → 整个本段事务 `ROLLBACK`（批次归属原样退回本段交班方），
  再另起事务写失败记录、把本段双方解冻（交班方回「值班中」、接班方回「空闲」）；
- 人工判失败（`POST /api/handovers/{id}/fail`）同样只关本段并解冻本段双方；
- 兜底钩子（`seed.py` 的 `TRIGGERS`）：已完成段禁止改状态/改双方/改挂接、清单禁止增删，
  已过户给接班方的批次只能经后继「进行中」段再次过户 —— 即使有人写 SQL 也无法回滚前段。

失败后计划状态为「段失败」：失败段未关闭时禁止发起任何后续段；关闭后该计划不再推进
（已完成的段保持原样），继续接力需另登记新计划。这样「上一段已完成」始终是开下一段的硬前提。

### 接力总览：查询时同源派生（择一，本仓采用）

`GET /api/relay-plans/{id}` 不读任何总计数器，而是每次由以下同源数据拼装：

- 各段交班单：`handovers`（状态、双方、起止、失败原因）
- 各段清单数：`COUNT(handover_items)`
- 各段对账结果：该段最新 `reconcile_reports.consistent`
- 汇总（已完成段数、失败段数、一致段数、合计清单数、总对账）均由上述分段结果即时聚合

因此刷新、重启后总览与分段明细始终一致，不存在旁路累加漂移。

### 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/relay-plans` | 计划列表（含段进度摘要） |
| POST | `/api/relay-plans` | 登记计划，body `{name?, shift_ids:[…]}`，至少三班 |
| GET | `/api/relay-plans/{id}` | 接力总览：分段交班单/状态/清单数/对账/失败原因 |
| POST | `/api/relay-plans/{id}/segments/start` | 发起下一段（校验顺序与全局互斥） |
| POST | `/api/relay-plans/{id}/abort` | 中止计划（无进行中段时） |
| POST | `/api/handovers/{id}/complete` | 完成该段；对账不一致则本段回滚并记失败 |
| POST | `/api/handovers/{id}/fail` | 人工判失败，只关本段并解冻双方 |
| POST | `/api/handovers/{id}/close` | 关闭失败段（此后仅可另登记新计划） |

## 界面约定

页面文案全部中文；入口：总览 / 班次 / 在途批次 / 接力交班 / 交班办理 / 交班记录 / 交班对账。
接力计划详情页（`/relay/:id`）显示分段状态、各段清单数、对账结果与失败原因，数据全部来自
后端持久化查询，刷新后仍在。

## 与已有仓差异

| 仓 | 骨架 |
| --- | --- |
| Feedstitch | 多源解析→冲突隔离→水位 |
| Batchgate | 作业依赖→重试→水位 |
| Changeflow | 事件序→缺口→Sink 游标 |
| Checklane | 入检→规则→隔离签发 |
| Tiercraft | 贴源→明细→汇总→层间对账 |
| **本仓** | **班次归属→交班互斥→批次过户→交班对账** |
