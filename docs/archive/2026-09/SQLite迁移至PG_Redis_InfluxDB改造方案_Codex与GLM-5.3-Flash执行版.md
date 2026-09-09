# X-QuantSentinel：SQLite 迁移至 PostgreSQL + Redis + InfluxDB 改造方案

> **用途**：直接交给 Codex 与 GLM-5.3-Flash 分阶段实施和审查  
> **文档版本**：v1.0  
> **编制日期**：2026-09-09  
> **目标系统**：X-QuantSentinel 量化交易与股票统一管理系统  
> **核心原则**：量化策略是业务核心；数据必须可追溯、可复现、可校验、可回滚。
>
> **执行状态（2026-09-09）**：已完成 PostgreSQL / Redis / InfluxDB 通用客户端、StorageSettings、持久化门面和 API 存储健康检查；三个中间件连接与健康检查通过。尚未完成 Alembic 迁移、SQLite 全量数据迁移和长期双写/Outbox 体系。

---

## 1. 改造结论

当前系统默认使用 SQLite。目标不是把所有 SQLite 表机械地搬到 PostgreSQL，而是按数据性质拆分到三类存储：

| 存储 | 系统定位 | 负责的数据 | 是否为事实源 |
|---|---|---|---|
| PostgreSQL | 事务数据库、业务事实库 | 用户、证券主数据、自选股、组合、持仓、订单、成交、策略、策略版本、参数、信号、回测元数据、任务、审计、迁移记录 | **是** |
| Redis | 实时状态、缓存、协调和轻量事件流 | 最新行情缓存、最新指标、分布式锁、限流状态、任务进度、短期幂等键、Redis Streams | 否，可重建 |
| InfluxDB | 时序数据库 | Tick、逐笔、盘口快照、K 线、因子值、技术指标、净值曲线、实时策略指标 | 是，但只针对时序明细 |
| SQLite | 迁移来源和本地备份 | 仅保留原数据库只读副本；不再作为生产默认写入库 | 否 |

最终数据流：

```text
行情供应商
    │
    ▼
采集与标准化服务
    ├── 写 InfluxDB：Tick / K线 / 因子 / 指标
    ├── 更新 Redis：最新行情 / 最新指标 / 实时状态
    └── 更新 PostgreSQL：采集检查点 / 数据质量 / 任务状态

策略引擎
    ├── PostgreSQL：策略版本、参数、组合、风控配置
    ├── InfluxDB：历史行情和因子窗口
    ├── Redis：最新状态和短期缓存
    └── PostgreSQL：信号、运行记录、决策证据、审计

通知与执行
    PostgreSQL Transaction + Outbox
        → Redis Stream
        → 通知服务 / 模拟交易 / 实盘适配器
```

### 1.1 推荐迁移方式

当前阶段优先采用**停机窗口迁移**，不做长期双写：

1. 停止写入 SQLite；
2. 对 SQLite 文件做一致性检查、备份和哈希；
3. 初始化 PostgreSQL、Redis、InfluxDB；
4. 执行可重复的数据迁移；
5. 完成数量、哈希、约束和业务流程校验；
6. 切换环境变量，使 PostgreSQL 成为默认业务数据库；
7. 保留 SQLite 只读副本，不再回写。

理由：当前系统从单机 SQLite 迁移到多存储架构时，长期双写会引入分布式一致性、回放、补偿和冲突处理，复杂度明显高于停机迁移。除非系统已经有多用户连续交易写入要求，否则不应在第一轮引入双写。

---

## 2. 强制约束

Codex 和 GLM-5.3-Flash 实施时必须遵守以下规则：

1. **先盘点，后建模**：未生成 SQLite 结构清单、数据分类矩阵和调用关系图之前，不允许直接删除 SQLite 代码。
2. **不破坏源数据**：迁移脚本对 SQLite 只读；禁止对源库执行 `UPDATE`、`DELETE`、`ALTER`、`VACUUM INTO` 覆盖原文件等操作。
3. **默认数据库切换后禁止静默回退**：生产配置连接 PostgreSQL 失败时必须启动失败或进入明确降级状态，禁止偷偷切回 SQLite。
4. **迁移可重复执行**：所有导入必须具备幂等键、检查点和 `ON CONFLICT`/确定性覆盖规则。
5. **PostgreSQL 是业务事实中心**：持仓、订单、成交、策略、信号、资金和审计不能只存在 Redis 或 InfluxDB。
6. **Redis 中的数据必须可重建**：任何只保存在 Redis、丢失后无法恢复的数据设计均视为阻断问题。
7. **禁止跨库伪事务**：不得在一个业务请求中依赖“同时成功写 PostgreSQL、Redis、InfluxDB”来保证正确性；使用 Outbox、检查点和幂等消费。
8. **时区统一**：数据库时间统一保存 UTC；交易日单独保存 `trading_date`；展示层再转换为市场时区或用户时区。
9. **金额与成交数据禁止二进制浮点**：PostgreSQL 中价格、金额、数量精度使用 `NUMERIC` 或经过明确缩放的整数。
10. **迁移与应用升级可独立回滚**：数据库迁移、数据迁移和应用切换不能绑成一个不可逆操作。
11. **禁止使用 Pickle 序列化 Redis 数据**：统一使用 JSON、MessagePack 或明确版本化的二进制协议。
12. **生产环境禁止自动 `create_all()`**：表结构只能通过 Alembic 版本化迁移创建和升级。
13. **Alembic 自动生成结果必须人工/模型复核**：禁止未经检查直接执行自动生成的删除表、删除列、改名操作。
14. **所有容器镜像必须锁定版本或 digest**：禁止生产使用 `latest`。

---

## 3. 实施边界与假设

本方案按 Python 3.11+ 项目设计，适用于 FastAPI、Flask、桌面端服务化后端或普通 Python 服务。实施时保留现有同步/异步编程模型：

- 现有代码是同步调用时，数据库迁移阶段继续使用同步 SQLAlchemy + psycopg；
- 现有代码已经是 asyncio/FastAPI 异步链路时，使用 SQLAlchemy AsyncSession + asyncpg；
- **不得为了迁移数据库顺便把全项目同步代码改成异步代码**；
- **不得为了迁移数据库顺便重写量化策略逻辑**；
- 原策略计算结果必须通过回归样本证明迁移前后一致。

当前对话未提供本地项目源代码和实际 SQLite 文件，所以第一阶段必须由 Codex 自动发现：

- SQLite 文件位置；
- ORM 或原生 SQL 使用方式；
- 现有表、索引、触发器和视图；
- 哪些模块读写 SQLite；
- 哪些表属于时序数据；
- 是否存在 JSON 文件、CSV、Parquet 与 SQLite 混合持久化；
- 是否存在测试环境专用 SQLite。

若项目仍保留 PA_Agent 派生结构，应优先检查 `pa_agent/records`、`pa_agent/data`、配置目录及所有包含 `sqlite`、`.db`、`.sqlite` 的路径，但不得假设这些目录一定是当前真实结构。

---

## 4. 目标技术栈

### 4.1 Python 依赖

根据项目同步/异步模式二选一，不允许重复引入两套数据库驱动。

#### 同步项目

```toml
sqlalchemy = ">=2.0,<3.0"
alembic = ">=1.16,<2.0"
psycopg = { version = ">=3.2,<4.0", extras = ["binary", "pool"] }
redis = { version = ">=6,<9", extras = ["hiredis"] }
influxdb3-python = ">=0.20,<1.0"
pydantic-settings = ">=2.0,<3.0"
```

#### 异步项目

```toml
sqlalchemy = { version = ">=2.0,<3.0", extras = ["asyncio"] }
alembic = ">=1.16,<2.0"
asyncpg = ">=0.30,<1.0"
psycopg = { version = ">=3.2,<4.0", extras = ["binary"] } # 仅供 Alembic/运维脚本同步连接
redis = { version = ">=6,<9", extras = ["hiredis"] }
influxdb3-python = ">=0.20,<1.0"
pydantic-settings = ">=2.0,<3.0"
```

> 版本范围是兼容边界，不代表无需锁文件。Codex 必须更新项目现有的 `uv.lock`、`poetry.lock` 或 `requirements.lock`，并通过完整测试。

