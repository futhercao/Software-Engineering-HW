"""业务 / 应用层（Business / Application Layer）。

按照课程「业务/应用层对象设计原则」，本层的服务类实现用例要求的各种系统级功能。命名
尽量沿用领域模型中的概念（计费、调度、充电、账单、监控、故障恢复等），每个服务遵循
单一职责，通过依赖注入持有所需仓储与支撑设施，并以「信息专家 + 创建者 + 控制器」等
GRASP 模式分配职责、协作完成用例。
"""

from __future__ import annotations

import itertools

from .domain import (
    Bill,
    ChargeSession,
    ChargingPile,
    ChargingRequest,
    ChargingRule,
    DetailedList,
    EVCar,
    UserAccount,
    FAST,
    TRICKLE,
    MODE_NAMES,
    PILE_WORKING,
    PILE_OFF,
    PILE_FAULT,
    ST_CANCELLED,
    ST_CHARGING,
    ST_CHARGING_AREA,
    ST_ENDED,
    ST_WAITING_AREA,
    STATE_NAMES,
    date_for_minutes,
    format_time,
    ticket_prefix,
)
from .fault_strategies import get_fault_strategy


# ===========================================================================
# 计费服务
# ===========================================================================

class PricingService:
    """计费服务。按需求规定的三时段电价与服务费单价，以分钟粒度计算费用。"""

    def __init__(self, rule_repo) -> None:
        self.rule_repo = rule_repo

    def price_at(self, minutes: int) -> float:
        return self.rule_repo.get().price_at(minutes)

    def calculate_fee(self, start: int, end: int, amount: float, power: float) -> dict:
        """充电费按实际充电时间跨越的峰/平/谷时段分段累加；服务费 = 单价 × 实际度数。"""
        rule = self.rule_repo.get()
        duration = max(1, int(end - start))
        remaining = float(amount)
        charge_fee = 0.0
        for offset in range(duration):
            if remaining <= 0:
                break
            energy = min(float(power) / 60, remaining)  # 每分钟充入的电量（度）
            charge_fee += energy * rule.price_at(start + offset)
            remaining -= energy
        service_fee = float(amount) * float(rule.service)
        return {
            "chargeFee": round(charge_fee, 2),
            "serviceFee": round(service_fee, 2),
            "totalFee": round(charge_fee + service_fee, 2),
        }


# ===========================================================================
# 账单 / 详单服务
# ===========================================================================

class BillingService:
    """账单与详单服务。负责生成详单、按车辆按日汇总账单，并提供查询。"""

    def __init__(self, bill_repo, detail_repo, sequencer) -> None:
        self.bill_repo = bill_repo
        self.detail_repo = detail_repo
        self.sequencer = sequencer

    def create_detail(
        self,
        request: ChargingRequest,
        pile: ChargingPile,
        start: int,
        end: int,
        charged: float,
        elapsed: int,
        fee: dict,
        reason: str,
    ) -> DetailedList:
        detail = DetailedList(
            detailId=self.sequencer.next_detail_id(),
            billId=f"BILL-{date_for_minutes(end)}-{request.carId}",
            carId=request.carId,
            date=date_for_minutes(end),
            pileId=pile.id,
            chargeAmount=round(float(charged), 2),
            chargeDuration=int(elapsed),
            startTime=format_time(start),
            endTime=format_time(end),
            chargeFee=fee["chargeFee"],
            serviceFee=fee["serviceFee"],
            totalFee=fee["totalFee"],
            generatedAt=format_time(end),
            reason=reason,
        )
        self.detail_repo.add(detail)
        self._upsert_bill(detail)
        return detail

    def _upsert_bill(self, detail: DetailedList) -> None:
        bill = self.bill_repo.find(detail.billId, detail.carId)
        if not bill:
            bill = Bill(billId=detail.billId, carId=detail.carId, date=detail.date)
            self.bill_repo.add(bill)
        bill.add_detail(detail)

    def query_bill(self, car_id: str = "", day: str = "") -> dict:
        bills = self.bill_repo.query(car_id, day)
        summary = {
            "chargeAmount": round(sum(b.chargeAmount for b in bills), 2),
            "chargeDuration": sum(b.chargeDuration for b in bills),
            "chargeFee": round(sum(b.chargeFee for b in bills), 2),
            "serviceFee": round(sum(b.serviceFee for b in bills), 2),
            "totalFee": round(sum(b.totalFee for b in bills), 2),
        }

        def bill_view(bill: Bill) -> dict:
            # 账单按车按日汇总（Total* 为当日合计）；ChargePileNum/StartTime/EndTime 等逐次字段
            # 由其下的详单行项承载，故附带明细行，契合《系统事件表》Request_Bill 的返回字段集合。
            data = bill.to_dict()
            data["details"] = [d.to_dict() for d in self.detail_repo.query(bill.carId, bill.billId)]
            return data

        return {"bills": [bill_view(b) for b in bills], "summary": summary}

    def query_details(self, car_id: str = "", bill_id: str = "") -> dict:
        return {"details": [d.to_dict() for d in self.detail_repo.query(car_id, bill_id)]}

    def report(self, period: str = "day") -> list[dict]:
        """按日/周/月 × 充电桩聚合详单，生成运营报表。"""
        from datetime import date as _date

        rows: dict[tuple[str, str], dict] = {}
        for detail in self.detail_repo.all():
            day = detail.date
            if period == "month":
                bucket = day[:7]
            elif period == "week":
                iso = _date.fromisoformat(day).isocalendar()
                bucket = f"{iso.year}-W{iso.week:02d}"
            else:
                bucket = day
            key = (bucket, detail.pileId)
            row = rows.setdefault(
                key,
                {
                    "period": bucket,
                    "pileId": detail.pileId,
                    "totalChargeNum": 0,
                    "totalChargeTime": 0,
                    "totalCapacity": 0.0,
                    "totalChargeFee": 0.0,
                    "totalServiceFee": 0.0,
                    "totalFee": 0.0,
                },
            )
            row["totalChargeNum"] += 1
            row["totalChargeTime"] += detail.chargeDuration
            row["totalCapacity"] += detail.chargeAmount
            row["totalChargeFee"] += detail.chargeFee
            row["totalServiceFee"] += detail.serviceFee
            row["totalFee"] += detail.totalFee
        result = list(rows.values())
        for row in result:
            for f in ("totalCapacity", "totalChargeFee", "totalServiceFee", "totalFee"):
                row[f] = round(row[f], 2)
        result.sort(key=lambda r: (r["period"], r["pileId"]))
        return result


