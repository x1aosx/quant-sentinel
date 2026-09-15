# Scheduler 定时任务模块

## 定位

`xquant.scheduler` 是项目级任务调度基础设施，只负责触发、执行实例、队列、状态、重试、锁和恢复。它不依赖 `market_data`、策略、回测或通知业务模块。业务层通过 `TaskHandler` 和 `TaskContext` 接入。

主要调用关系：

```text
market_data / strategy / backtest / notification
                         |
                         v
                      scheduler
```

## 已实现范围

- `TaskDefinition`、`ScheduleDefinition`、`TaskExecution`、`TaskResult`、`TaskContext` 等领域模型。
- Cron、Interval、Date、FixedDelay 四类触发器。
- APScheduler 引擎适配，业务代码不直接操作 APScheduler。
- Local 与 Redis Dispatcher。Redis 队列支持优先级、延迟、inflight lease 和 ack。
- Local 与 Redis Lock、RateLimiter。
- TaskRegistry 的显式注册和装饰器注册。
- 统一 TaskExecutor：并发策略、超时、取消、重试、结构化事件和执行历史。
- Redis Worker、Heartbeat 和 Stale Execution Recovery。
- RecoveryService 同时回收 PostgreSQL 中过期的 `RUNNING/RETRYING` 执行和 Redis 中过期的 inflight delivery。
- PostgreSQL 仓储与四张基础表：
  `scheduler.scheduler_task`、`scheduler.scheduler_schedule`、
  `scheduler.scheduler_execution`、`scheduler.scheduler_execution_log`。
- `/api/v1/scheduler` 下的任务、计划、执行记录和 Worker 查询 API。
- 示例任务：
  - `market.daily.sync`：同步单个数据集。
  - `market.symbol.sync`：按 `symbols` 批量同步数据集。

## 配置

调度功能默认关闭，现有 SQLite 和单进程测试不受影响。

```env
SCHEDULER_ENABLED=true
SCHEDULER_EMBEDDED=false
SCHEDULER_ENGINE_TYPE=apscheduler
SCHEDULER_DISPATCHER_TYPE=redis
SCHEDULER_WORKER_CONCURRENCY=8
SCHEDULER_HEARTBEAT_INTERVAL_SECONDS=10
SCHEDULER_HEARTBEAT_TIMEOUT_SECONDS=60
SCHEDULER_LEASE_SECONDS=3600
SCHEDULER_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=60
SCHEDULER_RECOVERY_ENABLED=true
SCHEDULER_RECOVERY_INTERVAL_SECONDS=30
SCHEDULER_QUEUE_PREFIX=xqs:scheduler
SCHEDULER_LOCK_PREFIX=xqs:lock
```

本地单进程联调可设置：

```env
SCHEDULER_ENABLED=true
SCHEDULER_EMBEDDED=true
SCHEDULER_ENGINE_TYPE=memory
SCHEDULER_DISPATCHER_TYPE=local
```

## 启动

开发环境内嵌 API：

```powershell
uv run uvicorn xquant.api.app:create_app --factory --reload --port 8000
```

生产独立进程：

```bash
xquant-scheduler
xquant-worker --queue realtime --queue market-data --queue batch --queue default
```

API 进程在 `SCHEDULER_EMBEDDED=false` 时不启动调度引擎，只负责创建 Execution 并写入 Redis。独立 scheduler 进程负责 APScheduler 和 Recovery，worker 进程负责消费队列。

## 手动运行任务

```http
POST /api/v1/scheduler/tasks/market.daily.sync/run
Content-Type: application/json

{
  "params": {
    "source": "yfinance",
    "symbol": "GC=F",
    "timeframe": "1d",
    "lookback": 500,
    "adjust": "qfq"
  }
}
```

调度计划接口使用统一的触发器结构：

```json
{
  "id": "daily-gc",
  "task_name": "market.daily.sync",
  "trigger": {
    "type": "cron",
    "minute": "0",
    "hour": "18",
    "timezone": "Asia/Shanghai"
  },
  "params": {
    "source": "yfinance",
    "symbol": "GC=F",
    "timeframe": "1d"
  },
  "timezone": "Asia/Shanghai",
  "misfire_policy": "FIRE_ONCE"
}
```

## 测试

```powershell
cd api
$env:PYTHONPATH = "src"
python -m pytest tests/unit -q
python -m ruff check src/xquant/scheduler tests/unit/scheduler
```

测试使用内存仓储和 fake Redis，不访问真实 PostgreSQL、Redis 或外部行情接口。

## 后续范围

当前未实现完整交易日节假日数据、Planner/批量 DAG、动态 Worker 扩缩容、InfluxDB 指标写入和任务取消到正在执行的 Handler。取消正在执行的 Execution 会返回 `501`，避免只改数据库状态却没有停止 Worker。`TradingCalendar` 已提供工作日和 A 股交易时段基础能力，后续可接入真实交易日历数据并纳入 Schedule 过滤。

Redis delivery 使用 `SCHEDULER_LEASE_SECONDS` 作为租约。单次任务执行时间不应超过该值；更长任务应拆分批次或提高租约配置。当前按单个 Scheduler 进程管理触发状态，多个 Scheduler 实例同时运行前需要增加基于 PostgreSQL CAS 的调度领取。
