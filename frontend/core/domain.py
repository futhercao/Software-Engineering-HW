"""领域层（Domain Layer）。

按照课程「面向对象设计」中的领域模型与 GRASP「信息专家」原则，本模块定义
充电桩调度计费系统的领域实体。每个实体既持有自身私有数据（了解型职责），也封装
只依赖自身数据即可完成的计算（行为型职责），尽量做到「自己的事自己干」，从而提高
内聚、降低与业务服务层的耦合。

实体之间的协作（叫号、调度、计费、故障重排等跨实体逻辑）放在业务/应用层（services）
中实现，本层不直接依赖任何持久化或网络设施。
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# 常量与枚举（用字符串常量，便于直接序列化为前端可读的 JSON）
# ---------------------------------------------------------------------------

BASE_DATE = date(2026, 6, 3)

FAST = "FAST"
TRICKLE = "TRICKLE"

MODE_NAMES = {FAST: "快充", TRICKLE: "慢充"}

# 充电桩工作状态
PILE_WORKING = "WORKING"
PILE_OFF = "OFF"
PILE_FAULT = "FAULT"

# 充电请求生命周期状态
ST_WAITING_AREA = "WAITING_AREA"   # 在等候区排队
ST_CHARGING_AREA = "CHARGING_AREA"  # 已叫号进入某充电桩排队队列
ST_CHARGING = "CHARGING"            # 正在充电
ST_ENDED = "ENDED"                  # 已结束（生成详单）
ST_CANCELLED = "CANCELLED"          # 已取消

STATE_NAMES = {
    ST_WAITING_AREA: "等候区",
    ST_CHARGING_AREA: "充电区排队",
    ST_CHARGING: "充电中",
    ST_ENDED: "已结束",
    ST_CANCELLED: "已取消",
}

ACTIVE_STATES = {ST_WAITING_AREA, ST_CHARGING_AREA, ST_CHARGING}


# ---------------------------------------------------------------------------
# 通用工具：口令散列、时间换算
# ---------------------------------------------------------------------------

def hash_password(password: str, salt: str | None = None) -> dict:
    """对口令加盐做 SHA-256 散列，返回 {salt, hash}。"""
    salt = salt or uuid.uuid4().hex
    digest = hashlib.sha256(f"{salt}:{password}".encode("utf-8")).hexdigest()
    return {"salt": salt, "hash": digest}


def verify_password(password: str, credential: dict | None) -> bool:
    if not credential:
        return False
    return hash_password(password, credential["salt"])["hash"] == credential.get("hash")


def format_time(minutes: int) -> str:
    """把「系统启动后的分钟数」格式化为 'YYYY-MM-DD HH:MM'。"""
    minutes = int(minutes)
    day, minute_of_day = divmod(minutes, 24 * 60)
    h, m = divmod(minute_of_day, 60)
    current_date = BASE_DATE + timedelta(days=day)
    return f"{current_date.isoformat()} {h:02d}:{m:02d}"


def date_for_minutes(minutes: int) -> str:
    return (BASE_DATE + timedelta(days=int(minutes) // (24 * 60))).isoformat()


def ticket_prefix(mode: str) -> str:
    return "F" if mode == FAST else "T"


# ---------------------------------------------------------------------------
# 领域实体
# ---------------------------------------------------------------------------

@dataclass
class QueueTicket:
    """排队号码。快充以 F 开头、慢充以 T 开头，号内编号从 1 开始递增。"""

    prefix: str
    number: int

    def __str__(self) -> str:
        return f"{self.prefix}{self.number}"

    @property
    def order(self) -> int:
        """用于「按排队号码先后顺序」排序的可比较键。"""
        base = 0 if self.prefix == "F" else 100_000
        return base + self.number

    @classmethod
    def parse(cls, text: str) -> "QueueTicket":
        text = str(text)
        return cls(prefix=text[:1] or "F", number=int(text[1:] or 0))

    @staticmethod
    def order_of(text: str) -> int:
        return QueueTicket.parse(text).order


@dataclass
class EVCar:
    """电动车，了解自己的电池总容量（度）。"""

    carId: str
    capacity: float

    def to_dict(self) -> dict:
        return {"carId": self.carId, "capacity": self.capacity}


@dataclass
class UserAccount:
    """用户账户，聚合一辆电动车，了解自身的登录凭据与验证状态。"""

    car: EVCar
    userName: str
    active: bool = False
    credential: dict | None = None

    @property
    def carId(self) -> str:
        return self.car.carId

    @property
    def capacity(self) -> float:
        return self.car.capacity

    def set_password(self, password: str) -> None:
        self.credential = hash_password(password)

    def verify(self, password: str) -> bool:
        return verify_password(password, self.credential)

    def to_dict(self) -> dict:
        return {
            "carId": self.carId,
            "userName": self.userName,
            "capacity": self.capacity,
            "active": self.active,
            "credential": self.credential,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UserAccount":
        return cls(
            car=EVCar(carId=data["carId"], capacity=float(data["capacity"])),
            userName=data.get("userName", data["carId"]),
            active=bool(data.get("active", False)),
            credential=data.get("credential"),
        )


@dataclass
class ChargeSession:
    """一次充电过程，了解开始时刻、预计时长与剩余时间。"""

    pileId: str
    start: int
    duration: int
    remainingMinutes: int
    charged: float | None = None
    end: int | None = None

    def advance(self, minutes: int) -> None:
        self.remainingMinutes = max(0, int(self.remainingMinutes) - int(minutes))

    @property
    def finished(self) -> bool:
        return self.remainingMinutes <= 0

    def to_dict(self) -> dict:
        return {
            "pileId": self.pileId,
            "start": self.start,
            "duration": self.duration,
            "remainingMinutes": self.remainingMinutes,
            "charged": self.charged,
            "end": self.end,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ChargeSession":
        return cls(
            pileId=data["pileId"],
            start=int(data["start"]),
            duration=int(data["duration"]),
            remainingMinutes=int(data["remainingMinutes"]),
            charged=data.get("charged"),
            end=data.get("end"),
        )


@dataclass
class ChargingRequest:
    """充电请求（领域核心实体）。了解自己的充电量、模式、排队号、状态与所属充电桩。"""

    id: str
    carId: str
    capacity: float
    amount: float
    mode: str
    ticket: str
    state: str
    createdAt: int
    pileId: str | None = None
    priority: bool = False
    session: ChargeSession | None = None

    @property
    def ticket_order(self) -> int:
        return QueueTicket.order_of(self.ticket)

    def charging_minutes(self, power: float) -> float:
        """自己充电时间（分钟）= 请求充电量 / 充电桩功率。"""
        return (float(self.amount) / float(power)) * 60

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_STATES

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "carId": self.carId,
            "capacity": self.capacity,
            "amount": self.amount,
            "mode": self.mode,
            "ticket": self.ticket,
            "state": self.state,
            "createdAt": self.createdAt,
            "pileId": self.pileId,
            "priority": self.priority,
            "session": self.session.to_dict() if self.session else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ChargingRequest":
        session = data.get("session")
        return cls(
            id=data["id"],
            carId=data["carId"],
            capacity=float(data["capacity"]),
            amount=float(data["amount"]),
            mode=data["mode"],
            ticket=data["ticket"],
            state=data["state"],
            createdAt=int(data["createdAt"]),
            pileId=data.get("pileId"),
            priority=bool(data.get("priority", False)),
            session=ChargeSession.from_dict(session) if session else None,
        )


@dataclass
class ChargingPile:
    """充电桩。了解自身功率、工作状态、当前充电车辆与排队队列，并能计算自身负载。"""

    id: str
    mode: str
    power: float
    state: str = PILE_WORKING
    queue: list[str] = field(default_factory=list)        # 排队（未充电）请求 id，按入队顺序
    activeRequestId: str | None = None                    # 队首正在充电的请求 id
    totalChargeNum: int = 0
    totalChargeTime: int = 0
    totalCapacity: float = 0.0
    totalChargeFee: float = 0.0
    totalServiceFee: float = 0.0
    totalFee: float = 0.0

    @property
    def is_working(self) -> bool:
        return self.state == PILE_WORKING

    def load(self) -> int:
        """当前占用车位数 = 排队车辆数 + 正在充电的 1 辆。"""
        return len(self.queue) + (1 if self.activeRequestId else 0)

    def has_slot(self, queue_len: int) -> bool:
        return self.is_working and self.load() < int(queue_len)

    def record_completion(self, elapsed: int, charged: float, fee: dict) -> None:
        """累计一次完成充电的统计数据（充电桩监控所需）。"""
        self.totalChargeNum += 1
        self.totalChargeTime += int(elapsed)
        self.totalCapacity += float(charged)
        self.totalChargeFee += fee["chargeFee"]
        self.totalServiceFee += fee["serviceFee"]
        self.totalFee += fee["totalFee"]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "mode": self.mode,
            "power": self.power,
            "state": self.state,
            "queue": list(self.queue),
            "activeRequestId": self.activeRequestId,
            "totalChargeNum": self.totalChargeNum,
            "totalChargeTime": self.totalChargeTime,
            "totalCapacity": round(self.totalCapacity, 4),
            "totalChargeFee": round(self.totalChargeFee, 4),
            "totalServiceFee": round(self.totalServiceFee, 4),
            "totalFee": round(self.totalFee, 4),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ChargingPile":
        return cls(
            id=data["id"],
            mode=data["mode"],
            power=float(data["power"]),
            state=data.get("state", PILE_WORKING),
            queue=list(data.get("queue", [])),
            activeRequestId=data.get("activeRequestId"),
            totalChargeNum=int(data.get("totalChargeNum", 0)),
            totalChargeTime=int(data.get("totalChargeTime", 0)),
            totalCapacity=float(data.get("totalCapacity", 0.0)),
            totalChargeFee=float(data.get("totalChargeFee", 0.0)),
            totalServiceFee=float(data.get("totalServiceFee", 0.0)),
            totalFee=float(data.get("totalFee", 0.0)),
        )


@dataclass
class ChargingRule:
    """计费规则。封装三时段电价、服务费单价与充电桩配置参数，并能判定某时刻的单位电价。"""

    peak: float = 1.0
    normal: float = 0.7
    valley: float = 0.4
    service: float = 0.8
    waitingAreaSize: int = 8
    chargingQueueLen: int = 3
    fastPileNum: int = 2
    tricklePileNum: int = 3
    fastPower: float = 30.0
    tricklePower: float = 10.0

    def price_at(self, minutes: int) -> float:
        """按需求规定的时间段返回单位电价（峰 / 平 / 谷）。"""
        hour = (int(minutes) % (24 * 60)) // 60
        if (10 <= hour < 15) or (18 <= hour < 21):
            return float(self.peak)
        if (7 <= hour < 10) or (15 <= hour < 18) or (21 <= hour < 23):
            return float(self.normal)
        return float(self.valley)

    def power_of(self, mode: str) -> float:
        return float(self.fastPower if mode == FAST else self.tricklePower)

    def to_dict(self) -> dict:
        return {
            "peak": self.peak,
            "normal": self.normal,
            "valley": self.valley,
            "service": self.service,
            "waitingAreaSize": self.waitingAreaSize,
            "chargingQueueLen": self.chargingQueueLen,
            "fastPileNum": self.fastPileNum,
            "tricklePileNum": self.tricklePileNum,
            "fastPower": self.fastPower,
            "tricklePower": self.tricklePower,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ChargingRule":
        merged = cls().to_dict()
        merged.update({k: v for k, v in (data or {}).items() if k in merged})
        return cls(
            peak=float(merged["peak"]),
            normal=float(merged["normal"]),
            valley=float(merged["valley"]),
            service=float(merged["service"]),
            waitingAreaSize=int(merged["waitingAreaSize"]),
            chargingQueueLen=int(merged["chargingQueueLen"]),
            fastPileNum=int(merged["fastPileNum"]),
            tricklePileNum=int(merged["tricklePileNum"]),
            fastPower=float(merged["fastPower"]),
            tricklePower=float(merged["tricklePower"]),
        )


@dataclass
class DetailedList:
    """详单（一次充电过程对应一条），了解本次充电的全部计费明细。"""

    detailId: str
    billId: str
    carId: str
    date: str
    pileId: str
    chargeAmount: float
    chargeDuration: int
    startTime: str
    endTime: str
    chargeFee: float
    serviceFee: float
    totalFee: float
    generatedAt: str
    reason: str = "用户结束"

    def to_dict(self) -> dict:
        return {
            "detailId": self.detailId,
            "billId": self.billId,
            "carId": self.carId,
            "date": self.date,
            "pileId": self.pileId,
            "chargeAmount": round(self.chargeAmount, 4),
            "chargeDuration": self.chargeDuration,
            "startTime": self.startTime,
            "endTime": self.endTime,
            "chargeFee": self.chargeFee,
            "serviceFee": self.serviceFee,
            "totalFee": self.totalFee,
            "generatedAt": self.generatedAt,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DetailedList":
        return cls(
            detailId=data["detailId"],
            billId=data["billId"],
            carId=data["carId"],
            date=data["date"],
            pileId=data["pileId"],
            chargeAmount=float(data["chargeAmount"]),
            chargeDuration=int(data["chargeDuration"]),
            startTime=data["startTime"],
            endTime=data["endTime"],
            chargeFee=float(data["chargeFee"]),
            serviceFee=float(data["serviceFee"]),
            totalFee=float(data["totalFee"]),
            generatedAt=data.get("generatedAt", data.get("endTime", "")),
            reason=data.get("reason", "用户结束"),
        )


@dataclass
class Bill:
    """账单（按车辆按日汇总多条详单）。"""

    billId: str
    carId: str
    date: str
    chargeAmount: float = 0.0
    chargeDuration: int = 0
    chargeFee: float = 0.0
    serviceFee: float = 0.0
    totalFee: float = 0.0

    def add_detail(self, detail: DetailedList) -> None:
        self.chargeAmount = round(self.chargeAmount + detail.chargeAmount, 2)
        self.chargeDuration += detail.chargeDuration
        self.chargeFee = round(self.chargeFee + detail.chargeFee, 2)
        self.serviceFee = round(self.serviceFee + detail.serviceFee, 2)
        self.totalFee = round(self.totalFee + detail.totalFee, 2)

    def to_dict(self) -> dict:
        return {
            "billId": self.billId,
            "carId": self.carId,
            "date": self.date,
            "chargeAmount": round(self.chargeAmount, 2),
            "chargeDuration": self.chargeDuration,
            "chargeFee": self.chargeFee,
            "serviceFee": self.serviceFee,
            "totalFee": self.totalFee,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Bill":
        return cls(
            billId=data["billId"],
            carId=data["carId"],
            date=data["date"],
            chargeAmount=float(data.get("chargeAmount", 0.0)),
            chargeDuration=int(data.get("chargeDuration", 0)),
            chargeFee=float(data.get("chargeFee", 0.0)),
            serviceFee=float(data.get("serviceFee", 0.0)),
            totalFee=float(data.get("totalFee", 0.0)),
        )
