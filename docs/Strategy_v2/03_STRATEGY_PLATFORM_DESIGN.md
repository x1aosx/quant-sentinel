# X-Quant v2：本地量化策略管理系统设计

> 版本：2.0；日期：2026-09-08。  
> 产品定位：**本地策略研究、验证、部署与复盘工作台**。股票页面是策略的观察界面，不再是产品主线。  
> 首批内核：SR 关键位检测、PA 确认状态机、策略/账户风控、事件回放、策略版本与实验管理。  
> 详细规则以 `02_SRPA_STRATEGY_SPEC.md` 为准，来源证据以 `01_SOURCE_AUDIT.md` 为准。

## 1. 与上一版设计的关系

本版本替代旧 `X-Stock_Design.md` 中的**产品优先级、默认部署方式、策略研究顺序和 Codex 阶段计划**。旧文档中数据授权、重试限流、账本完整性、不可伪造预测、用户确认等原则仍有效，但发生冲突时以本交付包为准。

| 上一版重点 | v2 调整 |
|---|---|
| 持仓/自选网站先完成，策略与验证后续增加 | 先让策略在本地正确回放，再实现管理界面 |
| 策略接近几个分析指标 | 策略是有版本、状态、入场、退出、风险、执行和验证记录的完整资产 |
| 参考两个仓库的工作流 | 逐条提取策略与预测逻辑，区分代码、主观规则、统计模型和未经验证假设 |
| 默认服务端部署、多个基础服务 | 本地进程优先，轻量数据库与文件快照，数据库服务/队列按需扩展 |
| 股票分析报告是主要结果 | 核心结果是可追溯策略计划、样本外证据、真实执行差异与迭代决策 |

### 1.1 核心使用闭环

```text
发现/登记策略 → 审查与固定版本 → 规格化 → 合成测试 → 回放回测
            → 样本外与消融 → 仿真/影子 → 人工批准辅助运行
            → 实际成交/忽略/拒绝 → 归因复盘 → 新版本研究
```

不得自动跳过“验证”从互联网代码直接进入持仓提醒。不得用“最新收益最高”自动替换正式版本。自动化的是研究任务和证据收集，资金相关部署仍需要明确批准。

## 2. 本地优先架构

### 2.1 默认技术选型

| 层 | 方案 | 设计理由 |
|---|---|---|
| 策略计算 | Python 纯领域包，NumPy/Pandas 或 Polars，明确浮点/Decimal 边界 | 与网页和数据源独立，可直接测试与批处理 |
| API/协调器 | FastAPI + 单个 LocalCoordinator | 同一业务命令用于网页/CLI；统一调度和账本写入 |
| 网页 | React + TypeScript + Vite 构建 SPA，构建产物由 API 提供 | 不需要默认 SSR 服务；本地同源访问 |
| 元数据与账本 | SQLite WAL、迁移脚本、事务与备份 | 个人本地负载起点；协调器串行写入，避免到处写库 |
| 行情/特征 | Parquet 不可变分区 + DuckDB 只读分析 | 大批量历史查询不占用业务事务表 |
| 作业 | SQLite 持久任务 + 受控子进程 + IPC 结果返回 | 首版不强制 Redis/Celery；崩溃后可恢复 |
| 工件 | 本地内容寻址目录：哈希、版本清单、只读数据快照 | 数据/模型/报告可追溯，不覆盖上次实验 |
| 图表 | 浏览器 K 线与曲线组件，可替换 | 图表只能展示事实与记录，不能承载策略逻辑 |
| 可选研究工具 | Qlib/MLflow 适配器 | 参考其训练、工件与版本思想，不强制整个平台依赖 [R22–R24][R27] |

初期不默认 Kubernetes、Kafka、微服务、分布式特征库或多个回测框架并存。需要远程多用户或吞吐扩大时，再通过既有 Repository/JobQueue 接口替换为 PostgreSQL 与外部任务服务。

### 2.2 进程和数据流

```text
本地浏览器 ──同源 HTTP──┐
本地 CLI ──────────────┼─> LocalCoordinator / API（唯一业务写入协调点）
                      │        ├─ Strategy Registry / Risk / Ledger
                      │        ├─ SQLite：版本、作业、事件、账本、审批、Outbox
                      │        ├─ Scheduler：市场日历、预算、恢复策略
                      │        └─ 子进程任务池（输入快照→纯计算→结果）
                      │                    └─ 只读 Parquet / 特征 / 模型
                      └─> 页面只读查询缓存

授权 Provider → DataHub → 暂存文件 → 校验 → 原子发布快照
业务事件 → Outbox → 通知适配器 → 投递状态（不等于成交状态）
真实成交导入 → 幂等校验/对账 → 账户账本 → 策略归因
```