# ===========================================================================
# 排队服务（排队号生成 + 队列状态查询）
# ===========================================================================

class QueueService:
    """排队服务。负责生成 F/T 排队号、计算前车数量、汇总各队列状态。"""

    def __init__(self, sequencer, request_repo, pile_repo, clock, rule_repo) -> None:
        self.sequencer = sequencer
        self.request_repo = request_repo
        self.pile_repo = pile_repo
        self.clock = clock
        self.rule_repo = rule_repo

    def make_ticket(self, mode: str) -> str:
        return f"{ticket_prefix(mode)}{self.sequencer.next_ticket_number(mode)}"

    def waiting_count(self) -> int:
        return len(self.request_repo.waiting())

    def count_before(self, request: ChargingRequest) -> int:
        """本充电模式下当前车辆前方的车辆数（等候区按号排序；充电区按桩内位置）。"""
        if request.state == ST_WAITING_AREA:
            same_mode = self.request_repo.waiting(request.mode)
            same_mode.sort(key=lambda r: (not r.priority, r.ticket_order, r.createdAt))
            return next(
                (i for i, r in enumerate(same_mode) if r.id == request.id), 0
            )
        pile = self.pile_repo.get(request.pileId or (request.session.pileId if request.session else None))
        if not pile:
            return 0
        before = 1 if pile.activeRequestId and pile.activeRequestId != request.id else 0
        if request.id in pile.queue:
            before += pile.queue.index(request.id)
        return before

    def queue_rows(self) -> list[dict]:
        rows: list[dict] = []
        now = self.clock.now()
        for pile in self.pile_repo.all():
            active = self.request_repo.get(pile.activeRequestId)
            if active and active.session:
                rows.append(self._row(pile.id, active, now - active.session.start, "充电中"))
            for request_id in pile.queue:
                req = self.request_repo.get(request_id)
                if req:
                    rows.append(self._row(pile.id, req, now - req.createdAt, "充电区排队"))
        for req in self.request_repo.waiting():
            rows.append(self._row("等候区", req, now - req.createdAt, "等候区"))
        return rows

    @staticmethod
    def _row(queue: str, req: ChargingRequest, wait: int, state: str) -> dict:
        return {
            "queue": queue,
            "carId": req.carId,
            "ticket": req.ticket,
            "capacity": req.capacity,
            "amount": req.amount,
            "waitTime": max(0, int(wait)),
            "state": state,
        }


# ===========================================================================
# 调度服务（叫号 + 最短完成时长调度 + 故障重排 + bonus 扩展调度）
# ===========================================================================

