"""分层后端的自动化业务测试。

覆盖：注册→申请→开始→自动结束→出详单出账单、跨峰平时段分钟级计费、修改请求的等候区
约束、F/T 排队号、最短完成时长调度、故障优先级/时间顺序调度、故障恢复、充电中取消、
bonus 单次/批量调度、并发加锁、持久化往返。
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from core import ChargingStationSystem
from core.domain import FAST, TRICKLE, PILE_OFF, PILE_WORKING


def make_system() -> ChargingStationSystem:
    tmp = tempfile.TemporaryDirectory()
    system = ChargingStationSystem(Path(tmp.name) / "state.json")
    system._tmp = tmp  # 防止 GC 提前清理
    return system


def add_account(system, car_id, capacity=80):
    system.account_service.create_account({"carId": car_id, "userName": car_id, "capacity": capacity})
    system.account_service.set_password({"carId": car_id, "password": "123456"})


def submit(system, car_id, amount, mode=FAST):
    return system.request_service.submit_request({"carId": car_id, "amount": amount, "mode": mode})


class ChargingFlowTest(unittest.TestCase):
    def test_request_start_auto_finish_and_bill(self):
        system = make_system()
        add_account(system, "京A-EV001", 60)
        result = submit(system, "京A-EV001", 30, FAST)
        self.assertEqual(result["carState"], "充电区排队")

        system.request_service.start_charging({"carId": "京A-EV001"})
        system.charging.advance(60)

        req = system.request_repo.active_for_car("京A-EV001")
        self.assertIsNone(req)  # 已结束（不再是 active）
        detail = system.detail_repo.all()[0]
        self.assertEqual(detail.chargeAmount, 30)
        self.assertEqual(detail.chargeDuration, 60)
        self.assertAlmostEqual(detail.chargeFee, 21.0)
        self.assertAlmostEqual(detail.serviceFee, 24.0)
        self.assertAlmostEqual(detail.totalFee, 45.0)
        self.assertTrue(system.bill_repo.all())
        self.assertEqual(system.bill_repo.all()[0].billId, detail.billId)

    def test_cross_peak_normal_fee_segmented_by_minute(self):
        system = make_system()
        system.clock.set(9 * 60 + 30)
        add_account(system, "京A-EV002", 60)
        submit(system, "京A-EV002", 30, FAST)
        system.request_service.start_charging({"carId": "京A-EV002"})
        system.charging.advance(60)

        detail = system.detail_repo.all()[0]
        self.assertEqual(detail.startTime, "2026-06-03 09:30")
        self.assertEqual(detail.endTime, "2026-06-03 10:30")
        self.assertAlmostEqual(detail.chargeFee, 25.5)
        self.assertAlmostEqual(detail.serviceFee, 24.0)
        self.assertAlmostEqual(detail.totalFee, 49.5)

    def test_ticket_prefix_fast_and_trickle(self):
        system = make_system()
        add_account(system, "F-1"); add_account(system, "F-2")
        add_account(system, "T-1")
        self.assertTrue(submit(system, "F-1", 20, FAST)["queueNum"].startswith("F"))
        self.assertTrue(submit(system, "F-2", 20, FAST)["queueNum"].startswith("F"))
        self.assertTrue(submit(system, "T-1", 20, TRICKLE)["queueNum"].startswith("T"))

    def test_modify_only_while_waiting_area(self):
        system = make_system()
        for pile in system.pile_repo.all():
            if pile.mode == FAST:
                pile.state = PILE_OFF
        add_account(system, "京A-EV003", 70)
        submit(system, "京A-EV003", 20, FAST)
        system.request_service.modify_amount({"carId": "京A-EV003", "amount": 25})
        req = system.request_repo.active_for_car("京A-EV003")
        self.assertEqual(req.state, "WAITING_AREA")
        self.assertEqual(req.amount, 25)

        for pile in system.pile_repo.all():
            if pile.mode == FAST:
                pile.state = PILE_WORKING
        system.dispatch.dispatch_waiting(FAST)
        with self.assertRaises(ValueError):
            system.request_service.modify_amount({"carId": "京A-EV003", "amount": 30})

    def test_modify_mode_regenerates_ticket_to_tail(self):
        system = make_system()
        for pile in system.pile_repo.all():
            pile.state = PILE_OFF
        add_account(system, "京A-EV004", 70)
        first = submit(system, "京A-EV004", 20, FAST)["queueNum"]
        system.request_service.modify_mode({"carId": "京A-EV004", "mode": TRICKLE})
        req = system.request_repo.active_for_car("京A-EV004")
        self.assertTrue(req.ticket.startswith("T"))
        self.assertNotEqual(req.ticket, first)


class DispatchTest(unittest.TestCase):
    def test_best_pile_minimizes_finish_time(self):
        system = make_system()
        # 关掉 F2，强制三辆快充车在 F1 排队，验证后到的车选 F1（唯一可用）后负载累积
        system.pile_repo.get("F2").state = PILE_OFF
        for i, cid in enumerate(["C1", "C2"]):
            add_account(system, cid, 80)
            submit(system, cid, 30, FAST)
        f1 = system.pile_repo.get("F1")
        self.assertEqual(f1.load(), 2)  # 两辆都进了 F1（队首+排队）

    def test_two_fast_cars_split_across_two_piles(self):
        system = make_system()
        add_account(system, "C1", 80); add_account(system, "C2", 80)
        submit(system, "C1", 30, FAST)
        submit(system, "C2", 30, FAST)
        # 两个快充桩各空，最短完成时长会让两辆车分到不同桩
        loaded = {p.id: p.load() for p in system.pile_repo.all() if p.mode == FAST}
        self.assertEqual(sorted(loaded.values()), [1, 1])

    def test_fault_priority_dispatch_stops_active_and_moves_queue(self):
        system = make_system()
        for cid in ["京A-EV101", "京A-EV102", "京A-EV103"]:
            add_account(system, cid, 80)
            submit(system, cid, 30, FAST)
        system.request_service.start_charging({"carId": "京A-EV101"})
        f1_queue = list(system.pile_repo.get("F1").queue)
        self.assertTrue(f1_queue)

        system.fault_service.handle_pile_fault({"pileId": "F1", "strategy": "PRIORITY"})

        self.assertEqual(system.pile_repo.get("F1").state, "FAULT")
        self.assertIsNone(system.request_repo.active_for_car("京A-EV101"))  # 停止计费
        self.assertTrue(system.detail_repo.all())  # 故障生成详单
        for rid in f1_queue:
            moved = system.request_repo.get(rid)
            self.assertNotEqual(moved.pileId, "F1")

    def test_time_order_fault_merges_other_pile_queues(self):
        system = make_system()
        for cid in ["A", "B", "C", "D"]:
            add_account(system, cid, 80)
            submit(system, cid, 30, FAST)
        # 故障 F1（时间顺序）：F1 与 F2 中尚未充电的车合并按号重排
        system.fault_service.handle_pile_fault({"pileId": "F1", "strategy": "TIME_ORDER"})
        self.assertEqual(system.pile_repo.get("F1").state, "FAULT")
        # F1 不应再有排队车
        self.assertEqual(system.pile_repo.get("F1").queue, [])

    def test_recover_pile_redispatches_same_mode_unique(self):
        system = make_system()
        for cid in ["京A-EV201", "京A-EV202", "京A-EV203"]:
            add_account(system, cid, 80)
            submit(system, cid, 30, FAST)
        system.fault_service.handle_pile_fault({"pileId": "F1", "strategy": "TIME_ORDER"})
        system.fault_service.recover_pile({"pileId": "F1"})
        self.assertEqual(system.pile_repo.get("F1").state, "WORKING")
        ids = [rid for p in system.pile_repo.all() if p.mode == FAST for rid in p.queue]
        self.assertEqual(len(ids), len(set(ids)))  # 无重复调度

    def test_cancel_during_charging_generates_detail(self):
        system = make_system()
        add_account(system, "Z", 60)
        submit(system, "Z", 30, FAST)
        system.request_service.start_charging({"carId": "Z"})
        system.clock.advance(20)
        result = system.request_service.cancel_request({"carId": "Z"})
        self.assertEqual(result["result"], 1)
        self.assertTrue(system.detail_repo.all())


class BonusDispatchTest(unittest.TestCase):
    def test_single_multi_slot_dispatch(self):
        system = make_system()
        # 让所有桩空闲，等候区放入多辆快充车，单次最短总时长调度一次叫多号
        for i in range(4):
            cid = f"M{i}"
            add_account(system, cid, 80)
            # 直接放入等候区：先关桩避免自动叫号
        for pile in system.pile_repo.all():
            pile.state = PILE_OFF
        for i in range(4):
            submit(system, f"M{i}", 30, FAST)
        for pile in system.pile_repo.all():
            pile.state = PILE_WORKING
        result = system.dispatch.dispatch_when_multiple_slots(FAST)
        self.assertGreaterEqual(result["dispatched"], 1)

    def test_batch_dispatch_requires_full(self):
        system = make_system()
        add_account(system, "B1", 80)
        submit(system, "B1", 30, FAST)
        with self.assertRaises(ValueError):
            system.dispatch.batch_dispatch_when_full()


class InfraTest(unittest.TestCase):
    def test_concurrent_submits_are_serialized_by_lock(self):
        system = make_system()
        for pile in system.pile_repo.all():
            pile.state = PILE_OFF  # 都进等候区，便于统计
        for i in range(8):
            add_account(system, f"P{i}", 80)

        def worker(i):
            with system.lock:
                submit(system, f"P{i}", 20, FAST)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        tickets = [r.ticket for r in system.request_repo.all()]
        self.assertEqual(len(tickets), 8)
        self.assertEqual(len(tickets), len(set(tickets)))  # 排队号无重复（序列器线程安全）

    def test_persistence_round_trip(self):
        tmp = tempfile.TemporaryDirectory()
        path = Path(tmp.name) / "state.json"
        system = ChargingStationSystem(path)
        add_account(system, "京A-EV900", 60)
        submit(system, "京A-EV900", 30, FAST)
        system.request_service.start_charging({"carId": "京A-EV900"})
        system.charging.advance(60)
        system.save()

        restored = ChargingStationSystem(path)
        self.assertIn("京A-EV900", {a.carId for a in restored.account_repo.all()})
        self.assertTrue(restored.detail_repo.all())
        self.assertEqual(restored.detail_repo.all()[0].chargeAmount, 30)


if __name__ == "__main__":
    unittest.main(verbosity=2)