策略计算子进程不直接写账户账本或业务数据库，不读取消息密钥。Coordinator 接收结构化结果，在事务中保存事件、状态检查点与 outbox。长回测不得占据实时持仓监控队列。

Parquet 发布采用临时路径写完、校验、生成 manifest、原子替换 manifest 指针；历史 manifest 指向的文件不被原地改写。DuckDB 分析连接只读取指定快照，不在并发子进程中修改同一个数据库文件。文件目录与 SQLite 元数据发布需采用可恢复的两阶段业务流程，残留暂存文件可回收。

### 2.3 本地运行模式

`research`：只读行情与模拟账户，不外发、不碰真实账本。  
`paper`：按真实时钟跑计划，但成交进入仿真账本；可发明确标识的测试消息。  
`live_assist`：通过批准的实例，输出人工可核对计划；只在实际成交导入后记真实持仓。  
`demo`：合成证券和行情，界面水印，不能发成真实投资提醒。

上述模式的数据库命名空间、工件目录和通知策略独立。旧实验无法因切换一个全局开关变成真实交易实例。

## 3. 策略管理领域模型

### 3.1 不能混为一谈的八个对象

| 对象 | 例子 | 关键属性 |
|---|---|---|
| Source | 一个 GitHub 仓库/一份研究资料 | URL、SHA、文件路径、许可、审查结论 |
| Component | 区域检测器、H2 状态机、ATR 风险政策 | 输入输出 schema、版本、测试、依赖 |
| StrategyDefinition | 突破回测做多这一策略族 | 假设、适用市场、参数 schema、所有者、标签 |
| StrategyVersion | `0.1.0 + config_hash` | 不可变代码/参数/标签/执行模型、依赖版本 |
| Experiment | 某次数据快照上的比较回测 | 输入、尝试参数、结果、状态、证据等级 |
| ModelArtifact | 一个概率/收益模型文件 | 训练截止、特征、标签、校准、适用范围、文件哈希 |
| StrategyInstance | 将某版本绑定账户/股票池运行 | 模式、预算、时间表、部署状态、版本钉住 |
| Decision/TradePlan | 某日某股的计划 | 时点、依据、风险预留、有效期、实际执行反馈 |

参数改动产生新 StrategyVersion，而不是更新同一个历史对象。实例可以切版本，但已存在的计划和持仓不自动重绑。模型文件换了，即使策略源代码没变，也构成新的部署版本。

### 3.2 策略包规范

每个策略包必须有 `manifest.yaml, parameters.schema.json, strategy.py, tests/, strategy_card.md`。复杂策略增加 `labels.py`、`features.py`、`state.schema.json`。manifest 至少声明：

```text
strategy_id, version, engine_api_version, source_refs, license_review
entrypoint, side, supported_markets, frequency, warmup_sessions
feature_schema_version, state_schema_version, required_capabilities
default_execution_profile, parameters, risk_policy_ref
lookahead_tests, state_transition_tests, benchmark_refs
```

manifest 和参数 schema 仅描述受支持的条件，不允许输入任意字符串表达式后 `eval`。参数严格类型、范围、互斥和依赖校验；数值优化范围必须显式设定，不接受无限制自动搜索。

### 3.3 状态与审批

版本的研究生命周期：

```text
DRAFT → SPECIFIED → UNIT_VERIFIED → BACKTESTED → OOS_PASSED
                                                ↓
                                   PAPER_VERIFIED → APPROVED_LIVE_ASSIST

任意阶段可标记 INCONCLUSIVE / REJECTED / ARCHIVED；不能擦除旧证据。
```

实例的运行状态：`STOPPED / RUNNING / PAUSED_ENTRIES / EXIT_ONLY / ERROR / DISABLED`。

| 操作 | 精确定义 |
|---|---|
| 暂停新增 | 不再生成新的买入计划；已有持仓风控继续 |
| 只出不进 | 取消未成交新增计划，继续生成已持仓退出提醒 |
| 停止实例 | 停止策略主动计算，但把持仓移交独立 RiskGuardian 监控，并提示退出逻辑降级 |
| 禁用版本 | 禁止新部署与新开仓；旧持仓必须保留可读的原始风险规则 |
| 回滚 | 未来新信号改用已批准旧版本；不会删除已成交交易或重写过去绩效 |
| 退役 | 无未结计划/受管持仓，或完成显式接管后归档 |
| 一键退出 | 生成待确认退出操作，不直接把仓位清零；无券商连接不得写“已平仓” |

