"""持久化层（Persistence Layer）——仓储（Repository）。

按照课程「持久化层对象设计原则」，仓储类的职责是「管理（增删改查）业务对象，并与
存储介质保持同步」，从而把数据访问与业务逻辑解耦。业务/应用层只依赖仓储暴露的查询
方法，不关心底层存储形式（此处为内存集合 + JSON 文件快照，未来可替换为数据库而不
影响上层）。

每个仓储都提供 ``dump()`` / ``load()`` 用于把自身管理的实体集合序列化到状态快照、
或从快照恢复，序列化与反序列化的细节委托给各实体的 ``to_dict`` / ``from_dict``。
"""

from __future__ import annotations

from .domain import (
    Bill,
    ChargingPile,
    ChargingRequest,
    ChargingRule,
    DetailedList,
    UserAccount,
    ST_WAITING_AREA,
)


class AccountRepository:
    """用户账户仓储，按车辆 ID 主键管理账户。"""

    def __init__(self) -> None:
        self._accounts: dict[str, UserAccount] = {}

    def add(self, account: UserAccount) -> None:
        self._accounts[account.carId] = account

    def get(self, car_id: str) -> UserAccount | None:
        return self._accounts.get(car_id)

    def exists(self, car_id: str) -> bool:
        return car_id in self._accounts

    def all(self) -> list[UserAccount]:
        return list(self._accounts.values())

    def dump(self) -> dict:
        return {cid: acc.to_dict() for cid, acc in self._accounts.items()}

    def load(self, data: dict) -> None:
        self._accounts = {
            cid: UserAccount.from_dict(d) for cid, d in (data or {}).items()
        }


class RequestRepository:
    """充电请求仓储，维护全部充电请求并提供按车辆 / 状态 / 模式的查询。"""

    def __init__(self) -> None:
        self._requests: list[ChargingRequest] = []

    def add(self, request: ChargingRequest) -> None:
        self._requests.append(request)

    def get(self, request_id: str | None) -> ChargingRequest | None:
        if not request_id:
            return None
        return next((r for r in self._requests if r.id == request_id), None)

    def all(self) -> list[ChargingRequest]:
        return list(self._requests)

    def active_for_car(self, car_id: str) -> ChargingRequest | None:
        return next(
            (r for r in self._requests if r.carId == car_id and r.is_active), None
        )

    def waiting(self, mode: str | None = None) -> list[ChargingRequest]:
        return [
            r
            for r in self._requests
            if r.state == ST_WAITING_AREA and (mode is None or r.mode == mode)
        ]

    def dump(self) -> list:
        return [r.to_dict() for r in self._requests]

    def load(self, data: list) -> None:
        self._requests = [ChargingRequest.from_dict(d) for d in (data or [])]


class PileRepository:
    """充电桩仓储，维护全部充电桩并提供按模式查询。"""

    def __init__(self) -> None:
        self._piles: list[ChargingPile] = []

    def get(self, pile_id: str | None) -> ChargingPile | None:
        if not pile_id:
            return None
        return next((p for p in self._piles if p.id == pile_id), None)

    def all(self) -> list[ChargingPile]:
        return list(self._piles)

    def by_mode(self, mode: str, exclude_id: str | None = None) -> list[ChargingPile]:
        return [p for p in self._piles if p.mode == mode and p.id != exclude_id]

    def replace_all(self, piles: list[ChargingPile]) -> None:
        self._piles = list(piles)

    def dump(self) -> list:
        return [p.to_dict() for p in self._piles]

    def load(self, data: list) -> None:
        self._piles = [ChargingPile.from_dict(d) for d in (data or [])]


class DetailedListRepository:
    """详单仓储，追加保存每一次充电过程产生的详单。"""

    def __init__(self) -> None:
        self._details: list[DetailedList] = []

    def add(self, detail: DetailedList) -> None:
        self._details.append(detail)

    def all(self) -> list[DetailedList]:
        return list(self._details)

    def query(self, car_id: str = "", bill_id: str = "") -> list[DetailedList]:
        return [
            d
            for d in self._details
            if (not car_id or d.carId == car_id) and (not bill_id or d.billId == bill_id)
        ]

    def dump(self) -> list:
        return [d.to_dict() for d in self._details]

    def load(self, data: list) -> None:
        self._details = [DetailedList.from_dict(d) for d in (data or [])]


class BillRepository:
    """账单仓储，按 (billId, carId) 维护按日汇总账单。"""

    def __init__(self) -> None:
        self._bills: list[Bill] = []

    def add(self, bill: Bill) -> None:
        self._bills.append(bill)

    def find(self, bill_id: str, car_id: str) -> Bill | None:
        return next(
            (b for b in self._bills if b.billId == bill_id and b.carId == car_id), None
        )

    def all(self) -> list[Bill]:
        return list(self._bills)

    def query(self, car_id: str = "", day: str = "") -> list[Bill]:
        return [
            b
            for b in self._bills
            if (not car_id or b.carId == car_id) and (not day or b.date == day)
        ]

    def dump(self) -> list:
        return [b.to_dict() for b in self._bills]

    def load(self, data: list) -> None:
        self._bills = [Bill.from_dict(d) for d in (data or [])]


class RuleRepository:
    """计费规则与系统配置参数仓储（单例语义）。"""

    def __init__(self) -> None:
        self._rule = ChargingRule()

    def get(self) -> ChargingRule:
        return self._rule

    def set(self, rule: ChargingRule) -> None:
        self._rule = rule

    def dump(self) -> dict:
        return self._rule.to_dict()

    def load(self, data: dict) -> None:
        self._rule = ChargingRule.from_dict(data or {})
