"""智能充电桩调度计费系统 —— 分层后端核心包。

分层（自顶向下）：
  controllers  控制器层：系统事件第一接收对象，转发给业务服务
  services     业务/应用层：实现用例的系统级功能
  fault_strategies  策略层：故障调度策略族（Strategy 模式）
  domain       领域层：实体（含行为）与领域规则
  repositories 持久化层：仓储封装数据访问
  support      支撑设施：时钟、序列号、事件日志
  system       应用门面：装配各层、线程安全、持久化、聚合视图
"""

from .system import ChargingStationSystem

__all__ = ["ChargingStationSystem"]
