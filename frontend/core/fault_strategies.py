"""故障调度策略（Strategy 模式）。

需求规定单一充电桩故障后，系统要能在「优先级调度」与「时间顺序调度」之间切换（验收时
随机选择启用哪一种）。这正是 GoF 策略模式的典型场景：把「会变化的算法族」抽象为统一
接口 ``FaultStrategy``，两种调度各自实现，``FaultRecoveryService`` 在运行时根据管理员
选择委托给对应策略——符合开闭原则（新增调度策略无需修改恢复服务）。

两种策略都遵循同一前置约束：调用前已暂停等候区叫号、故障桩正在充电的车已停止计费并
生成详单、故障队列中的车已全部退回等候态（``stranded``）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .domain import ChargingPile, ChargingRequest


class FaultStrategy(ABC):
    """故障重调度策略的抽象基类，定义统一的重排接口。"""

    #: 策略中文名，用于日志与说明
    label: str = "故障"

    @abstractmethod
    def redispatch(
        self,
        faulted_pile: ChargingPile,
        stranded: list[ChargingRequest],
        dispatch_service,
    ) -> None:
        """对故障引发的待调度车辆执行重排。"""


class PriorityFaultStrategy(FaultStrategy):
    """优先级调度：优先把故障充电桩等候队列中的车辆调度到其它同类型桩空位。

    等候区叫号在此期间保持暂停，待故障队列全部调度完毕后再由上层恢复叫号。
    """

    label = "优先级"

    def redispatch(self, faulted_pile, stranded, dispatch_service) -> None:
        ordered = sorted(stranded, key=lambda req: req.ticket_order)
        dispatch_service.redispatch_specific(ordered, exclude_pile_id=faulted_pile.id)


class TimeOrderFaultStrategy(FaultStrategy):
    """时间顺序调度：将故障队列车辆与其它同类型桩中尚未充电的车辆合为一组，

    按排队号码先后顺序统一重新调度到同类型各桩。
    """

    label = "时间顺序"

    def redispatch(self, faulted_pile, stranded, dispatch_service) -> None:
        others = dispatch_service.collect_queued_by_mode(
            faulted_pile.mode, exclude_pile_id=faulted_pile.id
        )
        merged = sorted(stranded + others, key=lambda req: req.ticket_order)
        dispatch_service.redispatch_specific(merged, exclude_pile_id=faulted_pile.id)


#: 策略注册表，供恢复服务按管理员选择查找。
FAULT_STRATEGIES: dict[str, FaultStrategy] = {
    "PRIORITY": PriorityFaultStrategy(),
    "TIME_ORDER": TimeOrderFaultStrategy(),
}


def get_fault_strategy(name: str) -> FaultStrategy:
    return FAULT_STRATEGIES.get(name, FAULT_STRATEGIES["PRIORITY"])