### 4.2 服务版本建议

| 组件 | 开发默认 | 生产要求 |
|---|---|---|
| PostgreSQL | `postgres:18` | 锁定 PostgreSQL 18 的具体补丁版本或镜像 digest |
| Redis | `redis:8` | 锁定 Redis 8 的具体补丁版本或镜像 digest |
| InfluxDB | `influxdb:3-core` | 锁定明确版本；禁止 `influxdb:latest` |

截至 2026-09-09，PostgreSQL 18 是当前稳定主版本，PostgreSQL 19 仍是 Beta，不应作为本项目生产基线。InfluxDB 官方已公告其 Docker `latest` 标签将在 2026-09-15 指向 InfluxDB 3 Core，因此必须显式指定版本通道或 digest。

---

## 5. 目标目录结构

Codex 应先适配当前仓库结构；不存在等价目录时再新增。推荐结构如下：

```text
src/ 或现有主包/
├── domain/
│   ├── entities/
│   ├── value_objects/
│   └── ports/
│       ├── repositories.py
│       ├── market_data.py
│       ├── cache.py
│       └── event_bus.py
├── application/
│   ├── services/
│   └── unit_of_work.py
├── infrastructure/
│   ├── postgres/
│   │   ├── base.py
│   │   ├── engine.py
│   │   ├── models/
│   │   ├── repositories/
│   │   └── unit_of_work.py
│   ├── redis/
│   │   ├── client.py
│   │   ├── cache.py
│   │   ├── locks.py
│   │   └── streams.py
│   ├── influx/
│   │   ├── client.py
│   │   ├── schema.py
│   │   ├── writer.py
│   │   └── repository.py
│   └── legacy_sqlite/
│       ├── inspector.py
│       └── reader.py
├── config/
│   └── storage.py
└── api/ 或 gui/

migrations/
├── env.py
├── script.py.mako
└── versions/

scripts/
├── inspect_sqlite.py
├── migrate_sqlite.py
├── verify_migration.py
├── warm_redis_cache.py
└── export_legacy_snapshot.py

tests/
├── fixtures/legacy_sqlite/
├── unit/storage/
├── integration/postgres/
├── integration/redis/
├── integration/influx/
└── migration/

docs/database/
├── SQLite现状盘点.md
├── SQLite表到目标存储映射.md
├── 数据库运行手册.md
├── 数据迁移验证报告.md
└── reviews/
```

### 5.1 架构边界

业务代码不得直接导入以下对象：

```python
sqlite3.Connection
sqlalchemy.Session
redis.Redis
InfluxDBClient3
```

业务层只能依赖端口接口，例如：

```python
class StrategyRepository(Protocol): ...
class PortfolioRepository(Protocol): ...
class MarketDataRepository(Protocol): ...
class CachePort(Protocol): ...
class EventPublisher(Protocol): ...
class UnitOfWork(Protocol): ...
```

这样才能避免未来再次被某个具体数据库绑定，也便于单元测试和策略回放。

---

## 6. 阶段 0：自动盘点 SQLite 与调用关系

### 6.1 Codex 必须执行的代码搜索

优先使用 `rg`：

```bash
rg -n --hidden \
  -g '!**/.git/**' \
  -g '!**/.venv/**' \
  -g '!**/node_modules/**' \
  '(sqlite|sqlite3|aiosqlite|SQLITE|\.sqlite3?\b|\.db\b|PRAGMA|AUTOINCREMENT|INSERT OR REPLACE|WITHOUT ROWID|strftime\(|datetime\(|json_extract\(|last_insert_rowid)' .

rg -n --hidden \
  -g '!**/.git/**' \
  '(DATABASE_URL|DB_PATH|DATABASE_PATH|create_engine|create_async_engine|SessionLocal|sessionmaker|connect\()' .
```

同时检查：

```bash
find . -type f \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) -print
find . -type f \( -name '*.json' -o -name '*.csv' -o -name '*.parquet' \) -print
```

### 6.2 SQLite 自动检查脚本

新增 `scripts/inspect_sqlite.py`，使用 SQLite URI 和 `uri=True` 只读连接：

```text
# 对仍可能变化的原库
file:<absolute-path>?mode=ro

# 只对已冻结、已复制且不会再变化的快照使用
file:<absolute-path>?mode=ro&immutable=1
```

`immutable=1` 不得用于仍在写入的数据库，否则可能绕过锁和变更检测。脚本必须输出：

- SQLite 版本；
- 文件路径、文件大小、SHA-256；
- `PRAGMA integrity_check` 结果；
- 所有表、视图、索引、触发器；
- 每张表的建表 SQL；
- 列名、声明类型、非空、默认值、主键；
- 外键定义；
- 行数；
- 每列实际出现的数据类型分布；
- 最小/最大时间；
- 空值数、空字符串数；
- JSON 列合法率；
- 重复自然键；
- 孤儿外键；
- 每表规范化校验哈希；
- 估算迁移目标：PostgreSQL / InfluxDB / Redis / 保留文件。

输出文件：

```text
docs/database/SQLite现状盘点.md
artifacts/database/sqlite_inventory.json
artifacts/database/sqlite_schema.sql
artifacts/database/sqlite_checksums.json
```

### 6.3 数据分类规则

Codex 根据实际字段和调用场景填写映射表，不得只根据表名猜测。

| 数据特征 | 目标存储 | 示例 |
|---|---|---|
| 需要事务、唯一约束、外键、审计 | PostgreSQL | 用户、组合、持仓、订单、策略、参数、信号 |
| 大量按证券和时间范围读取、主要追加 | InfluxDB | Tick、K 线、因子、指标、净值序列 |
| 短期、可丢失、可从事实库重建 | Redis | 最新行情、查询缓存、任务进度 |
| 协调、限流、短时幂等 | Redis | 分布式锁、供应商限流桶、消费去重 |
| 大型原始文件或模型文件 | 保留文件/未来对象存储 | 原始 CSV、Parquet、模型权重 |

### 6.4 阶段 0 验收条件

- [ ] 找到所有 SQLite 文件和连接入口；
- [ ] 找到所有原生 SQL、ORM Model、Repository 和数据访问工具；
- [ ] 每个现有表都有目标存储和迁移规则；
- [ ] 现有测试全部运行并保存基线结果；
- [ ] 至少保存 3 组代表性策略输入及输出，作为迁移后回归样本；
- [ ] 未修改任何生产数据路径；
- [ ] 生成 `SQLite现状盘点.md` 与 `SQLite表到目标存储映射.md`。

---

## 7. PostgreSQL 改造设计

### 7.1 Schema 规划

推荐使用多个 PostgreSQL Schema，而不是所有表放入 `public`：

```text
ref         证券、交易所、交易日历、公司行动、复权因子
identity    用户、权限、API 凭据元数据
portfolio   账户、组合、持仓、资金流水、持仓快照
trading     订单、成交、执行回报、订单事件
strategy    策略、版本、参数集、运行、信号、决策证据
research    数据集版本、回测、实验、模型版本
ops         数据源、任务、检查点、Outbox、通知投递
migration   旧 ID 映射、迁移批次、迁移错误
audit       操作审计和安全事件
```


### 7.2 核心表最低集合

第一轮迁移至少覆盖：

```text
ref.instrument
ref.exchange
ref.trading_calendar
portfolio.portfolio
portfolio.position
strategy.strategy
strategy.strategy_version
strategy.strategy_parameter_set
strategy.strategy_run
strategy.signal
research.backtest_run
ops.data_source
ops.ingest_checkpoint
ops.outbox_event
migration.migration_batch
migration.legacy_id_map
migration.migration_row_error
audit.operation_log
```

实际存在用户、订单、成交、自选股、通知等模块时，同步建立对应表，不能继续保留在 SQLite。

### 7.3 通用字段规范

业务表建议具备：

```text
id              UUID 或 BIGINT 主键，项目内统一
created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
version         INTEGER NOT NULL DEFAULT 1，用于乐观锁时启用
metadata        JSONB，仅存扩展字段
```

行情相关元数据建议具备：