个人单用户可以自己审批，但仍需记录是谁、何时、基于哪些报告、哪种执行口径和风险限额批准。批准时版本哈希与报告对应关系必须校验。

## 4. 策略管理功能清单

### 4.1 第一优先级：策略资产、参数、实验

| 功能 | 具体行为 | 实施阶段 |
|---|---|---|
| 来源登记 | GitHub/论文/资料链接、路径、SHA、许可和审查说明 | P0 |
| 策略卡片 | 交易假设、市场/周期、入场/退出、风险、证据等级 | P1 |
| 策略创建/复制 | 从模板建立新策略或克隆为研究分支 | P1 |
| 参数管理 | schema 表单、范围验证、导入/导出 YAML、参数差异 | P1 CLI，P3 网页 |
| 版本管理 | 不可变版本、代码/参数/特征/模型 diff、来源升级提醒 | P1–P3 |
| 依赖检查 | 缺字段/窗口不足/交易规则未知时拒绝启动 | P1 |
| 单策略回放 | 逐棒查看状态、信号与权益变化 | P1–P2 |
| 批量回测 | 明确日期/股票池/参数网格与任务预算 | P2 |
| 基线比较 | 相同数据与执行成本下并排比较 | P2 |
| 消融 | 删除 SR/RS/状态闸门/模型层形成独立实验 | P2 |
| 防泄漏检查 | 前缀一致、污染未来、标签成熟、枢轴确认 | P1–P2 |
| 样本外管理 | 时间段锁定、purge、实验尝试审计、防测试集复用 | P2 |
| 研究报告 | 成本后曲线、回撤、交易分布、拒绝信号、置信区间 | P2–P3 |
| 暂无交易诊断 | 逐层过滤漏斗，显示哪个条件阻止了多少候选 | P2–P3 |

### 4.2 第二优先级：部署、组合与复盘

| 功能 | 具体行为 | 实施阶段 |
|---|---|---|
| 股票池绑定 | 全市场/行业/价格/自选/组合持仓；保存每日点时快照 | P2–P3 |
| 实例预算 | 按账户设置最大资金、风险、行业/证券限额 | P2–P3 |
| 仿真部署 | 实时同代码、仿真账本、与历史回放对照 | P3 |
| 启停与回滚 | 明确新增/退出/已有持仓处理，不含糊 | P3 |
| 信号订阅 | 某策略、某组合或某股票；级别和通知时段可配 | P3 |
| 提醒闭环 | 观察/可核对计划/退出/失效，确认、忽略、过期 | P3 |
| 持仓接管 | 既有手工仓位建立独立接管记录与政策 | P3 |
| 执行偏差 | 参考计划与真实成交的价格、数量、时间差 | P3–P4 |
| 逐笔复盘 | 信号当时图表、状态轨迹、数据版本、取消/退出原因 | P3–P4 |
| 组合归因 | 策略、行业、市场状态、成本与执行偏差分别贡献 | P4 |
| 健康监控 | 数据老化、任务延迟、异常换手、回撤、模型漂移 | P3–P4 |
| 模型注册 | 特征/标签/校准版本、训练截止、失效/回滚 | P4 |
| Champion/Challenger | 新版本旁路观察，不夺取旧版本预算 | P4 |
| 导出/迁移 | 策略包、实验报告、JSON/CSV/Parquet 和可恢复备份 | P3–P4 |

### 4.3 后续增强，不阻塞首个可用策略

可视化规则组合器只能组合已注册的安全节点，保存的结果必须仍可导出、版本化、测试。支持研究沙箱、成本敏感性、策略相关性、风险预算组合与再训练候选，但不允许“每晚自动选择最赚钱参数然后上线”。外部自动交易连接作为单独产品阶段，须重新设计券商订单状态和独立安全开关。

## 5. 领域接口：同一策略不认识网页和数据供应商

以下为待实现契约，不是声称已有可运行库。跨包数据结构必须有类型和 schema 版本。

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Mapping, Protocol, Sequence

@dataclass(frozen=True)
class EvaluationContext:
    event_kind: str
    decision_at: datetime
    snapshot_id: str
    calendar_version: str
    execution_profile: str
    state: Mapping[str, object]
    position_view: Mapping[str, object]
    features: Mapping[str, object]

