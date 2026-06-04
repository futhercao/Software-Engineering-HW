"""应用门面（Application Facade）：ChargingStationSystem。

负责按依赖关系装配领域层、持久化层、业务层与控制器层（依赖注入），对外提供：
  · 线程安全的命令执行（``RLock``，避免 ThreadingHTTPServer 并发改状态的竞态）；
  · 状态的 JSON 持久化（``load`` / ``save``）；
  · 供前端渲染的聚合视图 ``public_state``；
  · 系统级动作（重置、时间推进、手动叫号、演示数据、清空日志）。

控制器、服务、仓储均作为该门面的成员对象，HTTP 层只与本门面及其控制器交互。
"""

from __future__ import annotations

import json
import threading
from copy import deepcopy
from pathlib import Path

from . import domain
from .controllers import (
    AccountController,
    BillingController,
    ChargingRequestController,
    FaultDispatchController,
    MonitorController,
    PileAdminController,
)
from .domain import (
    ChargingRule,
    EVCar,
    MODE_NAMES,
    STATE_NAMES,
    ST_CHARGING,
    ST_WAITING_AREA,
    UserAccount,
    format_time,
    hash_password,
    verify_password,
)
from .repositories import (
    AccountRepository,
    BillRepository,
    DetailedListRepository,
    PileRepository,
    RequestRepository,
    RuleRepository,
)
from .services import (
    AccountService,
    BillingService,
    ChargingRequestService,
    ChargingService,
    DispatchService,
    FaultRecoveryService,
    PileMonitorService,
    PileService,
    PricingService,
    QueueService,
    build_piles,
)
from .support import Clock, EventLog, Sequencer

DEFAULT_STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "state.json"

# 演示用开箱账户（用户端可直接登录；车辆 ID 刻意避开单元测试用例占用的 ID）
DEMO_CAR = "京A-88888"
DEMO_PASSWORD = "123456"

# 管理员登录凭据：工号 -> 口令。课程演示用，可按需扩充。
# （需求只设「用户 / 管理员」两类客户端，注册与账单查询均归用户端。）
STAFF_ACCOUNTS = {
    "admin": {"admin": "admin123456"},
}
ROLE_LABELS = {"user": "用户", "admin": "管理员"}