```text
event_time      市场事件时间，TIMESTAMPTZ
data_time       数据代表的时间，例如 K 线起始时间
received_at     系统收到时间，TIMESTAMPTZ
ingested_at     成功入库时间，TIMESTAMPTZ
trading_date    DATE，所属交易日
source          数据供应商
source_seq      数据源原始序号
schema_version  数据结构版本
quality_flags   数据质量位标志或 JSONB
```

### 7.4 类型映射

| SQLite 常见形式 | PostgreSQL 目标 | 处理要求 |
|---|---|---|
| `INTEGER PRIMARY KEY` | `BIGINT GENERATED ... AS IDENTITY` 或 UUID | 必须建立旧 ID 映射 |
| `INTEGER` 布尔值 | `BOOLEAN` | 仅接受 0/1/null，异常值进入错误表 |
| `REAL` 金额/价格 | `NUMERIC(p,s)` | 根据实际数据扫描确定精度 |
| `REAL` 指标值 | `DOUBLE PRECISION` | 允许用于技术指标、概率、评分 |
| `TEXT` 时间 | `TIMESTAMPTZ` / `DATE` | 明确时区和格式，禁止隐式解析 |
| Unix 秒/毫秒 | `TIMESTAMPTZ` + 保留原值可选 | 自动判断前必须有阈值测试 |
| JSON 文本 | `JSONB` | 迁移前解析，非法 JSON 记录错误 |
| 空字符串 | `NULL` 或保留空串 | 每列单独制定规则 |
| `BLOB` | `BYTEA` 或外部文件引用 | 大对象优先保存引用和哈希 |

### 7.5 SQLite 与 PostgreSQL 方言差异

Codex 必须逐项扫描和改写，不能仅替换连接字符串：

| SQLite 写法/行为 | PostgreSQL 改造方式 | 注意事项 |
|---|---|---|
| `INSERT OR REPLACE` | `INSERT ... ON CONFLICT (...) DO UPDATE` | SQLite 的 REPLACE 可能先删除再插入，审计、外键和触发器语义不同 |
| `INSERT OR IGNORE` | `ON CONFLICT (...) DO NOTHING` | 必须明确冲突目标，不能吞掉其他约束错误 |
| `last_insert_rowid()` | `INSERT ... RETURNING id` | 同一事务内直接取得返回值 |
| `INTEGER PRIMARY KEY AUTOINCREMENT` | Identity 或应用生成 UUID | 不机械复制 `sqlite_sequence`，除非决定保留旧整数 ID |
| `datetime('now')` | `CURRENT_TIMESTAMP` / `now()` | PostgreSQL 使用 `TIMESTAMPTZ` 并统一 UTC |
| `strftime(...)` | `date_trunc`、`extract`、`to_char` | `to_char` 仅用于展示，不用于核心时间比较 |
| `json_extract` / `json_each` | JSONB 运算符、JSONPath、`jsonb_array_elements` | 迁移前确保 JSON 合法 |
| `IFNULL(a,b)` | `COALESCE(a,b)` | 检查类型推断差异 |
| `GROUP_CONCAT` | `string_agg` | 必须显式指定排序，否则结果顺序不稳定 |
| `COLLATE NOCASE` | `citext` 或 `lower(column)` 功能索引 | 先确认中文、证券代码、用户名的大小写语义 |
| `GLOB` | `LIKE`、`ILIKE` 或正则 | 模式语法不同 |
| `LIMIT offset,count` | `LIMIT count OFFSET offset` | 参数顺序不能沿用 |
| `?` 参数占位符 | SQLAlchemy 绑定参数 | 禁止自行拼接 SQL |
| `ROWID` / `WITHOUT ROWID` | 显式主键和索引 | 找出所有依赖隐式 ROWID 的排序或分页 |
| `PRAGMA foreign_keys` | PostgreSQL 始终执行约束 | 迁移前先清理/隔离孤儿数据 |
| 动态列类型 | 严格列类型 | 导入前扫描每列真实类型分布 |
| 默认大小写/排序规则 | 显式 collation 或规范化字段 | 搜索、唯一约束结果可能变化 |
| SQLite 单写锁 | PostgreSQL MVCC 与行锁 | 重新检查并发更新、乐观锁和死锁重试 |

所有方言改写都必须有针对性测试，尤其是 Upsert、分页、大小写唯一性、时间筛选和聚合顺序。

### 7.6 主键与自然键

- 证券必须使用稳定的 `instrument_id`；不能把 `symbol` 单独作为全局主键；
- 证券唯一约束建议为 `(exchange_id, symbol, valid_from)` 或根据实际代码变更模型设计；
- 策略版本不可覆盖更新，使用新版本记录；
- 策略信号必须包含确定性幂等键；
- K 线唯一性不在 PostgreSQL 明细表实现，而由 InfluxDB 时序键及迁移检查点保证；
- 订单、成交、通知等外部事件保留供应商/券商原始 ID，并建立唯一约束。

### 7.7 JSONB 使用边界

适合 JSONB：

- 策略参数快照；
- 模型超参数；
- 信号决策证据；
- 数据供应商原始扩展字段；
- 兼容旧版本的未知字段。

必须建成普通列：

- `instrument_id`；
- `strategy_id`、`strategy_version_id`；
- `status`；
- `event_time`、`trading_date`；
- `price`、`quantity`、`amount`；
- 所有经常用于过滤、排序、Join、唯一约束的字段。

### 7.8 索引与分区

索引依据真实查询建立，初始建议：

```text
ref.instrument(exchange_id, symbol)
portfolio.position(portfolio_id, instrument_id) UNIQUE
strategy.strategy_version(strategy_id, version) UNIQUE
strategy.strategy_run(strategy_version_id, started_at DESC)
strategy.signal(instrument_id, event_time DESC)
strategy.signal(strategy_version_id, event_time DESC)
ops.outbox_event(available_at) WHERE published_at IS NULL
ops.outbox_event(idempotency_key) UNIQUE
ops.ingest_checkpoint(source_id, instrument_id, interval) UNIQUE
```

规则：

- 大量追加且按时间查询的审计、信号、订单事件可评估 BRIN；
- JSONB 只有存在真实查询时才建立 GIN；
- 状态队列优先使用部分索引；
- 高频更新表避免过度索引；
- 数据规模未达到阈值时不要提前分区；
- 需要分区时优先按 `event_time` 月度分区，并用实际查询验证分区裁剪；
- 所有索引必须通过 `EXPLAIN (ANALYZE, BUFFERS)` 或等价压测证明价值。

### 7.9 事务与 Outbox

所有需要触发通知、缓存失效、执行任务的业务写入采用：

```text
1. 开启 PostgreSQL 事务
2. 更新业务表
3. 同一事务插入 ops.outbox_event
4. 提交
5. Outbox Worker 使用 SELECT ... FOR UPDATE SKIP LOCKED 获取事件
6. 发布到 Redis Stream
7. 成功后标记 published_at
8. 失败则增加 retry_count、记录 next_retry_at
```

`ops.outbox_event` 最低字段：

```text
id, aggregate_type, aggregate_id, event_type, payload,
created_at, available_at, published_at, retry_count,
last_error, idempotency_key
```

`idempotency_key` 建立唯一约束。

### 7.10 数据库连接与运行参数

配置项必须可通过环境变量覆盖：

```text
DATABASE_URL
DB_POOL_SIZE
DB_MAX_OVERFLOW
DB_POOL_TIMEOUT_SECONDS
DB_STATEMENT_TIMEOUT_MS
DB_CONNECT_TIMEOUT_SECONDS
DB_ECHO
```

推荐初始值仅作为开发默认，生产必须压测后调整：

```text
pool_size=10
max_overflow=20
pool_timeout=30s
pool_pre_ping=true
statement_timeout=30s
```

### 7.11 Alembic 规则

- 初始化 Alembic；
- 所有约束显式命名；
- `compare_type=True`；
- 自动生成后检查表名/列名改名是否被误识别为删除后新建；
- 迁移脚本同时提供 `upgrade()` 和可行的 `downgrade()`；
- 数据迁移不要全部塞进 Alembic，使用独立、可检查点恢复的脚本；
- CI 执行 `alembic upgrade head`、`alembic downgrade -1`、再次 `upgrade head`；
- CI 执行 `alembic check`，防止 Model 与迁移版本漂移；
- 生产启动只允许执行显式部署步骤，不允许应用进程自动生成迁移。