class DispatchService:
    """调度服务。实现需求核心调度策略：选择使被调度车辆「完成充电所需时长最短」的充电桩。

    完成充电所需时长 = 等待时间 + 自己充电时间；
      · 等待时间 = 目标桩队列中所有未完成车辆从当前时刻到完成充电的时间之和；
      · 自己充电时间 = 请求充电量 / 充电桩功率。
    """

    def __init__(self, request_repo, pile_repo, rule_repo, clock, logger) -> None:
        self.request_repo = request_repo
        self.pile_repo = pile_repo
        self.rule_repo = rule_repo
        self.clock = clock
        self.logger = logger

    # ---- 单桩完成时长估计 -------------------------------------------------
    def pile_finish_minutes(self, pile: ChargingPile, amount: float) -> float:
        total = 0.0
        active = self.request_repo.get(pile.activeRequestId)
        if active and active.session:
            total += float(active.session.remainingMinutes)
        for request_id in pile.queue:
            req = self.request_repo.get(request_id)
            if req:
                total += req.charging_minutes(pile.power)
        total += (float(amount) / float(pile.power)) * 60
        return total

    def best_pile(self, mode: str, amount: float, exclude_pile_id: str | None = None) -> ChargingPile | None:
        queue_len = self.rule_repo.get().chargingQueueLen
        candidates = [
            p
            for p in self.pile_repo.all()
            if p.mode == mode and p.has_slot(queue_len) and p.id != exclude_pile_id
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda p: (self.pile_finish_minutes(p, amount), p.id))

    # ---- 基础叫号与分配 ---------------------------------------------------
    def _attach(self, req: ChargingRequest, pile: ChargingPile) -> None:
        pile.queue.append(req.id)
        req.state = ST_CHARGING_AREA
        req.pileId = pile.id
        req.priority = False
        self.logger.log(f"{req.ticket} 进入 {pile.id} 队列")

    def assign_to_best_pile(self, req: ChargingRequest, exclude_pile_id: str | None = None) -> bool:
        pile = self.best_pile(req.mode, req.amount, exclude_pile_id)
        if not pile:
            req.state = ST_WAITING_AREA
            req.pileId = None
            return False
        self._attach(req, pile)
        return True

    def dispatch_waiting(self, mode: str | None = None) -> None:
        """等候区叫号：每次取排在最前、且能被分配的车辆进入充电区，直到无车可调度。"""
        while True:
            waiting = self.request_repo.waiting(mode)
            waiting.sort(key=lambda r: (not r.priority, r.ticket_order, r.createdAt))
            if not waiting:
                return
            # 取排在最前、且其模式对应充电桩尚有空位的车辆叫号；若某模式无空位，
            # 则顺延到下一辆能匹配空位的车（例如快充桩满时叫第一辆慢充车）。
            if not any(self.assign_to_best_pile(req) for req in waiting):
                return

    def redispatch_specific(self, requests: list[ChargingRequest], exclude_pile_id: str | None = None) -> None:
        for req in requests:
            req.state = ST_WAITING_AREA
            req.pileId = None
            self.assign_to_best_pile(req, exclude_pile_id)

    def collect_queued_by_mode(self, mode: str, exclude_pile_id: str | None = None) -> list[ChargingRequest]:
        """把同类型（排除指定桩）各桩中尚未充电（仅排队）的车辆收回等候态并返回。"""
        collected: list[ChargingRequest] = []
        for pile in self.pile_repo.by_mode(mode, exclude_id=exclude_pile_id):
            queue_ids = pile.queue
            pile.queue = []
            for request_id in queue_ids:
                req = self.request_repo.get(request_id)
                if req:
                    req.state = ST_WAITING_AREA
                    req.pileId = None
                    req.priority = True
                    collected.append(req)
        return collected

    # ---- bonus：扩展调度（选做加分） -------------------------------------
    def _free_slots(self, mode: str) -> int:
        queue_len = self.rule_repo.get().chargingQueueLen
        return sum(max(0, queue_len - p.load()) for p in self.pile_repo.all()
                   if p.mode == mode and p.is_working)

    def dispatch_when_multiple_slots(self, mode: str) -> dict:
        """单次调度总充电时长最短（bonus）。

        当某模式充电区出现多个空位时，一次叫多个号；进入充电区的多辆车不考虑排队先后，
        采用「按完成总时长最短」的组合分配（候选规模很小，直接枚举求最优）。
        """
        slots = self._free_slots(mode)
        waiting = self.request_repo.waiting(mode)
        waiting.sort(key=lambda r: (not r.priority, r.ticket_order, r.createdAt))
        chosen = waiting[:slots]
        if not chosen:
            return {"dispatched": 0, "totalFinishMinutes": 0.0}
        assignment, total = self._optimal_assignment(chosen, mode, None)
        for req, pile in assignment:
            self._attach(req, pile)
        self.logger.log(f"单次最短总时长调度：{len(assignment)} 辆，累计完成 {total:.0f} 分钟")
        return {"dispatched": len(assignment), "totalFinishMinutes": round(total, 2)}

    def batch_dispatch_when_full(self) -> dict:
        """批量调度总充电时长最短（bonus）。

        仅当到站车辆数 = 全部车位（充电区 + 等候区）时启动；不区分快/慢充与到达顺序，
        所有车辆可分配任意类型充电桩，按完成总时长最短统一分配。
        """
        rule = self.rule_repo.get()
        capacity = rule.waitingAreaSize + len(self.pile_repo.all()) * rule.chargingQueueLen
        active = [r for r in self.request_repo.all() if r.is_active]
        if len(active) < capacity:
            raise ValueError(
                f"批量调度需到站车辆填满全部 {capacity} 个车位，当前 {len(active)} 辆"
            )
        # 收回所有尚未充电的车辆（保留正在充电的车）
        pool: list[ChargingRequest] = []
        for pile in self.pile_repo.all():
            for request_id in pile.queue:
                req = self.request_repo.get(request_id)
                if req:
                    pool.append(req)
            pile.queue = []
        pool += self.request_repo.waiting()
        pool.sort(key=lambda r: r.ticket_order)
        for req in pool:
            req.state = ST_WAITING_AREA
            req.pileId = None
        assignment, total = self._greedy_any_mode(pool)
        for req, pile in assignment:
            self._attach(req, pile)
        self.logger.log(f"批量最短总时长调度：{len(assignment)} 辆，累计完成 {total:.0f} 分钟")
        return {"dispatched": len(assignment), "totalFinishMinutes": round(total, 2)}

    def _optimal_assignment(self, cars, mode, exclude_id):
        """对少量车辆枚举到同模式各桩的最优分配，最小化完成总时长。"""
        piles = [p for p in self.pile_repo.by_mode(mode, exclude_id=exclude_id) if p.is_working]
        if not piles:
            return [], 0.0
        queue_len = self.rule_repo.get().chargingQueueLen
        order = sorted(cars, key=lambda r: r.ticket_order)
        best, best_total = None, float("inf")
        # 车辆数与桩数都很小，直接枚举「每辆车选哪个桩」的全部组合求最优
        for combo in itertools.product(range(len(piles)), repeat=len(order)):
            counts = {p.id: p.load() for p in piles}
            accum = {p.id: self.pile_finish_minutes(p, 0) for p in piles}
            total, ok, pairs = 0.0, True, []
            for car, pidx in zip(order, combo):
                pile = piles[pidx]
                counts[pile.id] += 1
                if counts[pile.id] > queue_len:
                    ok = False
                    break
                accum[pile.id] += car.charging_minutes(pile.power)
                total += accum[pile.id]
                pairs.append((car, pile))
            if ok and total < best_total:
                best_total, best = total, pairs
        if best is None:  # 容量不足，退化为逐辆最短桩贪心
            return self._greedy_any_mode(cars, restrict_mode=mode, exclude_id=exclude_id)
        return best, best_total

    def _greedy_any_mode(self, cars, restrict_mode=None, exclude_id=None):
        """贪心：按充电量从小到大，依次分配到「使该车完成时长最短」的可用桩。"""
        queue_len = self.rule_repo.get().chargingQueueLen
        piles = [
            p for p in self.pile_repo.all()
            if p.is_working and p.id != exclude_id
            and (restrict_mode is None or p.mode == restrict_mode)
        ]
        accum = {p.id: self.pile_finish_minutes(p, 0) for p in piles}
        counts = {p.id: p.load() for p in piles}
        assignment = []
        total = 0.0
        for req in sorted(cars, key=lambda r: r.amount):
            options = [p for p in piles if counts[p.id] < queue_len]
            if not options:
                break
            pile = min(options, key=lambda p: accum[p.id] + (req.amount / p.power) * 60)
            accum[pile.id] += (req.amount / pile.power) * 60
            counts[pile.id] += 1
            total += accum[pile.id]
            assignment.append((req, pile))
        return assignment, total