class ChargingStationSystem:
    def __init__(self, state_file: Path | str = DEFAULT_STATE_FILE) -> None:
        self.state_file = Path(state_file)
        self.lock = threading.RLock()

        # ---- 支撑设施 ----
        self.clock = Clock()
        self.sequencer = Sequencer()
        self.logger = EventLog(self.clock)

        # ---- 持久化层（仓储） ----
        self.account_repo = AccountRepository()
        self.request_repo = RequestRepository()
        self.pile_repo = PileRepository()
        self.bill_repo = BillRepository()
        self.detail_repo = DetailedListRepository()
        self.rule_repo = RuleRepository()

        # ---- 业务/应用层（服务） ----
        self.pricing = PricingService(self.rule_repo)
        self.billing = BillingService(self.bill_repo, self.detail_repo, self.sequencer)
        self.queue = QueueService(self.sequencer, self.request_repo, self.pile_repo, self.clock, self.rule_repo)
        self.dispatch = DispatchService(self.request_repo, self.pile_repo, self.rule_repo, self.clock, self.logger)
        self.charging = ChargingService(self.request_repo, self.pile_repo, self.rule_repo, self.clock,
                                        self.pricing, self.billing, self.dispatch, self.logger)
        self.account_service = AccountService(self.account_repo, self.logger)
        self.pile_service = PileService(self.pile_repo, self.rule_repo, self.request_repo, self.dispatch, self.logger)
        self.monitor_service = PileMonitorService(self.pile_repo, self.request_repo, self.clock, self.queue)
        self.request_service = ChargingRequestService(
            self.account_service, self.queue, self.dispatch, self.charging,
            self.request_repo, self.pile_repo, self.rule_repo, self.clock, self.sequencer, self.logger,
        )
        self.fault_service = FaultRecoveryService(
            self.pile_repo, self.request_repo, self.dispatch, self.charging, self.clock, self.logger
        )

        # ---- 控制器层 ----
        self.account_controller = AccountController(self.account_service)
        self.request_controller = ChargingRequestController(self.request_service)
        self.billing_controller = BillingController(self.billing)
        self.pile_admin_controller = PileAdminController(self.pile_service)
        self.monitor_controller = MonitorController(self.monitor_service)
        self.fault_controller = FaultDispatchController(self.fault_service, self.dispatch)

        self._load()

    # ------------------------------------------------------------------ #
    # 持久化
    # ------------------------------------------------------------------ #
    def _load(self) -> None:
        if not self.state_file.exists():
            self._init_default()
            return
        try:
            with self.state_file.open("r", encoding="utf-8") as fp:
                state = json.load(fp)
        except (json.JSONDecodeError, OSError):
            self._init_default()
            return
        self.clock.set(state.get("clock", 8 * 60))
        self.sequencer.load(state.get("seq", {}))
        self.logger.load(state.get("logs", []))
        self.rule_repo.load(state.get("rule", {}))
        self.account_repo.load(state.get("accounts", {}))
        self.request_repo.load(state.get("requests", []))
        self.pile_repo.load(state.get("piles", []))
        self.bill_repo.load(state.get("bills", []))
        self.detail_repo.load(state.get("details", []))
        if not self.pile_repo.all():
            self.pile_repo.replace_all(build_piles(self.rule_repo.get()))

    def _init_default(self) -> None:
        # 原地重置支撑设施与各仓储，保持已注入到各服务的对象引用不变
        self.clock.set(8 * 60)
        self.sequencer.load({"request": 1, "detail": 1, "ticketFAST": 1, "ticketTRICKLE": 1})
        self.logger.clear()
        self.rule_repo.set(ChargingRule())
        self.account_repo.load({})
        self.request_repo.load([])
        self.bill_repo.load([])
        self.detail_repo.load([])
        self.pile_repo.replace_all(build_piles(self.rule_repo.get()))
        # 种子：一个已验证的演示账户，便于用户端开箱登录（静默，不写事件日志）
        self.account_repo.add(UserAccount(
            car=EVCar(carId=DEMO_CAR, capacity=60.0),
            userName="演示用户",
            active=True,
            credential=hash_password(DEMO_PASSWORD),
        ))

    def save(self) -> None:
        state = {
            "clock": self.clock.now(),
            "seq": self.sequencer.dump(),
            "logs": self.logger.dump(),
            "rule": self.rule_repo.dump(),
            "accounts": self.account_repo.dump(),
            "requests": self.request_repo.dump(),
            "piles": self.pile_repo.dump(),
            "bills": self.bill_repo.dump(),
            "details": self.detail_repo.dump(),
        }
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as fp:
            json.dump(state, fp, ensure_ascii=False, indent=2)
        tmp.replace(self.state_file)

    # ------------------------------------------------------------------ #
    # 登录鉴权（区分用户 / 管理员两类客户端）
    # ------------------------------------------------------------------ #
    def login(self, payload: dict) -> dict:
        """统一登录入口。

        · 用户：车辆 ID + 口令，复用账户体系的 ``verify_password``；
        · 管理员：工号 + 预置口令（``STAFF_ACCOUNTS``）。
        校验失败抛 ``ValueError``，由 HTTP 层转 400。
        """
        role = str(payload.get("role", "")).strip()
        if role == "user":
            car_id = str(payload.get("carId", "")).strip()
            password = str(payload.get("password", ""))
            account = self.account_repo.get(car_id)
            if account is None or not account.active or not verify_password(password, account.credential):
                raise ValueError("车辆 ID 或密码错误，或账户尚未开通")
            self.logger.log(f"用户 {car_id} 登录")
            return {"role": "user", "carId": car_id, "userName": account.userName}
        if role == "admin":
            username = str(payload.get("username", "")).strip()
            password = str(payload.get("password", ""))
            if STAFF_ACCOUNTS[role].get(username) != password:
                raise ValueError("工号或密码错误")
            self.logger.log(f"{ROLE_LABELS[role]} {username} 登录")
            return {"role": role, "username": username}
        raise ValueError("未知的登录角色")

    # ------------------------------------------------------------------ #
    # 系统级动作
    # ------------------------------------------------------------------ #
    def reset(self) -> dict:
        self._init_default()
        self.logger.log("系统重置")
        self.save()
        return {"result": 1}

    def tick(self, payload: dict) -> dict:
        self.charging.advance(int(payload.get("minutes", 15)))
        return {"result": 1}

    def manual_dispatch(self) -> dict:
        self.dispatch.dispatch_waiting()
        return {"result": 1}

    def clear_logs(self) -> dict:
        self.logger.clear()
        return {"result": 1}

    def add_demo(self) -> dict:
        samples = [
            ("京A-EV102", domain.FAST, 28, 70),
            ("京A-EV205", domain.FAST, 35, 75),
            ("京A-EV309", domain.TRICKLE, 18, 50),
            ("京A-EV417", domain.TRICKLE, 22, 55),
            ("京A-EV520", domain.TRICKLE, 16, 45),
        ]
        for car_id, mode, amount, capacity in samples:
            if not self.account_repo.exists(car_id):
                self.account_service.create_account({"carId": car_id, "userName": car_id, "capacity": capacity})
                self.account_service.set_password({"carId": car_id, "password": "123456"})
            if not self.request_repo.active_for_car(car_id):
                self.request_service.submit_request({"carId": car_id, "amount": amount, "mode": mode})
        self.logger.log("加入一组测试车辆")
        return {"result": 1}

    # ------------------------------------------------------------------ #
    # 聚合视图（供前端渲染）
    # ------------------------------------------------------------------ #
    def public_state(self) -> dict:
        rule = self.rule_repo.get()
        now = self.clock.now()
        accounts = {
            acc.carId: {
                "carId": acc.carId,
                "userName": acc.userName,
                "capacity": acc.capacity,
                "active": acc.active,
            }
            for acc in self.account_repo.all()
        }
        requests = []
        for req in self.request_repo.all():
            data = req.to_dict()
            data["stateText"] = STATE_NAMES.get(req.state, req.state)
            data["modeText"] = MODE_NAMES.get(req.mode, req.mode)
            data["waitMinutes"] = max(0, now - req.createdAt)
            data["beforeCount"] = self.queue.count_before(req)
            if req.state == ST_CHARGING and req.session:
                pile = self.pile_repo.get(req.session.pileId)
                power = pile.power if pile else rule.power_of(req.mode)
                elapsed = max(0, now - req.session.start)
                charged = min(float(req.amount), (elapsed / 60) * float(power))
                fee = self.pricing.calculate_fee(req.session.start, now, charged, power)
                data["live"] = {
                    "elapsed": elapsed,
                    "charged": round(charged, 2),
                    "fee": fee,
                    "remainingMinutes": max(0, req.session.remainingMinutes),
                }
            requests.append(data)
        return {
            "simTimeText": format_time(now),
            "rules": rule.to_dict(),
            "config": rule.to_dict(),
            "accounts": accounts,
            "requests": requests,
            "piles": [p.to_dict() for p in self.pile_repo.all()],
            "waitingCount": self.queue.waiting_count(),
            "queueRows": self.queue.queue_rows(),
            "reports": self.billing.report("day"),
            "bills": [b.to_dict() for b in self.bill_repo.all()],
            "details": [d.to_dict() for d in self.detail_repo.all()],
            "logs": self.logger.entries(),
        }