---

## 8. InfluxDB 改造设计

### 8.1 时序表规划

根据实际 SQLite 数据选择迁移，推荐表：

```text
market_bar
market_tick
orderbook_snapshot
factor_value
technical_indicator
portfolio_nav
strategy_metric
market_breadth
```

### 8.2 `market_bar` 结构

建议标签/维度：

```text
instrument_id
interval
source
adjustment
```

建议字段：

```text
open
high
low
close
volume
amount
vwap
trade_count
is_complete
quality_flags
source_seq
original_event_time_ns
```

时间列：

```text
bar_start_time，UTC，纳秒精度
```

自然唯一语义：

```text
instrument_id + interval + source + adjustment + bar_start_time
```

### 8.3 `market_tick` 结构

标签应控制基数：

```text
instrument_id
source
exchange
```

字段：

```text
price
quantity
amount
side
source_seq
trade_id
received_at_ns
quality_flags
```

时间：

```text
event_time_ns
```

同一证券在同一时间戳可能出现多条 Tick。处理规则：

1. 优先使用供应商提供的纳秒时间；
2. 时间精度不足时，使用 `source_seq` 生成确定性的纳秒偏移；
3. 同时保留原始时间和原始序号；
4. 禁止把随机 UUID 作为每个 Tick 的 tag，避免无界高基数；
5. 重跑迁移时，同一源事件必须生成完全相同的时间和标签集合。

### 8.4 因子与指标

不要建立“每个因子一张表”，优先使用：

```text
factor_value
  tags: instrument_id, factor_id, timeframe, strategy_version_id(optional)
  fields: value, rank, zscore, quality_flags
  time: event_time
```

因子定义、代码版本、参数、输入数据集版本保存在 PostgreSQL；只有随时间变化的数值存入 InfluxDB。

### 8.5 写入策略

- 批量写入，不逐点创建客户端；
- 初始批大小配置为 5,000～20,000 点，根据压测调整；
- 写入失败按可重试/不可重试分类；
- 对部分写入失败必须解析失败点并写入 `migration.migration_row_error`；
- 迁移任务记录起止时间、数量、重试次数和校验摘要；
- 写入成功后再更新 PostgreSQL 的 `ops.ingest_checkpoint`；
- 消费消息时，InfluxDB 写入和检查点更新完成后再 ACK。

### 8.6 读取策略

- 业务服务先从 PostgreSQL 获得证券、策略、权限和版本信息；
- 再以 ID 和时间范围查询 InfluxDB；
- 不尝试在 SQL 层做 PostgreSQL 与 InfluxDB 跨库 Join；
- 查询结果按统一 DTO 返回，策略代码不得感知 InfluxDB 客户端类型；
- 热点窗口可放 Redis，但 Redis miss 时必须能够回源 InfluxDB。

### 8.7 数据保留

第一轮迁移暂不自动删除任何旧时序数据。上线稳定后再配置：

| 数据 | 建议热保留 | 备注 |
|---|---:|---|
| Tick / 逐笔 | 7～30 天起步 | 完整历史建议未来归档 Parquet |
| 盘口快照 | 7～30 天 | 体量最大，先做 PoC |
| 1 分钟 K 线 | 1～3 年或按容量 | 回测需要可长期保存 |
| 日线 K 线 | 长期 | 数据量较小 |
| 因子和指标 | 30～365 天或按版本 | 重要回测版本可长期保存 |
| 组合净值 | 长期 | 同时在 PostgreSQL 保存汇总元数据 |

任何保留策略启用前，必须完成备份、回放和历史回测需求确认。

---

## 9. Redis 改造设计

### 9.1 Key 命名

统一前缀和版本：

```text
xqs:v1:quote:last:{instrument_id}
xqs:v1:bar:last:{interval}:{instrument_id}
xqs:v1:factor:last:{factor_id}:{instrument_id}
xqs:v1:strategy:state:{strategy_run_id}
xqs:v1:task:progress:{task_id}
xqs:v1:idempotency:{namespace}:{key}
xqs:v1:ratelimit:{provider}:{credential_id}
xqs:v1:lock:{resource}:{resource_id}
xqs:v1:stream:market-data
xqs:v1:stream:strategy-signal
xqs:v1:stream:notification
xqs:v1:stream:execution
xqs:v1:stream:dead-letter
```

### 9.2 TTL 初始建议

| Key 类型 | 初始 TTL | 说明 |
|---|---:|---|
| 最新实时行情 | 10 分钟 | 交易时段持续刷新 |
| 最新 K 线 | 2 天 | 日线可延长 |
| 最新因子/指标 | 1～24 小时 | 根据计算频率 |
| 任务进度 | 24 小时 | 完成后保留短期查询 |
| 幂等键 | 1～7 天 | 不得短于最大重试窗口 |
| 分布式锁 | 15～60 秒 | 必须续租或保证任务小于 TTL |
| 限流桶 | 按供应商窗口 | 秒、分钟、日额度分开 |

所有 TTL 必须集中配置，禁止散落魔法数字。

### 9.3 缓存模式

使用 Cache-Aside：

```text
读取：Redis → miss → PostgreSQL/InfluxDB → 回填 Redis
写入：先提交 PostgreSQL/InfluxDB → 通过事件删除或刷新 Redis
```

禁止：

- 先更新缓存再提交 PostgreSQL；
- 把 Redis 值当成持仓、余额、订单的最终结果；
- Redis 故障时用过期缓存覆盖 PostgreSQL；
- 无 TTL 的普通缓存；
- 多服务使用不一致的 JSON 结构且无版本号。

### 9.4 Redis Streams

需要确认、重试、消费组和回放的消息使用 Streams；页面实时刷新且允许丢失的数据才使用 Pub/Sub。

每个消费者必须：

- 使用消费者组；
- 成功后 ACK；
- 定期回收 pending 消息；
- 设置最大重试次数；
- 超限进入 Dead Letter Stream；
- 记录 `event_id` 和业务幂等键；
- Stream 设置近似 `MAXLEN`，防止无限增长。

### 9.5 Redis 故障行为

| 功能 | Redis 故障时行为 |
|---|---|
| 普通查询缓存 | 直接回源，记录告警，不影响正确性 |
| 最新行情缓存 | 回源 InfluxDB；标记延迟状态 |
| 分布式锁 | 阻止需要互斥的任务启动，不能无锁继续执行 |
| 限流 | 使用本地保守限流或暂停供应商请求 |
| 关键 Stream | Worker readiness 失败并告警；业务事件仍留在 PostgreSQL Outbox |

---

## 10. 配置改造

### 10.1 环境变量

`.env.example` 至少包含：

```dotenv
# Storage selection
STORAGE_BACKEND=postgres
LEGACY_SQLITE_PATH=./data/app.db
LEGACY_SQLITE_READ_ONLY=true

# PostgreSQL
DATABASE_URL=postgresql+psycopg://xqs_app:change_me@postgres:5432/xquant_sentinel
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20
DB_POOL_TIMEOUT_SECONDS=30
DB_STATEMENT_TIMEOUT_MS=30000

# Redis
REDIS_URL=redis://:change_me@redis:6379/0
REDIS_KEY_PREFIX=xqs:v1
REDIS_SOCKET_TIMEOUT_SECONDS=3
REDIS_HEALTH_CHECK_INTERVAL_SECONDS=30

# InfluxDB 3
INFLUXDB_URL=http://influxdb3-core:8181
INFLUXDB_TOKEN=change_me
INFLUXDB_DATABASE=xquant_market
INFLUXDB_TIMEOUT_SECONDS=30
INFLUXDB_WRITE_BATCH_SIZE=5000

# Migration
MIGRATION_BATCH_SIZE=2000
MIGRATION_ERROR_LIMIT=100
MIGRATION_DRY_RUN=true
```

### 10.2 配置验证

启动时必须验证：