# ===========================================================================
# 充电服务（开始 / 结束充电、时间推进）
# ===========================================================================

class ChargingService:
    """充电服务。负责开始充电、结束充电（生成详单、累计充电桩统计）以及时间推进。"""

    def __init__(self, request_repo, pile_repo, rule_repo, clock, pricing, billing, dispatch, logger) -> None:
        self.request_repo = request_repo
        self.pile_repo = pile_repo
        self.rule_repo = rule_repo
        self.clock = clock
        self.pricing = pricing
        self.billing = billing
        self.dispatch = dispatch
        self.logger = logger

    def start_charging(self, req: ChargingRequest, pile: ChargingPile) -> dict:
        if not pile or not pile.is_working:
            raise ValueError("车辆还没有进入可用充电桩队列")
        if pile.activeRequestId:
            raise ValueError(f"{pile.id} 正在充电，请等待队首释放")
        if not pile.queue or pile.queue[0] != req.id:
            raise ValueError("只有队首车辆可以开始充电")
        pile.queue.pop(0)
        duration = max(1, round(req.charging_minutes(pile.power)))
        req.state = ST_CHARGING
        req.pileId = pile.id
        req.session = ChargeSession(
            pileId=pile.id,
            start=self.clock.now(),
            duration=duration,
            remainingMinutes=duration,
        )
        pile.activeRequestId = req.id
        self.logger.log(f"{req.ticket} 在 {pile.id} 开始充电")
        return {"result": 1, "session": req.session.to_dict()}

    def end_charging(self, req: ChargingRequest, end_time: int | None = None,
                     skip_dispatch: bool = False, reason: str = "用户结束") -> dict:
        if not req or req.state != ST_CHARGING or not req.session:
            raise ValueError("当前车辆未处于充电中")
        pile = self.pile_repo.get(req.session.pileId)
        end_time = int(end_time if end_time is not None else self.clock.now())
        start = int(req.session.start)
        elapsed = max(1, end_time - start)
        charged = min(float(req.amount), (elapsed / 60) * float(pile.power))
        fee = self.pricing.calculate_fee(start, end_time, charged, pile.power)
        req.state = ST_ENDED
        req.session.end = end_time
        req.session.charged = charged
        req.priority = False
        if pile.activeRequestId == req.id:
            pile.activeRequestId = None
            pile.record_completion(elapsed, charged, fee)
        detail = self.billing.create_detail(req, pile, start, end_time, charged, elapsed, fee, reason)
        if not skip_dispatch:
            self.dispatch.dispatch_waiting(req.mode)
        self.logger.log(f"{req.ticket} 结束充电，生成详单 {detail.detailId}")
        return {"result": 1, "detail": detail.to_dict()}

    def auto_start_ready(self) -> None:
        """对每个空闲（无车在充）且队首有车的工作桩，自动让队首车进入充电。

        模拟真实充电站「排到某可用桩队首即开始充电」的行为：当一个工作中的充电桩没有
        正在充电的车、而其排队队列又有车时，让队首车立即开始充电。供时间推进时驱动队列
        前进，避免「前车充满后、后车永远停在充电区不充电」的死锁。
        """
        for pile in self.pile_repo.all():
            if not pile.is_working or pile.activeRequestId or not pile.queue:
                continue
            head = self.request_repo.get(pile.queue[0])
            if head and head.state == ST_CHARGING_AREA:
                self.start_charging(head, pile)

    def advance(self, minutes: int) -> None:
        """推进系统时间（逐分钟）：让排到队首的车开始充电、递减在充会话的剩余时间，
        对到点的车自动结束并生成详单，期间持续叫号并启动后续车辆，使队列自动前进。"""
        if minutes <= 0:
            raise ValueError("推进时间必须为正数")
        # 推进前先让已排到队首的车从当前时刻开始充电（计费起点准确）
        self.auto_start_ready()
        for _ in range(int(minutes)):
            self.clock.advance(1)
            now = self.clock.now()
            for pile in self.pile_repo.all():
                req = self.request_repo.get(pile.activeRequestId)
                if not req or not req.session:
                    continue
                req.session.advance(1)
                if req.session.finished:
                    # 到点恰好是 start + duration，停止计费并出详单（暂不在循环内叫号）
                    self.end_charging(req, end_time=now, skip_dispatch=True,
                                      reason="达到请求电量")
            # 桩可能腾出空位：先把等候区车辆叫入充电区，再启动各空闲桩的队首车
            self.dispatch.dispatch_waiting()
            self.auto_start_ready()

    def charging_state(self, req: ChargingRequest) -> dict:
        if not req or req.state != ST_CHARGING or not req.session:
            raise ValueError("车辆未处于充电中")
        pile = self.pile_repo.get(req.session.pileId)
        start = int(req.session.start)
        now = self.clock.now()
        elapsed = max(0, now - start)
        charged = min(float(req.amount), (elapsed / 60) * float(pile.power))
        fee = self.pricing.calculate_fee(start, now, charged, pile.power)
        return {
            "detailId": None,
            "carId": req.carId,
            "pileId": pile.id,
            "chargeAmount": round(charged, 2),
            "chargeDuration": elapsed,
            "startTime": format_time(start),
            "endTime": format_time(now),
            **fee,
        }