@dataclass(frozen=True)
class StrategyIntent:
    instrument_id: str
    action: str  # WATCH, PROPOSE_ENTRY, PROPOSE_EXIT, CANCEL_PLAN, ABSTAIN
    reason_codes: tuple[str, ...]
    payload: Mapping[str, object]

@dataclass(frozen=True)
class EvaluationResult:
    next_state: Mapping[str, object]
    intents: Sequence[StrategyIntent]
    trace: Sequence[Mapping[str, object]]

class Strategy(Protocol):
    def evaluate(self, ctx: EvaluationContext) -> EvaluationResult: ...

class RiskPolicy(Protocol):
    def assess(self, intent: StrategyIntent,
               account_snapshot: Mapping[str, object]) -> Mapping[str, object]: ...
```

`Strategy.evaluate` 只能消费传入的时间切片与状态，不能调用系统当前时间、外网、通知或账户写入。输入状态视为不可变；输出下一状态由引擎原子保存。金钱/成交价通过 Decimal/定点类型进入账本，特征计算可使用浮点，序列化精度与比较容差需要固定。

Provider 返回标准化数据而不是直接生成买卖决策。ForecastModel 返回有目标定义的 Forecast；它不能私自下单。NotificationAdapter 只投递已落库消息，不能从推送成功推导“成交”。

### 5.1 事件链

```text
BarsPublished → FeatureBatchReady → StrategyEvaluated
→ CandidateBatchRanked → RiskReserved → PlanPublished
→ UserAcknowledged / PlanExpired / PlanCancelled
→ ExecutionImported → LedgerUpdated → PositionReviewed
→ ExitPlanPublished → ExecutionImported → AttributionReady
```

对同一候选批次，先完成统一数据截面的全部候选，再按固定排序分配风险；不能让最快返回的线程抢走所有预算。持仓退出比新仓扫描优先，不能等全市场训练完成才处理止损提醒。

## 6. 数据库与工件结构

### 6.1 最小表集合

| 组 | 表 | 不可省略的约束 |
|---|---|---|
| 来源 | `sources, source_snapshots, source_reviews` | 快照唯一 SHA/哈希；审查记录 append-only |
| 策略 | `strategy_definitions, strategy_versions, component_versions` | `(strategy_id,version)` 唯一，已冻结版本不可 UPDATE 内容 |
| 参数 | `parameter_sets, search_spaces` | 内容哈希、范围、策略兼容性、实验预算 |
| 数据 | `data_snapshots, universe_snapshots, artifact_manifests` | immutable manifest、PIT 标记、能力与质量标记 |
| 研究 | `experiments, experiment_trials, evaluations, model_artifacts` | 所有尝试入库，训练/验证/测试清单与代码哈希 |
| 部署 | `strategy_instances, deployments, approvals` | 实例钉版本；批准必须匹配报告/版本 |
| 运行 | `jobs, checkpoints, event_log` | 作业幂等键、租约/尝试号、顺序号与恢复点 |
| 决策 | `signals, decision_traces, trade_plans, risk_reservations` | 批次唯一、有效期、拒绝原因；冻结额度可对账 |
| 账户 | `accounts, ledger_events, positions, position_lots, execution_imports` | 成交幂等；持仓由账本投影；真实/仿真隔离 |
| 通知 | `subscriptions, notification_outbox, deliveries, user_feedback` | 业务去重键、投递重试、过期/撤销与幂等确认 |
| 监控 | `health_events, drift_reports, audit_log` | 时间和对象可追溯，敏感字段脱敏 |

特征和完整行情存 Parquet，SQLite 存索引/manifest/摘要。策略轨迹体积大时落工件文件，表里保存 hash 与位置。默认不物理删除被任何实验、交易、审批引用的对象。

### 6.2 实验指纹

必须保存：`code_commit, strategy_version, config_hash, component_versions, feature_schema_hash, dataset_manifest_hash, universe_snapshot_hash, corporate_action_vintage, calendar_version, market_rule_version, cost_profile_hash, execution_profile, model_hash, seed, dependency_lock_hash`。

同一环境下重复回放应有一致的事件与成交轨迹；跨硬件浮点结果按已声明精度容差核对，不能把模型训练的非确定性藏在“策略没变”下面。浮点容差不能放宽成价格跨了一个 tick 仍算一致。

### 6.3 真实持仓与策略归因

实际账本是权威。持仓投影按成交、手续费、分红、拆并股、现金流等重建。真实成交必须带 `broker_execution_id` 或可审核的导入批次去重键，重复上传不能重复加仓。

同一股票多个策略持有时，真实数量按账户净额管理，归因通过 lot/虚拟策略份额记录，总和必须等于实际数量。首版默认禁止同一股票被多个实例同时新增，以减少归因与互相对冲问题。无策略来源的既有仓位标记 `MANUAL_UNASSIGNED`，不能把其既往涨幅记作新策略收益。

用户确认消息只是 `ACKNOWLEDGED`；只有 `ExecutionImported` 才可转 `FILLED`。忽略、取消、部分成交、成交迟到都必须保存，不能从报表中消失。

## 7. API 与 CLI

### 7.1 API 分组

| 接口 | 行为 |
|---|---|
| `GET/POST /api/v1/sources` | 查询/登记来源；登记不执行代码 |
| `POST /api/v1/sources/{id}/reviews` | 提交指定快照审查 |
| `GET/POST /api/v1/strategies` | 策略目录/创建定义 |
| `POST /api/v1/strategies/{id}/versions` | 创建冻结版本，不修改历史版本 |
| `POST /api/v1/strategy-versions/{id}/validate` | 生成 schema/能力/测试作业 |
| `POST /api/v1/experiments` | 回放、参数研究、消融或 OOS 作业 |
| `GET /api/v1/experiments/{id}` | 状态、结果、拒绝/失败和工件 |
| `GET /api/v1/comparisons?run_ids=...` | 比较前核验快照/成本/执行可比性 |
| `POST /api/v1/instances` | 指定策略版本、账户、股票池、预算、运行模式 |
| `POST /api/v1/instances/{id}/commands` | start/pause_entries/exit_only/stop/rollback |
| `POST /api/v1/deployments/{id}/approve` | 显式批准精确版本与限额 |
| `GET /api/v1/signals`、`GET /api/v1/plans` | 区分观察与操作计划、状态/到期 |
| `POST /api/v1/plans/{id}/feedback` | 确认/忽略/取消理由，不造交易 |
| `POST /api/v1/executions/import` | 真实/仿真成交导入和对账 |
| `GET /api/v1/health` | 数据/作业/风险/模型健康 |
| `GET /api/v1/events` | 本地 SSE 实时状态推送，断线可按序号补读 |

所有有副作用命令要求 `Idempotency-Key`，响应返回 command/job ID。长任务返回 202，不在请求中跑完整回测。启停使用实例状态版本的乐观并发检查；状态冲突返回 409，不覆盖新状态。

### 7.2 CLI 是首要验收入口

预定命令包括 `xquant data validate`、`xquant strategy validate`、`xquant replay`、`xquant experiment compare`、`xquant instance start --mode paper`、`xquant serve`。精确实施见 Codex 文档。命令是目标接口，不是本交付已经安装完成的程序。

## 8. 网页信息架构：策略优先

首页是“策略驾驶台”，不是股票新闻门户。默认展示运行实例、数据截止、风控状态、待处理计划、近期实验和失效策略；任何示例曲线必须带 DEMO 水印。

| 页面 | 主要内容 |
|---|---|
| 策略库 | 策略卡、证据等级、来源、频率、市场、状态、标签、最近验证 |
| 策略详情 | 交易假设、规则、参数 schema、版本差异、相关实验与部署 |
| 实验中心 | 队列/进度/预算、同口径比较、消融、参数稳定性与失败记录 |
| 回放工作台 | K 线、当时可见区域、状态转换、计划/成交、拒绝原因；支持逐棒推进 |
| 部署中心 | 实例/账户/股票池、模式、预算、启停、审批和回滚 |
| 候选雷达 | 全市场过滤漏斗、观察事件、可核对计划，不能把分数写成胜率 |
| 持仓与风险 | 真实可卖数量、成本、保护阈值、行业敞口、策略归因与手工仓位 |
| 预测管理 | 模型目标、训练/校准截止、样本外指标、适用范围、漂移/失效 |
| 数据中心 | 覆盖率、延迟、权限、缺失、修订、配额、熔断与来源差异 |
| 通知/复盘 | 投递、用户反馈、成交导入、计划执行差异、逐笔复盘 |

提供“为什么没有交易”入口，展示每一层的排除数量和示例，避免 Codex 或用户通过随意降低所有门槛来制造信号。完整策略编辑器可以后续做，首版采用 schema 表单和 YAML 导入即可。

## 9. 数据获取与限流：服务策略，不让策略直接抓网站

全市场日线集中批量同步；持仓优先，研究历史回补次之；盘中订阅仅在授权与预算允许时开启。每个策略不得各自重复拉同一股票数据。

DataHub 负责供应商/凭证/接口/主机多层统一配额、请求合并、本地缓存、增量水位、条件请求、最大并发、429 的 Retry-After、指数退避和抖动、熔断及质量隔离。配额来自实际合同/官方文档配置，不在策略里写死“每秒可以请求多少”。

403/验证码/授权失败时暂停相关源，检查权限；不使用轮换账号、代理池、验证码绕过或伪装来规避限制。备用数据源必须也被授权，且完成价格/复权/时间戳一致性检查；切源生成新 revision，不把不同口径拼成同一条事实序列。

无外网时可研究已持有的授权快照，但不能把上周数据当今天信号。恢复联网后补数据→校验→重建必要状态→判断计划时效；过期历史信号只归档，不能积压后一口气发送为当前买入提醒。

## 10. 通知、休眠与本地可靠性

独立 outbox 保存业务消息与有效期。优先支持站内、飞书 webhook、邮件；其他聊天工具通过适配器扩展，不依赖个人聊天软件非官方协议。首版选择用户实际配置的一个外部渠道即可。

去重键建议 `(instance_id,instrument_id,setup_id,state_transition,event_session)`。同一形态反复轮询不能重复轰炸；首次观察、确认、取消、退出、数据失效是不同事件。过期的买入提醒不再投递，未投递的退出/系统风险按最新状态重新生成摘要，不能无声丢弃。

消息展示策略版本、数据时点、观察/操作性质、价格范围、数量上限、保护阈值、目标、执行窗口与失效条件。默认不向第三方群聊发送账户总资产、成本明细或 API 密钥。需要外发具体持仓时要求用户明确设置允许字段。

电脑休眠/退出程序时服务不会运行，这是本地产品能力边界。界面必须显著显示最后健康心跳和最后成功数据时间；系统恢复后应提示监控空窗。需要持续监控时，应部署到持续运行的本地设备/自托管主机，不将个人电脑关闭后的消息能力写成保证。

## 11. 安全与来源代码执行

默认仅监听 `127.0.0.1`，严格校验 Host/Origin、会话令牌与有副作用请求的 CSRF。首次本机配对建立本地登录会话；不将本地服务默认开放公网或局域网。Web 静态目录不能映射行情、密钥、账户数据库或整个用户主目录。

仓库 URL 的获取端需防 SSRF/路径穿越，仅允许预设外部源，不接受随意访问私网地址。用户上传策略包默认静态分析，禁自动执行 setup.py/安装钩子。Python 子进程不是安全沙箱；首版只执行本项目内置和经过人工审查的策略。后续第三方不受信任代码必须采用 OS/容器级限制、无网络、最小只读挂载、CPU/内存/时间限制，仍不得授予账户与密钥权限。

密钥使用系统安全存储或受限配置文件，不能进入 git、日志、报告或模型提示词。备份需包含 SQLite 一致性快照、相关 manifest/工件与版本，并定期执行恢复测试；只复制一个正在写入的数据库文件不算完整备份策略。

## 12. 容错、性能与可观测性

实时持仓检查、通知、批量扫描、研究训练采用独立优先级预算。默认研究并发 1，最大并发由本机资源配置；不得因为跑参数搜索占满内存而让风险服务无响应。数据量相关性能目标在本机基准测试后填写，不声称所有设备都能在固定秒数扫完全部股票。

关键观测项：最后完成会话、数据缺失比例、队列等待、任务 heartbeat、超时/取消、计划到期量、消息投递延迟、资金预留总额、账本对账差异、每策略过滤漏斗与信号频率异常。指标异常可自动暂停新增，但不要把“停止提醒”当作降低真实持仓风险。

任务执行采用可重试、可重复投递模型；通过幂等业务事件保证不会双记成交、双冻结资金或重复创建同一计划。进程在计算完成前崩溃可重算，落库事务成功但应答丢失时重复请求返回原结果。

## 13. 非目标与禁止捷径

首版不做全市场高频交易，不做保证收益排名，不靠大模型自主改策略上线，不默认加载所有开源框架，不允许未经许可抓取，不使用一套未含成本的指标回测作为实战证明。

禁止完成一个充满示例数据的仪表盘后宣称“量化系统完成”。真正的首个里程碑是策略内核和回放验证；网页只在此之后展示已存在、可追溯的结果。