- `STORAGE_BACKEND=postgres` 时必须存在 PostgreSQL URL；
- 禁止生产环境使用 SQLite URL；
- 禁止默认密码；
- Redis URL 不得打印密码；
- InfluxDB Token 不得进入日志；
- `LEGACY_SQLITE_READ_ONLY` 在迁移后必须为 `true`；
- 生产环境缺少必需配置时启动失败。

### 10.3 健康检查

推荐端点：

```text
/health/live    仅检查进程和事件循环
/health/ready   检查当前服务依赖的存储
/health/storage 返回 PostgreSQL、Redis、InfluxDB 的分项状态与延迟，不返回凭据
```

PostgreSQL 是所有业务服务的强依赖；Redis 与 InfluxDB 是否影响 readiness 根据服务职责分别判断，不能用一个统一规则覆盖 API、行情 Worker、策略 Worker 和通知 Worker。

---

## 11. Docker Compose 开发环境

以下是开发基线，Codex 必须根据现有 Compose 合并，不能无条件覆盖已有服务：

```yaml
services:
  postgres:
    image: ${POSTGRES_IMAGE:-postgres:18}
    environment:
      POSTGRES_DB: xquant_sentinel
      POSTGRES_USER: xqs_app
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?required}
    volumes:
      - postgres_data:/var/lib/postgresql
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U xqs_app -d xquant_sentinel"]
      interval: 5s
      timeout: 5s
      retries: 20
    restart: unless-stopped

  redis:
    image: ${REDIS_IMAGE:-redis:8}
    command:
      - redis-server
      - --appendonly
      - "yes"
      - --appendfsync
      - everysec
      - --requirepass
      - ${REDIS_PASSWORD:?required}
    volumes:
      - redis_data:/data
    healthcheck:
      test: ["CMD-SHELL", "redis-cli -a \"$$REDIS_PASSWORD\" ping | grep PONG"]
      interval: 5s
      timeout: 5s
      retries: 20
    environment:
      REDIS_PASSWORD: ${REDIS_PASSWORD:?required}
    restart: unless-stopped

  influxdb3-core:
    image: ${INFLUXDB_IMAGE:-influxdb:3-core}
    command:
      - influxdb3
      - serve
      - --node-id=node0
      - --object-store=file
      - --data-dir=/var/lib/influxdb3/data
      - --plugin-dir=/var/lib/influxdb3/plugins
    volumes:
      - influxdb_data:/var/lib/influxdb3
    ports:
      - "127.0.0.1:8181:8181"
    restart: unless-stopped

volumes:
  postgres_data:
  redis_data:
  influxdb_data:
```

注意：

1. PostgreSQL 18 官方镜像的数据目录规则与旧版本不同，卷应挂载到 `/var/lib/postgresql`；
2. 开发环境可暴露本机端口，生产环境禁止数据库直接暴露公网；
3. InfluxDB 首次启动后使用 CLI 创建 Token，并放入密钥管理系统；
4. Compose 中的 Redis healthcheck 需要保证环境变量能被容器内命令读取，Codex 必须实际运行验证；
5. 生产部署前生成 `deploy/image-lock.env`，写入具体补丁版本或 digest；
6. 禁止把真实密码提交到 Git。

---

## 12. 数据迁移程序设计

### 12.1 命令接口

统一提供一个可重入 CLI：

```bash
python -m scripts.migrate_sqlite inspect \
  --sqlite ./data/app.db \
  --output artifacts/database

python -m scripts.migrate_sqlite bootstrap \
  --target postgres,influx

python -m scripts.migrate_sqlite migrate-postgres \
  --sqlite ./data/app.db \
  --resume

python -m scripts.migrate_sqlite migrate-influx \
  --sqlite ./data/app.db \
  --resume

python -m scripts.migrate_sqlite verify \
  --sqlite ./data/app.db \
  --report docs/database/数据迁移验证报告.md

python -m scripts.migrate_sqlite status
```

必须支持：

```text
--dry-run
--resume
--batch-size
--table
--from-id / --to-id
--from-time / --to-time
--max-errors
--fail-fast
--report
```

### 12.2 迁移批次表

`migration.migration_batch`：

```text
id
source_database_sha256
source_table
target_store
target_object
status
started_at
finished_at
last_source_key
rows_read
rows_written
rows_skipped
rows_failed
source_checksum
target_checksum
error_summary
code_commit
```

同一 SQLite 文件哈希、源表、目标对象和迁移代码版本构成迁移身份。脚本重启后从 `last_source_key` 恢复。

### 12.3 旧 ID 映射

`migration.legacy_id_map`：

```text
entity_type
legacy_table
legacy_id
new_id
source_database_sha256
created_at
```

唯一约束：

```text
(source_database_sha256, legacy_table, legacy_id)
```

父表导入完成后再导入子表，所有外键通过映射表转换。禁止假设 SQLite 整数 ID 可以直接作为新系统永久 ID，除非盘点后明确决定保留且有测试证明无冲突。

### 12.4 PostgreSQL 导入策略

- 小表可使用 SQLAlchemy 批量插入；
- 大表优先使用 PostgreSQL `COPY` 或驱动批量接口；
- 每批独立事务；
- 每批提交后写检查点；
- 使用确定性 `ON CONFLICT`；
- 失败行进入 `migration.migration_row_error`；
- 错误率超过阈值立即停止；
- 禁止用一笔超大事务导入全库；
- 禁止导入时关闭全部约束后不做复核。

### 12.5 InfluxDB 导入策略

- 按表、证券、周期、日期分片；
- 将 SQLite 时间转换为 UTC 纳秒；
- 使用确定性 tags 和 timestamp；
- 每批保存源范围和点数；
- 失败可单批重试；
- 导入完成后按证券/周期/交易日做聚合校验；
- K 线至少校验 `high >= max(open, close, low)`、`low <= min(open, close, high)`、`volume >= 0`；
- 发现相同自然键但 OHLCV 不同的数据时不得自动覆盖，必须生成冲突报告。

### 12.6 时间转换规则

迁移脚本不得通过字符串长度盲目判断秒/毫秒/微秒。必须：

1. 根据表定义和调用代码确定语义；
2. 对数值时间使用合理年份范围验证；
3. 保存原始值到迁移审计或错误记录；
4. 对无时区本地时间明确指定原市场时区；
5. 夏令时市场出现歧义时间时使用交易所日历或供应商原始 offset；
6. 迁移报告列出每张表的时间解释规则。

### 12.7 异常数据策略

| 异常 | 默认处理 |
|---|---|
| 非法 JSON | 记录错误；关键业务表停止，非关键扩展字段可保留原字符串 |
| 非法金额/价格 | 停止该表迁移，不得置零 |
| 缺失外键 | 写错误表并停止关键表 |
| 重复自然键且内容相同 | 幂等跳过 |
| 重复自然键但内容不同 | 生成冲突报告并停止 |
| 非法时间 | 写错误表；关键记录停止 |
| 证券代码无法映射 | 进入隔离表，禁止丢弃 |
| 空字符串与 NULL 混用 | 按字段规则转换并计数 |

---

## 13. 验证方案

### 13.1 结构校验

- PostgreSQL 所有目标表、索引、唯一约束、外键存在；
- Alembic 版本为 `head`；
- Model 与 Migration 无漂移；
- InfluxDB 所需表和字段类型已显式建立或通过受控初始化创建；
- Redis key 前缀和 TTL 配置生效；
- 生产环境无 SQLite 写连接。

### 13.2 PostgreSQL 数据校验

每张表至少比较：

```text
源行数
成功行数
跳过行数
失败行数
主键唯一数
自然键唯一数
NULL 分布
数值求和/最小值/最大值
时间最小值/最大值
规范化行哈希
外键孤儿数
```

规范化哈希规则必须固定：

- 列按定义顺序；
- 时间统一 UTC ISO-8601；
- Decimal 固定量化；
- JSON key 排序；
- `NULL` 使用固定标记；
- Unicode 使用 NFC；
- 不把新系统生成的 ID 纳入源目标内容哈希。

### 13.3 InfluxDB 数据校验

按以下维度聚合对比：