# ===========================================================================
# 账户服务
# ===========================================================================

class AccountService:
    """账户服务。负责用户注册（创建账号）与口令验证（登录）。"""

    def __init__(self, account_repo, logger) -> None:
        self.account_repo = account_repo
        self.logger = logger

    def create_account(self, payload: dict) -> dict:
        car_id = str(payload.get("carId", "")).strip()
        user_name = str(payload.get("userName", "")).strip()
        capacity = float(payload.get("capacity", 0) or 0)
        if not car_id or not user_name or capacity <= 0:
            raise ValueError("车辆ID、用户名和电池容量不能为空")
        if self.account_repo.exists(car_id):
            raise ValueError("车辆账户已存在")
        account = UserAccount(car=EVCar(carId=car_id, capacity=capacity), userName=user_name)
        self.account_repo.add(account)
        self.logger.log(f"创建账号 {car_id}")
        return {"result": 1, "account": account.to_dict()}

    def set_password(self, payload: dict) -> dict:
        car_id = str(payload.get("carId", "")).strip()
        password = str(payload.get("password", ""))
        account = self.account_repo.get(car_id)
        if not account:
            raise ValueError("请先创建账号")
        if len(password) < 6:
            raise ValueError("密码至少 6 位")
        if account.credential and not account.verify(password):
            raise ValueError("密码验证失败")
        if not account.credential:
            account.set_password(password)
        account.active = True
        self.logger.log(f"账户 {car_id} 验证通过")
        return {"result": 1}

    def require_account(self, car_id: str) -> UserAccount:
        account = self.account_repo.get(car_id)
        if not account or not account.active:
            raise ValueError("车辆账户不存在或尚未登录验证")
        return account


