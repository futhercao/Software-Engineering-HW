"""HTTP 接口层（薄表现层）。

只负责：解析 HTTP 请求 → 在系统门面的锁保护下转发给对应控制器 → 把结果与最新聚合
状态序列化为 JSON 返回。不包含任何业务逻辑。静态资源（前端页面）也由本服务托管，
便于「启动 server.py 即可访问完整系统」。

POST 视为会改变状态的系统事件，处理后持久化；GET 为只读查询。所有处理都在
``system.lock`` 内完成，避免 ThreadingHTTPServer 多线程并发修改共享状态的竞态。
"""

from __future__ import annotations

import json
import mimetypes
import sys
import traceback
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core import ChargingStationSystem  # noqa: E402

ROOT = Path(__file__).resolve().parent
system = ChargingStationSystem()


def _first(query: dict, key: str) -> str:
    return str((query.get(key) or [""])[0]).strip()


class Handler(SimpleHTTPRequestHandler):
    # -- 静态资源安全：限制在 frontend 目录内 -----------------------------
    def translate_path(self, path: str) -> str:
        target = urlparse(path).path.lstrip("/") or "index.html"
        return str((ROOT / target).resolve())

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    # -- GET：静态资源 + 只读查询 ----------------------------------------
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path.startswith("/api/"):
            self._handle(self._route_get, parse_qs(parsed.query), mutating=False, path=parsed.path)
            return
        target = Path(self.translate_path(self.path))
        if not str(target).startswith(str(ROOT.resolve())):
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if target.is_dir():
            target = target / "index.html"
        if not target.exists():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # -- POST：系统事件 ---------------------------------------------------
    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/"):
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(body or "{}")
        except json.JSONDecodeError:
            payload = {}
        self._handle(self._route_post, payload, mutating=True, path=parsed.path)

    # -- 统一处理：加锁 → 路由 → （持久化）→ 返回 data + 最新 state -------
    def _handle(self, router, arg, mutating: bool, path: str) -> None:
        try:
            with system.lock:
                data = router(path, arg)
                if mutating:
                    system.save()
                state = system.public_state()
            self._write_json({"ok": True, "data": data, "state": state})
        except ValueError as exc:
            with system.lock:
                state = system.public_state()
            self._write_json({"ok": False, "error": str(exc), "state": state}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # 兜底：不泄露堆栈给前端，但记录到服务端
            traceback.print_exc()
            with system.lock:
                state = system.public_state()
            self._write_json({"ok": False, "error": f"服务器内部错误：{exc}", "state": state},
                             HTTPStatus.INTERNAL_SERVER_ERROR)

    def _route_get(self, path: str, query: dict):
        c = system
        if path == "/api/state":
            return c.public_state()
        if path == "/api/charging/car-state":
            return c.request_controller.Query_Car_State(_first(query, "carId"))
        if path == "/api/charging/state":
            return c.request_controller.Query_Charging_State(_first(query, "carId"))
        if path == "/api/bills":
            return c.billing_controller.Request_Bill(_first(query, "carId"), _first(query, "date"))
        if path == "/api/details":
            return c.billing_controller.Request_DetailedList(_first(query, "carId"), _first(query, "billId"))
        if path == "/api/piles":
            return c.monitor_controller.Query_PileState(_first(query, "pileId"))
        if path == "/api/queues":
            return c.monitor_controller.Query_QueueState()
        if path == "/api/reports":
            return c.billing_controller.report(_first(query, "period") or "day")
        raise ValueError("接口不存在")

    def _route_post(self, path: str, payload: dict):
        c = system
        routes = {
            "/api/reset": lambda p: c.reset(),
            "/api/login": c.login,
            "/api/accounts": c.account_controller.createNewAccount,
            "/api/accounts/password": c.account_controller.set_pwd,
            "/api/charging/request": c.request_controller.E_chargingRequest,
            "/api/charging/modify-amount": c.request_controller.Modify_Amount,
            "/api/charging/modify-mode": c.request_controller.Modify_Mode,
            "/api/charging/cancel": c.request_controller.Cancel_Request,
            "/api/charging/start": c.request_controller.Start_Charging,
            "/api/charging/end": c.request_controller.End_Charging,
            "/api/time/tick": c.tick,
            "/api/rules": c.pile_admin_controller.setParameters,
            "/api/piles/power": c.pile_admin_controller.set_power,
            "/api/piles/start": c.pile_admin_controller.Start_ChargingPile,
            "/api/fault": c.fault_controller.handlePileFault,
            "/api/recover": c.fault_controller.recoverPile,
            "/api/dispatch": lambda p: c.manual_dispatch(),
            "/api/dispatch/multi": c.fault_controller.dispatchWhenMultipleSlots,
            "/api/dispatch/batch": c.fault_controller.batchDispatchWhenFull,
            "/api/demo": lambda p: c.add_demo(),
            "/api/logs/clear": lambda p: c.clear_logs(),
        }
        if path not in routes:
            raise ValueError("接口不存在")
        return routes[path](payload)

    def _write_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt: str, *args) -> None:  # 静默默认访问日志
        return


def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5173
    host = "127.0.0.1"
    print(f"Charging station system running at http://{host}:{port}/")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