```text
instrument_id + interval + trading_date
instrument_id + source + hour（Tick）
factor_id + instrument_id + trading_date
portfolio_id + trading_date（净值）
```

校验项：

- 点数；
- 最早/最晚时间；
- OHLCV 聚合；
- volume/amount 总和；
- 重复时序键；
- 缺口；
- 随机抽样逐点比较；
- 迟到数据和乱序数据；
- 复权类型是否一致。

### 13.4 Redis 校验

Redis 不做“迁移总行数相等”要求，只验证：

- 可通过 PostgreSQL/InfluxDB 重新预热；
- 所有普通缓存有 TTL；
- Stream 消费组可工作；
- 重复事件不会重复触发业务结果；
- Redis 清空后系统能够恢复正确结果；
- Redis 不可用时行为符合降级矩阵。

### 13.5 量化策略回归

这是本项目最重要的验收项。对阶段 0 保存的代表性数据集执行：

```text
同一策略版本
同一参数集
同一数据时间窗口
同一复权方式
同一随机种子
同一交易成本和滑点配置
```

比较：

- 输入 K 线数量与顺序；
- 因子值；
- 买卖信号时间与方向；
- 目标价格、止损、止盈；
- 持仓变化；
- 回测净值；
- 收益率、最大回撤、胜率、交易次数；
- AI 分析结构化输出中的关键字段。

容差要求：

- 离散信号必须完全一致；
- Decimal 账务结果必须完全一致；
- 浮点技术指标使用明确的绝对/相对误差，默认不高于 `1e-10`，若原算法或库版本导致差异，必须记录原因；
- 不允许用放宽容差掩盖排序、时区、缺失数据或复权错误。

### 13.6 业务冒烟测试

至少覆盖：

1. 用户或本地身份初始化；
2. 新增/删除自选股；
3. 创建组合和持仓；
4. 新增策略和策略版本；
5. 执行一次策略分析；
6. 写入并读取信号；
7. 查询 K 线窗口；
8. 运行小型回测；
9. 生成通知事件；
10. 服务重启后状态恢复；
11. 清空 Redis 后恢复；
12. 暂停 InfluxDB 后行情接口明确报错/降级；
13. 暂停 PostgreSQL 后业务写入停止；
14. 重复提交相同请求不会产生重复订单、信号或通知。

---

## 14. 分阶段改造任务

### DB-00：现状盘点与冻结基线

**目标**：完整发现 SQLite 和其他持久化方式，不改业务行为。

**工作内容**：

- 执行代码搜索；
- 建立 SQLite 只读检查脚本；
- 生成表清单、调用图和存储映射；
- 保存测试和策略回归基线；
- 标记所有 SQLite 方言专属 SQL；
- 识别是否存在数据库初始化时自动建表。

**交付物**：

```text
docs/database/SQLite现状盘点.md
docs/database/SQLite表到目标存储映射.md
artifacts/database/sqlite_inventory.json
artifacts/database/sqlite_checksums.json
```

**验收**：所有现有表和读写入口均可追溯。

---

### DB-01：基础设施与配置

**目标**：本地一键启动 PostgreSQL、Redis、InfluxDB，应用暂不切换。

**工作内容**：

- 合并 Docker Compose；
- 增加 `.env.example`；
- 增加 Pydantic StorageSettings；
- 增加三个客户端工厂和关闭逻辑；
- 增加健康检查；
- 增加密钥脱敏；
- 更新依赖锁文件。

**验收**：

```bash
docker compose up -d
# PostgreSQL、Redis、InfluxDB 均通过实际连接测试
pytest -m integration tests/integration/storage
```

---

### DB-02：持久化边界与兼容适配器

**目标**：业务层不再直接依赖 SQLite。

**工作内容**：

- 抽取 Repository、MarketDataRepository、CachePort、UnitOfWork；
- 将现有 SQLite 访问包装成 `LegacySQLite*Adapter`；
- 保持现有功能和测试不变；
- 禁止业务层新建 SQLite 连接；
- 新增架构测试，阻止领域层导入数据库客户端。

**验收**：全部旧测试通过，默认仍可在测试配置下运行 Legacy 适配器。

---

### DB-03：PostgreSQL 模型与 Alembic

**目标**：建立业务事实库。

**工作内容**：

- 按映射表实现 SQLAlchemy Model；
- 建立命名约定和 Schema；
- 生成并人工检查 Alembic 初始迁移；
- 实现 PostgreSQL Repository 与 UnitOfWork；
- 实现 Outbox 表和 Worker 基础；
- 编写唯一约束、并发和事务测试。

**验收**：

```bash
alembic upgrade head
alembic check
pytest tests/integration/postgres
```

---

### DB-04：SQLite → PostgreSQL 数据迁移

**目标**：完整导入业务数据，并可重跑。

**工作内容**：

- 实现迁移批次、旧 ID 映射和错误表；
- 按依赖顺序导入；
- 实现批处理、检查点、dry-run 和 resume；
- 生成数量、哈希和外键报告；
- 对冲突数据停止并输出报告；
- 不切换应用默认数据库。

**验收**：同一 SQLite 副本连续执行两次，第二次不得产生重复记录；验证报告通过。

---

### DB-05：SQLite → InfluxDB 时序迁移

**目标**：把 K 线、Tick、因子、指标和净值时序迁入 InfluxDB。

**工作内容**：

- 根据实际表建立 Influx schema；
- 实现时间和标签映射；
- 处理同时间多 Tick；
- 批量导入、重试和错误隔离；
- 实现 Influx MarketDataRepository；
- 完成按证券/周期/日期的校验；
- 保持策略层 DTO 不变。

**验收**：代表性策略读取 InfluxDB 后，输入窗口和信号与基线一致。

---

### DB-06：Redis 缓存、锁、限流和 Streams

**目标**：增加实时性能和服务协调，不改变事实数据归属。

**工作内容**：

- 实现 KeyBuilder 和统一序列化；
- 实现 Cache-Aside；
- 实现锁与续租/超时策略；
- 实现供应商限流；
- 实现 Outbox → Redis Stream；
- 实现消费组、重试、pending 回收和死信；
- 实现缓存预热脚本。

**验收**：清空 Redis 后结果仍正确；重复消费不会重复产生业务记录。

---

### DB-07：应用切换

**目标**：PostgreSQL 成为默认业务数据库，InfluxDB 成为默认时序库，Redis 启用。

**工作内容**：

- `STORAGE_BACKEND` 默认改为 `postgres`；
- 禁止生产 SQLite 回退；
- 旧 SQLite Adapter 仅供迁移和测试；
- 更新启动、安装和部署文档；
- 执行全量迁移与验证；
- 执行业务冒烟和策略回归；
- 生成切换记录。

**验收**：正常启动过程不打开 SQLite 写连接；所有核心流程通过。

---

### DB-08：可观测性、备份和故障演练

**目标**：数据库问题可发现、可恢复。

**工作内容**：

- PostgreSQL 慢查询、连接池、事务失败指标；
- Redis 命中率、内存、Stream lag、pending 和死信指标；
- InfluxDB 写入延迟、拒绝点、查询延迟和缺口指标；
- 备份脚本与恢复说明；
- Redis 清空、InfluxDB 停机、PostgreSQL 重启演练；
- 迁移报告和审计日志归档。

**验收**：完成一次从备份恢复到隔离环境的演练并留存报告。

---

### DB-09：SQLite 退役

**目标**：删除生产依赖，但保留可审计的只读归档。

**工作内容**：

- 删除生产启动路径中的 SQLite 初始化；
- 删除不再使用的 SQLite 方言 SQL；
- 保留 `legacy_sqlite` 只读工具和迁移测试 fixture；
- SQLite 原文件改名并设只读权限；
- 记录 SHA-256、迁移批次和归档位置；
- 观察期后再决定是否移出主部署目录。

**验收**：生产依赖扫描中不存在 `sqlite3`/`aiosqlite` 运行时依赖；测试 fixture 除外。

---

## 15. 正式切换 Runbook

### 15.1 切换前