# ===========================================================================
# 充电桩服务（启停 / 设置参数）与监控服务
# ===========================================================================

def build_piles(rule: ChargingRule) -> list[ChargingPile]:
    piles: list[ChargingPile] = []
    for i in range(1, int(rule.fastPileNum) + 1):
        piles.append(ChargingPile(id=f"F{i}", mode=FAST, power=float(rule.fastPower)))
    for i in range(1, int(rule.tricklePileNum) + 1):
        piles.append(ChargingPile(id=f"T{i}", mode=TRICKLE, power=float(rule.tricklePower)))
    return piles


class PileService:
    """充电桩服务。管理员启动/关闭充电桩、设置计费规则与运行参数。"""

    def __init__(self, pile_repo, rule_repo, request_repo, dispatch, logger) -> None:
        self.pile_repo = pile_repo
        self.rule_repo = rule_repo
        self.request_repo = request_repo
        self.dispatch = dispatch
        self.logger = logger

    def set_power(self, payload: dict) -> dict:
        pile_id = str(payload.get("pileId", "")).strip()
        powered = bool(payload.get("powered", True))
        pile = self.pile_repo.get(pile_id)
        if not pile:
            raise ValueError("充电桩不存在")
        if not powered and pile.activeRequestId:
            raise ValueError("正在充电的充电桩不能普通关闭，请走故障流程")
        stranded = []
        if not powered:
            for request_id in pile.queue:
                req = self.request_repo.get(request_id)
                if req:
                    req.state = ST_WAITING_AREA
                    req.pileId = None
                    req.priority = True
                    stranded.append(req)
            pile.queue = []
        pile.state = PILE_WORKING if powered else PILE_OFF
        self.logger.log(f"{pile_id} {'启动' if powered else '关闭'}")
        if stranded:
            self.dispatch.redispatch_specific(stranded, exclude_pile_id=pile_id)
        self.dispatch.dispatch_waiting(pile.mode)
        return {"result": 1}

    def start_pile(self, payload: dict) -> dict:
        return self.set_power({"pileId": payload.get("pileId"), "powered": True})

    def power_off(self, payload: dict) -> dict:
        return self.set_power({"pileId": payload.get("pileId"), "powered": False})

    def set_parameters(self, payload: dict) -> dict:
        """设置计费规则（三时段电价 + 服务费）。负值非法。"""
        rule = self.rule_repo.get()
        values = {
            "peak": float(payload.get("peak", rule.peak)),
            "normal": float(payload.get("normal", rule.normal)),
            "valley": float(payload.get("valley", rule.valley)),
            "service": float(payload.get("service", rule.service)),
        }
        if any(v < 0 for v in values.values()):
            raise ValueError("计费参数不能为负数")
        rule.peak, rule.normal, rule.valley, rule.service = (
            values["peak"], values["normal"], values["valley"], values["service"]
        )
        self.rule_repo.set(rule)
        self.logger.log("管理员更新计费参数")
        return {"result": 1, "rules": rule.to_dict()}


