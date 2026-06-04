"""控制器层（Controller Layer）。

按照 GRASP「控制器」模式，控制器是系统事件的「第一接收对象」：它本身不实现业务逻辑，
而是把请求转发给业务/应用层的服务对象处理（方案二：用例控制器）。本系统系统事件较多，
故按用例划分为多个用例控制器（注册、充电申请、账单、充电桩管理、监控、故障调度），
各自维护本用例的会话状态、降低耦合。

控制器方法名直接对应作业《系统事件》表中的指令（如 createNewAccount、E_chargingRequest、
handlePileFault 等），使「操作契约 → 交互图 → 代码」三者一一对应。
"""

from __future__ import annotations


class AccountController:
    """注册用例控制器：接收注册与口令验证系统事件。"""

    def __init__(self, account_service) -> None:
        self.account_service = account_service

    def createNewAccount(self, payload: dict) -> dict:
        return self.account_service.create_account(payload)

    def set_pwd(self, payload: dict) -> dict:
        return self.account_service.set_password(payload)


class ChargingRequestController:
    """充电申请用例控制器：接收充电申请、修改、取消、查询、开始与结束充电系统事件。"""

    def __init__(self, request_service) -> None:
        self.request_service = request_service

    def E_chargingRequest(self, payload: dict) -> dict:
        return self.request_service.submit_request(payload)

    def Modify_Amount(self, payload: dict) -> dict:
        return self.request_service.modify_amount(payload)

    def Modify_Mode(self, payload: dict) -> dict:
        return self.request_service.modify_mode(payload)

    def Cancel_Request(self, payload: dict) -> dict:
        return self.request_service.cancel_request(payload)

    def Query_Car_State(self, car_id: str) -> dict:
        return self.request_service.car_state(car_id)

    def Start_Charging(self, payload: dict) -> dict:
        return self.request_service.start_charging(payload)

    def Query_Charging_State(self, car_id: str) -> dict:
        return self.request_service.charging_state(car_id)

    def End_Charging(self, payload: dict) -> dict:
        return self.request_service.end_charging(payload)


class BillingController:
    """账单/详单用例控制器：接收查看账单与查看详单系统事件。"""

    def __init__(self, billing_service) -> None:
        self.billing_service = billing_service

    def Request_Bill(self, car_id: str = "", date: str = "") -> dict:
        return self.billing_service.query_bill(car_id, date)

    def Request_DetailedList(self, car_id: str = "", bill_id: str = "") -> dict:
        return self.billing_service.query_details(car_id, bill_id)

    def report(self, period: str = "day") -> dict:
        return {"reports": self.billing_service.report(period)}


class PileAdminController:
    """充电桩管理用例控制器：接收启动/关闭充电桩、设置参数等系统事件。"""

    def __init__(self, pile_service) -> None:
        self.pile_service = pile_service

    def powerOn(self, payload: dict) -> dict:
        return self.pile_service.start_pile(payload)

    def powerOff(self, payload: dict) -> dict:
        return self.pile_service.power_off(payload)

    def Start_ChargingPile(self, payload: dict) -> dict:
        return self.pile_service.start_pile(payload)

    def set_power(self, payload: dict) -> dict:
        return self.pile_service.set_power(payload)

    def setParameters(self, payload: dict) -> dict:
        return self.pile_service.set_parameters(payload)


class MonitorController:
    """监控用例控制器：接收查看充电桩状态与查看队列状态系统事件。"""

    def __init__(self, monitor_service) -> None:
        self.monitor_service = monitor_service

    def Query_PileState(self, pile_id: str = "") -> dict:
        return self.monitor_service.query_pile_state(pile_id)

    def Query_QueueState(self) -> dict:
        return self.monitor_service.query_queue_state()


class FaultDispatchController:
    """故障调度用例控制器：接收充电桩故障、故障恢复与扩展调度（bonus）系统事件。"""

    def __init__(self, fault_service, dispatch_service) -> None:
        self.fault_service = fault_service
        self.dispatch_service = dispatch_service

    def handlePileFault(self, payload: dict) -> dict:
        return self.fault_service.handle_pile_fault(payload)

    def recoverPile(self, payload: dict) -> dict:
        return self.fault_service.recover_pile(payload)

    def dispatchWhenMultipleSlots(self, payload: dict) -> dict:
        from .domain import FAST
        return self.dispatch_service.dispatch_when_multiple_slots(payload.get("mode", FAST))

    def batchDispatchWhenFull(self, payload: dict) -> dict:
        return self.dispatch_service.batch_dispatch_when_full()