- [ ] 代码冻结；
- [ ] 全部 CI 通过；
- [ ] 在 SQLite 副本完成至少两次迁移演练；
- [ ] 迁移时间和容量已测量；
- [ ] PostgreSQL、Redis、InfluxDB 备份策略已配置；
- [ ] 生成镜像版本锁；
- [ ] 准备旧版本应用包和 SQLite 只读副本；
- [ ] 明确切换负责人和回滚触发条件。

### 15.2 切换步骤

```text
1. 停止 API、定时任务、行情采集、策略 Worker、通知 Worker
2. 确认 SQLite 无写连接
3. 执行 PRAGMA integrity_check
4. 复制 SQLite 文件到带时间戳的归档目录
5. 计算并记录 SHA-256
6. PostgreSQL 执行 alembic upgrade head
7. 创建/验证 InfluxDB 数据库和 Token
8. 执行 migrate-postgres --resume
9. 执行 migrate-influx --resume
10. 执行 verify，要求阻断项为 0
11. 设置 STORAGE_BACKEND=postgres
12. 启动 API，执行只读冒烟
13. 启动 Outbox/通知 Worker
14. 启动行情采集和策略 Worker
15. 执行完整业务冒烟和策略回归
16. 观察错误率、延迟、连接池和数据写入
17. 将 SQLite 原库设置为只读并移出运行时默认路径
```

### 15.3 回滚触发条件

出现以下任一项立即停止新写入并进入回滚评估：

- 迁移验证存在未解释的行数差异；
- 持仓、资金、订单、信号关键表存在冲突；
- 代表性策略信号不一致；
- 时间范围或交易日发生系统性偏移；
- InfluxDB 出现无法解释的点覆盖或缺口；
- PostgreSQL 约束导致核心流程持续失败；
- 关键业务错误率超过预设阈值。

### 15.4 回滚方式

推荐在切换验证阶段保持系统写入冻结。一旦开始接受 PostgreSQL 新写入，直接切回旧 SQLite 会丢失新数据，因此必须采用以下之一：

1. 在首次开放写入前完成所有验证，失败直接回旧应用和原 SQLite；
2. 开放写入后失败，先停止所有写入，将 PostgreSQL 新增数据导出为增量补偿包，再决定回滚；
3. 不允许同时让新旧系统对同一业务对象继续写入。

SQLite 归档至少保留 30 天，观察期内不得删除。

---

## 16. 测试与 CI 要求

### 16.1 单元测试

- Repository 接口契约；
- SQLite 类型转换；
- 时间和时区转换；
- Decimal 精度；
- 幂等键；
- Redis KeyBuilder；
- Influx 行协议/点构造；
- 错误分类与重试策略；
- Outbox 状态机。

### 16.2 集成测试

使用真实 PostgreSQL、Redis、InfluxDB 容器，不允许只用 Mock 宣称迁移完成。

```text
pytest -m integration tests/integration/postgres
pytest -m integration tests/integration/redis
pytest -m integration tests/integration/influx
pytest tests/migration
```

### 16.3 迁移 Fixture

至少准备：

1. 正常小型 SQLite；
2. 空库；
3. 含非法 JSON；
4. 含重复自然键；
5. 含孤儿外键；
6. 含秒/毫秒混淆风险时间；
7. 同一时间多 Tick；
8. 中文、Emoji、特殊符号；
9. 大 Decimal；
10. 旧版本 Schema。

### 16.4 CI 阻断项

- lint/type check 失败；
- 单元或集成测试失败；
- `alembic check` 失败；
- 从空 PostgreSQL 无法升级到 head；
- 迁移 Fixture 校验不一致；
- 领域层导入具体数据库驱动；
- 生产配置存在 SQLite fallback；
- 普通 Redis 缓存缺少 TTL；
- 日志泄露密码或 Token。

---

## 17. 可观测性

最低指标：

### PostgreSQL

```text
xqs_db_pool_checked_out
xqs_db_pool_overflow
xqs_db_query_duration_seconds
xqs_db_transaction_rollback_total
xqs_db_deadlock_total
xqs_outbox_unpublished_total
xqs_outbox_oldest_age_seconds
```

### Redis

```text
xqs_redis_cache_hit_total
xqs_redis_cache_miss_total
xqs_redis_command_duration_seconds
xqs_redis_stream_pending_total
xqs_redis_stream_lag
xqs_redis_dead_letter_total
xqs_redis_lock_contention_total
```

### InfluxDB

```text
xqs_influx_write_points_total
xqs_influx_write_failed_points_total
xqs_influx_write_duration_seconds
xqs_influx_query_duration_seconds
xqs_market_data_gap_total
xqs_market_data_late_event_total
```

### 迁移

```text
xqs_migration_rows_read_total
xqs_migration_rows_written_total
xqs_migration_rows_failed_total
xqs_migration_batch_duration_seconds
xqs_migration_checksum_mismatch_total
```

结构化日志至少带：

```text
trace_id
request_id
strategy_run_id
backtest_run_id
instrument_id
migration_batch_id
event_id
```

不得记录：数据库密码、Redis 密码、InfluxDB Token、券商密钥、AI API Key、完整个人隐私数据。

---

## 18. 安全和权限

### PostgreSQL 角色

```text
xqs_owner       仅部署时拥有对象
xqs_app         应用读写，不允许建表
xqs_migrator    Alembic 和数据迁移
xqs_readonly    报表与排查
xqs_backup      备份专用
```

要求：

- 应用角色不得是超级用户；
- 远程连接启用 TLS；
- 生产密码进入密钥管理，不进入 `.env` 文件仓库；
- 审计关键数据变更；
- SQL 查询参数化，禁止字符串拼接。

### Redis

- 使用密码或 ACL 用户；
- 只绑定内网；
- 生产禁止暴露 6379 到公网；
- 为不同服务分配最小命令权限时优先使用 ACL；
- 禁止 `FLUSHALL`、`CONFIG` 等高危命令授予普通应用账号。

### InfluxDB

- 为写入、查询、迁移分别创建 Token；
- Token 只显示一次时立即安全保存；
- 不把管理员 Token 配给普通应用；
- 生产不暴露 8181 到公网；
- 定期轮换并记录 Token 所属服务。

---

## 19. Codex + GLM-5.3-Flash 协作协议

### 19.1 分工

| 角色 | 主要职责 |
|---|---|
| Codex | 主执行者：读取仓库、修改代码、运行命令、编写迁移、修复测试、提交阶段性变更 |
| GLM-5.3-Flash | 长上下文审查者：盘点遗漏、Schema 审查、跨文件一致性检查、迁移风险审查、测试覆盖审查 |

也可让 GLM-5.3-Flash 执行隔离的小任务，但禁止两个 Agent 同时编辑同一组文件。

### 19.2 协作循环

每个 DB 工单执行：

```text
1. Codex 阅读本方案和当前仓库
2. Codex 只实施一个工单
3. Codex 运行测试并生成变更摘要
4. GLM-5.3-Flash 审查 git diff、测试日志和相关文件
5. GLM 输出结构化问题清单
6. Codex 修复 Blocker/Major
7. 再次运行测试
8. 完成该工单后再进入下一阶段
```

### 19.3 GLM-5.3-Flash 调用建议

官方模型标识：

```text
glm-5.3-flash
```

审查复杂迁移时建议：

```json
{
  "temperature": 1,
  "top_p": 0.95,
  "reasoning_effort": "max",
  "thinking": {
    "type": "enabled",
    "clear_thinking": false
  }
}
```

日常小范围代码审查可将 `reasoning_effort` 调整为 `high`，架构、迁移、数据一致性审查使用 `max`。

### 19.4 GLM 审查输出格式

要求 GLM 只输出以下 JSON 结构，便于 Codex 处理：

```json
{
  "verdict": "pass|changes_required|blocked",
  "blockers": [
    {
      "file": "path",
      "line": 0,
      "problem": "问题",
      "risk": "后果",
      "required_fix": "必须修改方式"
    }
  ],
  "major": [],
  "minor": [],
  "missing_tests": [],
  "migration_risks": [],
  "data_integrity_checks": [],
  "recommended_commands": []
}
```

### 19.5 Agent 禁止事项