class PileMonitorService:
    """充电桩监控服务。提供「查看所有充电桩状态」与「查看等候服务车辆信息」。"""

    def __init__(self, pile_repo, request_repo, clock, queue_service) -> None:
        self.pile_repo = pile_repo
        self.request_repo = request_repo
        self.clock = clock
        self.queue_service = queue_service

    def query_pile_state(self, pile_id: str = "") -> dict:
        piles = [p for p in self.pile_repo.all() if not pile_id or p.id == pile_id]
        return {"piles": [p.to_dict() for p in piles]}

    def query_queue_state(self) -> dict:
        return {"queueRows": self.queue_service.queue_rows()}


# ===========================================================================
# 充电申请应用服务（UC_02 用例编排）
# ===========================================================================

class ChargingRequestService:
    """充电申请用例的应用服务（用例控制器在业务层的协作核心）。

    编排账户校验、排队号生成、叫号调度、开始/结束充电等子步骤，完成「提交/修改/取消
    充电请求、查看队列与充电状态、开始/结束充电」的完整用例场景。
    """

    def __init__(self, account_service, queue_service, dispatch, charging,
                 request_repo, pile_repo, rule_repo, clock, sequencer, logger) -> None:
        self.accounts = account_service
        self.queue = queue_service
        self.dispatch = dispatch
        self.charging = charging
        self.request_repo = request_repo
        self.pile_repo = pile_repo
        self.rule_repo = rule_repo
        self.clock = clock
        self.sequencer = sequencer
        self.logger = logger

    def submit_request(self, payload: dict) -> dict:
        car_id = str(payload.get("carId", "")).strip()
        amount = float(payload.get("amount", 0) or 0)
        mode = str(payload.get("mode", FAST))
        account = self.accounts.require_account(car_id)
        if mode not in (FAST, TRICKLE):
            raise ValueError("充电模式无效")
        if amount <= 0 or amount > float(account.capacity):
            raise ValueError("请求电量需大于 0 且不超过车辆电池容量")
        if self.request_repo.active_for_car(car_id):
            raise ValueError("当前车辆已有未完成请求")
        rule = self.rule_repo.get()
        if self.queue.waiting_count() >= rule.waitingAreaSize and not self.dispatch.best_pile(mode, amount):
            raise ValueError("等候区已满")
        req = ChargingRequest(
            id=self.sequencer.next_request_id(),
            carId=car_id,
            capacity=float(account.capacity),
            amount=amount,
            mode=mode,
            ticket=self.queue.make_ticket(mode),
            state=ST_WAITING_AREA,
            createdAt=self.clock.now(),
        )
        self.request_repo.add(req)
        self.logger.log(f"提交{MODE_NAMES[mode]}申请，排队号 {req.ticket}")
        self.dispatch.dispatch_waiting(mode)
        return self.car_state(car_id)

    def modify_amount(self, payload: dict) -> dict:
        car_id = str(payload.get("carId", "")).strip()
        amount = float(payload.get("amount", 0) or 0)
        req = self.request_repo.active_for_car(car_id)
        if not req:
            raise ValueError("没有可修改的充电请求")
        if req.state != ST_WAITING_AREA:
            raise ValueError("车辆进入充电区后不能修改电量")
        if amount <= 0 or amount > float(req.capacity):
            raise ValueError("请求电量需大于 0 且不超过车辆电池容量")
        req.amount = amount
        self.logger.log(f"{req.ticket} 修改请求电量为 {amount:g} 度")
        self.dispatch.dispatch_waiting(req.mode)
        return self.car_state(car_id)

    def modify_mode(self, payload: dict) -> dict:
        car_id = str(payload.get("carId", "")).strip()
        mode = str(payload.get("mode", FAST))
        req = self.request_repo.active_for_car(car_id)
        if not req:
            raise ValueError("没有可修改的充电请求")
        if req.state != ST_WAITING_AREA:
            raise ValueError("车辆进入充电区后不能修改模式")
        if mode not in (FAST, TRICKLE):
            raise ValueError("充电模式无效")
        req.mode = mode
        req.ticket = self.queue.make_ticket(mode)  # 重新生成排队号，排到对应模式队列最后
        req.createdAt = self.clock.now()
        req.priority = False
        self.logger.log(f"{car_id} 修改充电模式为{MODE_NAMES[mode]}，新排队号 {req.ticket}")
        self.dispatch.dispatch_waiting(mode)
        return self.car_state(car_id)

    def cancel_request(self, payload: dict) -> dict:
        car_id = str(payload.get("carId", "")).strip()
        req = self.request_repo.active_for_car(car_id)
        if not req:
            raise ValueError("没有可取消的充电请求")
        if req.state == ST_CHARGING:
            return self.charging.end_charging(req, reason="取消充电")
        if req.pileId:
            pile = self.pile_repo.get(req.pileId)
            if pile and req.id in pile.queue:
                pile.queue.remove(req.id)
        req.state = ST_CANCELLED
        req.pileId = None
        req.priority = False
        self.logger.log(f"{req.ticket} 取消充电")
        self.dispatch.dispatch_waiting(req.mode)
        return {"result": 1}

    def start_charging(self, payload: dict) -> dict:
        car_id = str(payload.get("carId", "")).strip()
        pile_id = str(payload.get("pileId", "")).strip()
        req = self.request_repo.active_for_car(car_id)
        if not req:
            raise ValueError("没有可开始的充电请求")
        pile = self.pile_repo.get(pile_id or req.pileId)
        return self.charging.start_charging(req, pile)

    def end_charging(self, payload: dict) -> dict:
        car_id = str(payload.get("carId", "")).strip()
        reason = str(payload.get("reason", "用户结束"))
        req = self.request_repo.active_for_car(car_id)
        return self.charging.end_charging(req, reason=reason)

    def car_state(self, car_id: str) -> dict:
        req = self.request_repo.active_for_car(car_id)
        if not req:
            return {"carId": car_id, "carState": "未入站", "queueNum": None, "beforeCount": 0, "requestTime": 0}
        return {
            "carId": car_id,
            "carPosition": req.pileId or ST_WAITING_AREA,
            "carState": STATE_NAMES.get(req.state, req.state),
            "queueNum": req.ticket,
            "beforeCount": self.queue.count_before(req),
            "requestTime": max(0, self.clock.now() - req.createdAt),
        }

    def charging_state(self, car_id: str) -> dict:
        return self.charging.charging_state(self.request_repo.active_for_car(car_id))


