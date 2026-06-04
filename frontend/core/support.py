"""业务支撑设施：系统时钟、序列号生成器、事件日志。

这些是被多个业务服务共享的「基础设施型」协作者，通过依赖注入传入各服务，既保证全局
一致（同一时钟、同一套自增序列），又便于在单元测试中替换或预设。
"""

from __future__ import annotations

from .domain import FAST, TRICKLE, format_time


class Clock:
    """系统模拟时钟，以「启动后分钟数」记时，默认从 08:00 开始。"""

    def __init__(self, minutes: int = 8 * 60) -> None:
        self._minutes = int(minutes)

    def now(self) -> int:
        return self._minutes

    def advance(self, minutes: int) -> None:
        self._minutes += int(minutes)

    def set(self, minutes: int) -> None:
        self._minutes = int(minutes)


class Sequencer:
    """统一的自增序列号生成器：请求号、详单号以及按模式的排队号。"""

    def __init__(self) -> None:
        self._seq = {"request": 1, "detail": 1, "ticketFAST": 1, "ticketTRICKLE": 1}

    def next_request_id(self) -> str:
        value = self._seq["request"]
        self._seq["request"] = value + 1
        return f"REQ-{value:04d}"

    def next_detail_id(self) -> str:
        value = self._seq["detail"]
        self._seq["detail"] = value + 1
        return f"DL-{value:04d}"

    def next_ticket_number(self, mode: str) -> int:
        key = "ticketFAST" if mode == FAST else "ticketTRICKLE"
        value = self._seq[key]
        self._seq[key] = value + 1
        return value

    def dump(self) -> dict:
        return dict(self._seq)

    def load(self, data: dict) -> None:
        if data:
            self._seq.update({k: int(v) for k, v in data.items() if k in self._seq})


class EventLog:
    """事件日志，带系统时间戳，倒序保留最近若干条，供前端「事件日志」展示。"""

    def __init__(self, clock: Clock, limit: int = 120) -> None:
        self._clock = clock
        self._limit = limit
        self._entries: list[str] = []

    def log(self, message: str) -> None:
        stamp = format_time(self._clock.now())
        self._entries.insert(0, f"{stamp}  {message}")
        del self._entries[self._limit:]

    def clear(self) -> None:
        self._entries = []

    def entries(self) -> list[str]:
        return list(self._entries)

    def dump(self) -> list:
        return list(self._entries)

    def load(self, data: list) -> None:
        self._entries = list(data or [])