- 不允许一次性实施 DB-00 到 DB-09；
- 不允许在未盘点时猜表名并删除旧代码；
- 不允许通过跳过测试、删除断言或放宽校验让 CI 变绿；
- 不允许修改量化策略结果来适配迁移后的数据差异；
- 不允许把失败行静默丢弃；
- 不允许使用 SQLite 作为生产 fallback；
- 不允许提交真实凭据；
- 不允许用 Redis 替代 PostgreSQL 事务；
- 不允许通过高基数随机 tag 解决 InfluxDB Tick 冲突；
- 不允许未经审查执行 Alembic 自动生成的 DROP 操作。

---

## 20. 可直接交给 Codex 的总提示词

```text
你正在改造 X-QuantSentinel 的存储层。项目当前默认使用 SQLite，目标为 PostgreSQL + Redis + InfluxDB。

必须完整阅读《SQLite迁移至PG_Redis_InfluxDB改造方案_Codex与GLM-5.3-Flash执行版.md》，严格按 DB-00 到 DB-09 分阶段实施。当前只执行我指定的工单，不得提前改后续阶段。

总规则：
1. 先盘点实际代码和 SQLite Schema，不猜测表结构。
2. SQLite 源文件只读，任何迁移都不得修改原库。
3. PostgreSQL 是业务事实库；InfluxDB 存时序；Redis 只存可重建状态、协调和 Streams。
4. 不做跨库事务；业务事件使用 PostgreSQL Outbox，行情写入使用 InfluxDB + PostgreSQL checkpoint。
5. 保持现有同步/异步模型，不借机重写全项目。
6. 不修改量化策略语义；保存并执行迁移前后策略回归。
7. 所有数据迁移必须幂等、可 resume、有检查点、有失败行记录和验证报告。
8. 生产不得静默回退 SQLite。
9. 禁止删除失败测试、放宽关键断言或忽略迁移错误。
10. 修改前先说明发现，修改后必须运行真实测试并报告结果。

每个工单输出：
- 现状发现；
- 修改文件清单；
- 关键设计决定；
- 执行的命令；
- 测试结果；
- 数据完整性风险；
- 尚未完成事项；
- 建议交给 GLM-5.3-Flash 审查的文件和问题。

从 DB-00 开始：只做现状盘点、SQLite 只读检查工具、存储映射和回归基线。不要切换数据库，不要删除 SQLite 代码。
```

---

## 21. 可直接交给 GLM-5.3-Flash 的审查提示词

```text
你是 X-QuantSentinel 数据库改造的独立审查者。请审查 Codex 对当前 DB 工单的实现。

上下文：系统从 SQLite 迁移到 PostgreSQL + Redis + InfluxDB。PostgreSQL 是业务事实库，InfluxDB 是时序库，Redis 只保存可重建缓存、协调状态和事件流。量化策略结果必须在迁移前后保持一致。

请重点审查：
1. 是否遗漏 SQLite 文件、连接入口、原生 SQL 或隐式持久化；
2. 数据是否被分配到正确存储；
3. 是否存在 Redis 被当作事实库；
4. 是否存在 PostgreSQL、Redis、InfluxDB 跨库伪事务；
5. 迁移是否幂等、可重入、可 resume；
6. 时间、时区、交易日、Decimal、JSON、NULL 和旧 ID 是否正确转换；
7. InfluxDB 标签基数、时间冲突和重复点处理是否正确；
8. Alembic 是否可能误删表、列、约束或数据；
9. 是否有静默 SQLite fallback；
10. 测试是否真实连接三种数据库，是否覆盖迁移失败和故障降级；
11. 量化策略回归是否足以发现排序、复权、缺口和浮点差异；
12. 日志和配置是否泄露凭据。

不要重写代码。输出严格 JSON，格式使用方案中的“GLM 审查输出格式”。Blocker 和 Major 必须给出具体文件、风险、修复要求和缺失测试。
```

---

## 22. 完成定义（Definition of Done）

只有全部满足以下条件，才能宣告 SQLite 改造完成：

- [ ] 生产默认业务数据库为 PostgreSQL；
- [ ] 正常启动路径不创建 SQLite 写连接；
- [ ] 所有现有 SQLite 表都有明确迁移或保留说明；
- [ ] PostgreSQL 业务数据数量、约束、哈希和聚合校验通过；
- [ ] InfluxDB 时序数据点数、时间范围、聚合和抽样校验通过；
- [ ] Redis 清空后系统能够恢复正确结果；
- [ ] 迁移脚本重复执行不会产生重复数据；
- [ ] 迁移可中断并恢复；
- [ ] 失败行和冲突均有记录，关键失败为 0；
- [ ] 量化策略基线回归通过；
- [ ] Outbox、Streams、重试和死信路径通过集成测试；
- [ ] PostgreSQL、Redis、InfluxDB 故障行为符合设计；
- [ ] Alembic 从空库可升级至 head；
- [ ] CI 中不存在被跳过的关键数据库测试；
- [ ] 生产没有 SQLite 静默 fallback；
- [ ] 镜像已锁定具体版本或 digest；
- [ ] 数据库凭据未进入 Git 和日志；
- [ ] 已完成备份恢复演练；
- [ ] SQLite 原库已记录 SHA-256、设置只读并归档；
- [ ] 已生成《数据迁移验证报告》和《数据库运行手册》。

---

## 23. 主要风险清单

| 风险 | 严重度 | 应对 |
|---|---:|---|
| SQLite 弱类型导致脏数据进入 PG | 高 | 扫描实际类型分布；严格转换；错误隔离 |
| 时间单位或时区误判 | 高 | 每表明确规则；保存原值；策略回归 |
| REAL 金额迁移产生精度误差 | 高 | Decimal 字符串解析；NUMERIC；聚合核对 |
| 旧 ID 冲突或外键断裂 | 高 | legacy_id_map；父子顺序；孤儿检查 |
| Influx 同时间点覆盖 Tick | 高 | 纳秒时间 + source_seq 确定性映射 |
| Influx tag 高基数 | 高 | 禁止随机事件 ID tag；压测 Schema |
| Redis 被误用为最终状态 | 高 | Repository 边界；清空 Redis 恢复测试 |
| 跨库部分成功 | 高 | Outbox、checkpoint、幂等重放 |
| Alembic 自动生成误删数据 | 高 | 手工审查；禁止自动执行 DROP |
| 长期双写产生漂移 | 高 | 当前采用停机迁移；不启用长期双写 |
| 策略结果因排序/缺口变化 | 高 | 输入窗口、因子、信号全链路回归 |
| 连接池配置不当 | 中 | 指标、压测、按服务设置连接数 |
| Redis Stream 无限增长 | 中 | MAXLEN、消费组、死信和监控 |
| 容器 latest 大版本漂移 | 高 | 版本/digest 锁定 |
| 回滚后丢失新写入 | 高 | 开放写入前完成验证；增量补偿包 |

---

## 24. 参考资料

- PostgreSQL 当前文档与版本：<https://www.postgresql.org/docs/current/>
- PostgreSQL 发布说明：<https://www.postgresql.org/docs/release/>
- Alembic 文档：<https://alembic.sqlalchemy.org/>
- Alembic 自动生成限制：<https://alembic.sqlalchemy.org/en/latest/autogenerate.html>
- Redis Python asyncio 示例：<https://redis.readthedocs.io/en/stable/examples/asyncio_examples.html>
- InfluxDB 3 Core 安装：<https://docs.influxdata.com/influxdb3/core/install/>
- InfluxDB 3 Core 设置：<https://docs.influxdata.com/influxdb3/core/get-started/setup/>
- InfluxDB 3 Python Client：<https://docs.influxdata.com/influxdb3/core/tags/python/>
- GLM-5.3-Flash 官方说明：<https://docs.z.ai/guides/vlm/glm-5.3-flash>

---

## 25. 第一条执行指令

将本文件放入项目根目录后，向 Codex 下达：

```text
读取本方案，只执行 DB-00。先扫描仓库与 SQLite，不改默认数据库，不删除旧代码。完成后提交 SQLite 现状盘点、目标存储映射、迁移风险清单、代表性策略回归基线和实际测试日志，等待 GLM-5.3-Flash 审查。
```