# ===========================================================================
# 故障恢复服务（委托策略对象完成故障重排）
# ===========================================================================

class FaultRecoveryService:
    """故障调度与恢复服务。单桩故障时停止计费、生成详单，并委托故障策略重排；

    故障恢复时把同类型其它桩中尚未充电的车按排队号重新调度，并将恢复桩纳入可选集合。
    """

    def __init__(self, pile_repo, request_repo, dispatch, charging, clock, logger) -> None:
        self.pile_repo = pile_repo
        self.request_repo = request_repo
        self.dispatch = dispatch
        self.charging = charging
        self.clock = clock
        self.logger = logger

    def handle_pile_fault(self, payload: dict) -> dict:
        pile_id = str(payload.get("pileId", "")).strip()
        strategy_name = str(payload.get("strategy", "PRIORITY"))
        pile = self.pile_repo.get(pile_id)
        if not pile:
            raise ValueError("充电桩不存在")
        if pile.state == PILE_FAULT:
            raise ValueError("充电桩已处于故障状态")
        pile.state = PILE_FAULT
        # 正在充电的车辆停止计费，本次充电对应一条详单
        if pile.activeRequestId:
            active = self.request_repo.get(pile.activeRequestId)
            if active:
                self.charging.end_charging(active, end_time=self.clock.now(),
                                           skip_dispatch=True, reason="充电桩故障停止计费")
        # 故障队列车辆退回等候态（暂停叫号），交由策略重排
        stranded = []
        for request_id in pile.queue:
            req = self.request_repo.get(request_id)
            if req:
                req.state = ST_WAITING_AREA
                req.pileId = None
                req.priority = True
                stranded.append(req)
        pile.queue = []
        strategy = get_fault_strategy(strategy_name)
        strategy.redispatch(pile, stranded, self.dispatch)
        # 故障队列全部重排完毕后，重新开启等候区叫号（需求 §7，与 recover_pile 对齐）
        self.dispatch.dispatch_waiting(pile.mode)
        self.logger.log(f"{pile_id} 故障，执行{strategy.label}调度")
        return {"result": 1, "strategy": strategy_name}

    def recover_pile(self, payload: dict) -> dict:
        pile_id = str(payload.get("pileId", "")).strip()
        pile = self.pile_repo.get(pile_id)
        if not pile:
            raise ValueError("充电桩不存在")
        pile.state = PILE_WORKING
        queued = self.dispatch.collect_queued_by_mode(pile.mode)
        queued.sort(key=lambda r: r.ticket_order)
        self.dispatch.redispatch_specific(queued)
        self.dispatch.dispatch_waiting(pile.mode)
        self.logger.log(f"{pile_id} 故障恢复，按排队号重新调度")
        return {"result": 1}
