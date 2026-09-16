# X-QuantSentinel 多周期分析与 AI / 量价分析整合改造方案

> 目标：将当前“AI 分析中心”“量价/支撑阻力分析”“多周期分析”统一为一个面向单股票的分析工作台。UI 层合并，分析引擎层保持解耦，通过统一的数据结构和多周期融合层输出最终决策状态。

> 归档状态：已实施并验收（2026-09-16）。
>
> 本次完成 Phase 1-5 的可运行闭环：新增单周期标准结果、多周期融合与确定性决策核心层；新增股票驱动的统一分析 API 与 15 分钟缓存；AI 改为消费结构化量化上下文并按需生成；前端新增 `/analysis` 股票分析工作台。原有 `/ai` AI 分析与 `/market-analysis` 量价分析继续作为独立入口和完整功能保留，不通过重定向替代。现有单周期分析端点、AI 服务与数据集能力保持兼容。
>
> 验证结果：后端量化、AI 上下文、统一股票 API、旧数据集/分析端点共 28 项测试通过；Ruff 检查通过；前端 `tsc -b` 与 `vite build` 通过。第二阶段可继续补充持久化快照、调度任务和回测版本追溯。

---

## 1. 改造背景

当前系统已经具备两类主要分析能力：

1. AI 分析模块
   - 实时分析
   - 交易决策
   - 风险与失效条件
   - 未来走势
   - AI 原始分析
   - 通知推送

2. 量价 / 支撑阻力分析模块
   - 支撑与压力
   - 趋势识别
   - ATR 距离
   - 守住概率
   - 历史回测
   - 关键价位
   - 价格行为

现有系统同时按不同周期保存和展示股票，例如：

```text
002277 + 1D
002277 + 1H
002277 + 30m
002277 + 15m
```

如果直接在前端列表中展开，会导致同一股票占据多行，用户难以快速判断：

- 大方向是否上涨
- 当天走势如何
- 当前是否存在操作机会

因此需要增加“多周期分析”能力，但不建议新增第三套独立分析页面，而应将其作为核心融合层，串联量化分析与 AI 分析。

---

# 2. 核心设计原则

## 2.1 UI 合并，引擎解耦

前端产品层：

```text
AI 分析
量价分析
多周期分析
```

不再作为三个完全独立的入口。

统一改造成：

```text
股票分析工作台
```

但后端仍保留独立模块：

```text
MarketData
    ↓
SingleTimeframeAnalyzer
    ↓
MultiTimeframeAnalyzer
    ↓
DecisionEngine
    ↓
AIAnalyzer
```

这样可以做到：

- UI 更简洁
- 策略保持可测试
- AI 不直接取代量化逻辑
- 未来支持更多周期和策略时无需重构页面

---

## 2.2 多周期不是独立策略，而是融合层

多周期分析不应被视为一种新的技术分析算法。

它负责解决：

> 同一股票在不同时间尺度上的分析结果如何组合。

例如：

```text
1D    上涨
1H    回调
30m   震荡
15m   转强
```

以上结果可以同时成立。

融合后应得到：

```text
战略趋势：上涨
日内阶段：回调后整理
短线状态：开始企稳
当前操作状态：WAIT_BUY
```

---

# 3. 推荐总体架构

```text
                           Market Data
                               │
        ┌──────────────────────┼──────────────────────┐
        │                      │                      │
        ▼                      ▼                      ▼
   1D Analyzer             1H Analyzer          15m Analyzer
        │                      │                      │
        └──────────────────────┼──────────────────────┘
                               ▼
                   MultiTimeframeAnalyzer
                               │
                               ▼
                       QuantDecisionEngine
                               │
                     ┌─────────┴─────────┐
                     ▼                   ▼
                  前端 UI             AI Analyzer
                                          │
                                          ▼
                           解释 / 情景 / 风险 / 通知
```

推荐项目层次：

```text
01 数据层
02 单周期量化策略层
03 多周期融合层
04 决策层
05 AI 分析层
06 表现层 / API
```

---

# 4. 周期职责定义

默认建议使用：

| 周期 | 系统角色 | 主要职责 |
|---|---|---|
| 1D | 战略趋势周期 | 判断中短期趋势、核心结构、是否值得持续关注 |
| 1H | 日内主周期 | 判断当天方向、回调、趋势延续或反转 |
| 30m | 确认周期 | 确认 1H 结构变化、观察整理和突破 |
| 15m | 执行周期 | 捕捉入场、突破、回踩、止损止盈触发 |

后续可扩展：

```text
1W
4H
5m
```

但前端不应写死这些周期。

建议通过角色映射：

```yaml
multi_timeframe_profile:
  strategic: 1d
  tactical: 1h
  confirmation: 30m
  execution: 15m
```

这样未来不同策略可以使用不同周期组合。

---

# 5. 模块职责重新定义

## 5.1 量化分析模块

建议将当前“支撑阻力与价格行为”逐步统一命名为：

```text
Quant Analysis / 量化分析
```

模块负责计算可重复、可回测、可量化的数据。

包括但不限于：

```text
Trend
Momentum
VolumePrice
SupportResistance
MarketStructure
Volatility
Breakout
MeanReversion
MoneyFlow
RelativeStrength
Pattern
ATR
```

接口示例：

```python
class SingleTimeframeAnalyzer:
    def analyze(
        self,
        symbol: str,
        timeframe: str,
        bars: list,
        context: dict | None = None,
    ) -> "TimeframeAnalysisResult":
        ...
```

输出结构：

```python
@dataclass
class TimeframeAnalysisResult:
    symbol: str
    timeframe: str
    timestamp: datetime

    trend: str
    trend_score: float
    phase: str

    momentum_score: float
    volatility_score: float

    support_levels: list[float]
    resistance_levels: list[float]

    volume_state: str
    price_structure: str

    signals: list[str]
    confidence: float
```

---

## 5.2 多周期融合模块

新增核心模块：

```text
MultiTimeframeAnalyzer
```

职责：

- 聚合不同周期分析结果
- 识别周期一致性
- 识别周期冲突
- 判断大周期趋势与小周期状态
- 输出统一综合状态
- 为决策引擎和 AI 提供标准输入

接口示例：

```python
class MultiTimeframeAnalyzer:
    def analyze(
        self,
        symbol: str,
        results: dict[str, TimeframeAnalysisResult],
        profile: "MultiTimeframeProfile",
    ) -> "MultiTimeframeResult":
        ...
```

配置：

```python
@dataclass
class MultiTimeframeProfile:
    strategic: str = "1d"
    tactical: str = "1h"
    confirmation: str = "30m"
    execution: str = "15m"
```

输出：

```python
@dataclass
class MultiTimeframeResult:
    symbol: str
    timestamp: datetime

    strategic_trend: str
    strategic_score: float

    intraday_state: str
    confirmation_state: str
    execution_state: str

    alignment_score: float
    conflict_score: float

    bullish_score: float
    bearish_score: float

    summary_state: str
    metadata: dict
```

---

# 6. 多周期状态建议

## 6.1 趋势状态

```text
STRONG_BULLISH
BULLISH
NEUTRAL
BEARISH
STRONG_BEARISH
```

## 6.2 阶段状态

```text
TRENDING
PULLBACK
CONSOLIDATION
BREAKOUT
REVERSAL
EXHAUSTION
UNKNOWN
```

## 6.3 执行状态

```text
WAIT
WATCH
READY
TRIGGERED
INVALIDATED
```

## 6.4 周期一致性

```text
FULL_ALIGNMENT
PARTIAL_ALIGNMENT
MIXED
HIGH_CONFLICT
```

推荐输出数值：

```text
alignment_score: 0 ~ 100
conflict_score: 0 ~ 100
```

---

# 7. 决策层设计

多周期融合结果不应直接等同于交易决策。

建议新增：

```text
QuantDecisionEngine
```

输入：

```text
MultiTimeframeResult
+ 风控参数
+ 当前持仓状态
+ 策略配置
```

输出统一决策：

```text
BUY
WAIT_BUY
HOLD
REDUCE
WAIT_SELL
SELL
AVOID
```

输出结构：

```python
@dataclass
class QuantDecision:
    action: str
    confidence: float

    entry: float | None
    stop: float | None
    target: float | None

    risk_reward: float | None

    trigger_conditions: list[str]
    invalid_conditions: list[str]
    risk_flags: list[str]

    reason_codes: list[str]
```

示例：

```yaml
symbol: "002277"
action: WAIT_BUY
confidence: 0.72

entry: 5.73
stop: 5.61
target: 6.02

trigger_conditions:
  - 15m 放量突破 5.72
  - 30m 收盘站稳关键压力

invalid_conditions:
  - 1H 跌破 5.62
  - 日线趋势评分低于 60
```

---

# 8. AI 模块重新定位

AI 不应承担基础量化计算。

错误方式：

```text
K 线
 ↓
AI
 ↓
判断趋势
 ↓
判断支撑压力
 ↓
给交易结论
```

推荐方式：

```text
量化引擎
 ↓
多周期融合
 ↓
决策引擎
 ↓
AI
```

AI 主要职责：

- 将量化结果转成人类可读说明
- 解释周期冲突
- 解释为何 WAIT / HOLD / BUY
- 结合新闻、政策、情绪、事件
- 输出情景推演
- 输出风险提示
- 生成飞书 / 微信 / 邮件通知内容

AI 输入示例：

```yaml
symbol: 002277

strategic:
  timeframe: 1d
  trend: bullish
  score: 82

tactical:
  timeframe: 1h
  state: pullback

confirmation:
  timeframe: 30m
  state: consolidation

execution:
  timeframe: 15m
  state: turning_bullish

volume_price:
  volume_state: shrinking
  price_structure: consolidation

multi_timeframe:
  alignment_score: 72
  conflict_score: 18

quant_decision:
  action: WAIT_BUY
  confidence: 0.72
```

AI 输出示例：

```text
日线趋势维持多头，但 1 小时仍处于回调阶段。
30 分钟正在横盘整理，15 分钟开始出现重新走强迹象。

当前尚未形成有效突破，不建议追涨。
若 15 分钟放量突破 5.72 且 30 分钟收盘确认，可重新评估入场机会。
若 1 小时跌破 5.62，则当前多头结构视为失效。
```

---

# 9. UI 改造方案

## 9.1 页面总体结构

将现有：

```text
AI 分析中心
支撑阻力与价格行为
```

逐步整合成：

```text
股票分析工作台
```

页面顶部：

```text
002277 友阿股份
当前价 5.69
```

核心摘要：

```text
趋势        ↑ 多头
日内        → 整理
短线        ↑ 转强
综合信号    WAIT_BUY
置信度      72%
```

---

## 9.2 推荐 Tab

```text
综合概览
多周期
量价结构
AI 诊断
决策
未来走势
历史分析
原始数据
```

---

# 10. 综合概览设计

此页面作为默认首页。

建议包含：

```text
┌─────────────────────────────────────┐
│             当前综合状态             │
│                                     │
│ 中期趋势      日内状态      操作状态 │
│ ↑ 多头        回调整理      WAIT_BUY │
│                                     │
│ 综合评分：78 / 100                   │
└─────────────────────────────────────┘
```

周期摘要：

```text
1D    ↑ 强势上涨       82
1H    ↓ 回调           61
30m   → 横盘           55
15m   ↑ 转强           73
```

关键价位：

```text
最近支撑   5.62
最近压力   5.72
当前价     5.69
```

AI 综合结论：

```text
日线维持偏多结构，当前处于小时级回调后的整理阶段。
15 分钟已经出现转强迹象，但 5.72 附近仍存在明显压力。
等待突破确认后再重新评估入场。
```

---

# 11. 多周期页面设计

表格示例：

| 周期 | 趋势 | 阶段 | 动量 | 量价 | 支撑 | 压力 | 信号 |
|---|---|---|---|---|---|---|---|
| 1D | ↑ | 趋势 | 强 | 健康 | 5.20 | 6.10 | HOLD |
| 1H | ↓ | 回调 | 弱 | 缩量 | 5.62 | 5.88 | WAIT |
| 30m | → | 整理 | 中性 | 缩量 | 5.65 | 5.78 | WAIT |
| 15m | ↑ | 企稳 | 增强 | 温和放量 | 5.68 | 5.72 | WATCH |

下面展示：

```text
周期一致性：72%
冲突度：18%
```

可视化：

```text
1D   ────────── BULL
1H       ────── PULLBACK
30m          ── RANGE
15m             TURNING_UP
```

---

# 12. 量价结构页面改造

保留现有支撑阻力、趋势、ATR、历史统计等能力。

但不再把“数据集”作为一级用户概念。

用户进入股票后，系统默认已经知道 symbol。

周期选择改成：

```text
[日线] [1小时] [30分钟] [15分钟]
```

高级参数例如：

```text
Lookback
区间数量
Risk Fraction
最小 RR
方向
```

收纳到：

```text
⚙ 策略参数
```

避免主页面过于工程化。

---

# 13. AI 分析页面改造

当前模式类似：

```text
友阿股份 · 002277 · 15m · 516 根
```

建议改为：

```text
友阿股份 · 002277
```

分析范围：

```text
● 综合多周期
○ 日线
○ 日内
○ 15 分钟
```

默认：

```text
综合多周期
```

AI 分析不应强制绑定单个时间周期。

---

# 14. 数据模型改造

## 14.1 不删除单周期数据

后端仍然保留：

```text
002277 + 1d
002277 + 1h
002277 + 30m
002277 + 15m
```

UI 合并，不代表底层数据合并。

---

## 14.2 analysis_snapshot

```sql
CREATE TABLE analysis_snapshot (
    id UUID PRIMARY KEY,
    symbol VARCHAR(32) NOT NULL,
    timeframe VARCHAR(16) NOT NULL,
    analyzed_at TIMESTAMP NOT NULL,

    trend VARCHAR(32),
    trend_score NUMERIC,
    phase VARCHAR(32),

    momentum_score NUMERIC,
    volatility_score NUMERIC,

    support_levels JSONB,
    resistance_levels JSONB,

    volume_state VARCHAR(32),
    price_structure VARCHAR(64),

    signals JSONB,
    confidence NUMERIC,
    raw_result JSONB,

    UNIQUE(symbol, timeframe, analyzed_at)
);
```

---

## 14.3 multi_timeframe_snapshot

```sql
CREATE TABLE multi_timeframe_snapshot (
    id UUID PRIMARY KEY,
    symbol VARCHAR(32) NOT NULL,
    analyzed_at TIMESTAMP NOT NULL,

    strategic_timeframe VARCHAR(16),
    tactical_timeframe VARCHAR(16),
    confirmation_timeframe VARCHAR(16),
    execution_timeframe VARCHAR(16),

    strategic_trend VARCHAR(32),
    strategic_score NUMERIC,

    intraday_state VARCHAR(32),
    confirmation_state VARCHAR(32),
    execution_state VARCHAR(32),

    alignment_score NUMERIC,
    conflict_score NUMERIC,

    summary_state VARCHAR(64),
    raw_result JSONB
);
```

建议索引：

```sql
CREATE INDEX idx_analysis_snapshot_symbol_timeframe_time
ON analysis_snapshot(symbol, timeframe, analyzed_at DESC);

CREATE INDEX idx_multi_tf_symbol_time
ON multi_timeframe_snapshot(symbol, analyzed_at DESC);
```

---

## 14.4 quant_decision_snapshot

```sql
CREATE TABLE quant_decision_snapshot (
    id UUID PRIMARY KEY,
    symbol VARCHAR(32) NOT NULL,
    decided_at TIMESTAMP NOT NULL,

    action VARCHAR(32) NOT NULL,
    confidence NUMERIC,

    entry_price NUMERIC,
    stop_price NUMERIC,
    target_price NUMERIC,
    risk_reward NUMERIC,

    trigger_conditions JSONB,
    invalid_conditions JSONB,
    risk_flags JSONB,
    reason_codes JSONB,

    raw_result JSONB
);
```

---

# 15. 股票实体与数据集解耦

系统当前操作方式偏向：

```text
数据集驱动
```

例如：

```text
002277_15m_516bars
```

目标应改为：

```text
股票驱动
```

结构：

```text
Stock
 └─ 002277
    ├─ 1D bars
    ├─ 1H bars
    ├─ 30m bars
    └─ 15m bars
```

用户操作对象：

```text
002277 友阿股份
```

数据集仅作为底层基础设施存在。

---

# 16. API 设计建议

## 16.1 获取股票统一分析

```http
GET /api/stocks/{symbol}/analysis
```

返回：

```json
{
  "symbol": "002277",
  "quote": {},
  "multi_timeframe": {},
  "decision": {},
  "ai_summary": {}
}
```

---

## 16.2 获取所有周期分析

```http
GET /api/stocks/{symbol}/analysis/timeframes
```

返回：

```json
{
  "1d": {},
  "1h": {},
  "30m": {},
  "15m": {}
}
```

---

## 16.3 获取指定周期分析

```http
GET /api/stocks/{symbol}/analysis/timeframes/{timeframe}
```

---

## 16.4 执行多周期融合

```http
POST /api/stocks/{symbol}/analysis/multi-timeframe
```

请求：

```json
{
  "profile": {
    "strategic": "1d",
    "tactical": "1h",
    "confirmation": "30m",
    "execution": "15m"
  }
}
```

---

## 16.5 请求 AI 综合分析

```http
POST /api/stocks/{symbol}/analysis/ai
```

请求：

```json
{
  "scope": "multi_timeframe"
}
```

scope 可选：

```text
multi_timeframe
daily
intraday
execution
```

---

# 17. 任务调度集成

多周期分析需与项目已有定时任务模块集成。

建议：

```text
收盘后
    ↓
更新 1D
    ↓
运行单周期分析
    ↓
刷新多周期融合

盘中每小时
    ↓
更新 1H
    ↓
刷新融合结果

每 30 分钟
    ↓
更新 30m
    ↓
刷新融合结果

每 15 分钟
    ↓
更新 15m
    ↓
刷新融合结果
    ↓
判断是否产生交易状态变化
```

仅在以下情况触发 AI：

```text
决策状态变化
关键价位突破
风险状态变化
用户主动分析
通知任务触发
```

避免每根 K 线都调用 AI。

---

# 18. 缓存策略

Redis 建议缓存：

```text
analysis:{symbol}:{timeframe}
multi_tf:{symbol}
decision:{symbol}
ai_summary:{symbol}
```

示例：

```text
analysis:002277:1d
analysis:002277:1h
analysis:002277:30m
analysis:002277:15m
multi_tf:002277
decision:002277
```

建议 TTL：

| 数据 | TTL |
|---|---|
| 1D 分析 | 24h |
| 1H 分析 | 2h |
| 30m 分析 | 1h |
| 15m 分析 | 30m |
| MultiTimeframe | 15~30m |
| Decision | 15~30m |
| AI Summary | 根据决策版本失效 |

AI 缓存建议基于版本：

```text
ai_summary:{symbol}:{decision_version}
```

避免重复调用模型。

---

# 19. 策略插件体系兼容

多周期模块不能写死具体指标。

建议单周期策略均实现统一接口：

```python
class StrategyAnalyzer(Protocol):
    name: str

    def analyze(
        self,
        context: "AnalysisContext"
    ) -> "StrategySignal":
        ...
```

例如：

```text
TrendStrategy
MomentumStrategy
SupportResistanceStrategy
VolumePriceStrategy
BreakoutStrategy
MeanReversionStrategy
MoneyFlowStrategy
```

统一输出：

```python
@dataclass
class StrategySignal:
    strategy: str
    direction: str
    score: float
    confidence: float
    signal: str | None
    metadata: dict
```

单周期分析器负责聚合策略结果。

---

# 20. 建议目录结构

```text
backend/
├── market_data/
│   ├── models/
│   ├── repositories/
│   └── services/
│
├── quant/
│   ├── core/
│   │   ├── context.py
│   │   ├── result.py
│   │   └── enums.py
│   │
│   ├── strategies/
│   │   ├── trend/
│   │   ├── momentum/
│   │   ├── support_resistance/
│   │   ├── volume_price/
│   │   ├── volatility/
│   │   └── breakout/
│   │
│   ├── single_timeframe/
│   │   └── analyzer.py
│   │
│   ├── multi_timeframe/
│   │   ├── analyzer.py
│   │   ├── profile.py
│   │   ├── alignment.py
│   │   └── conflict.py
│   │
│   └── decision/
│       ├── engine.py
│       ├── rules.py
│       └── risk.py
│
├── ai/
│   ├── analyzer.py
│   ├── prompts/
│   ├── schemas/
│   └── context_builder.py
│
├── api/
│   └── stock_analysis/
│
└── tasks/
    ├── timeframe_analysis.py
    ├── multi_timeframe.py
    └── ai_analysis.py
```

前端：

```text
frontend/src/
├── pages/
│   └── StockAnalysisWorkbench/
│
├── components/
│   └── stock-analysis/
│       ├── OverviewPanel
│       ├── MultiTimeframePanel
│       ├── QuantPanel
│       ├── AIDiagnosisPanel
│       ├── DecisionPanel
│       ├── HistoryPanel
│       └── RawDataPanel
```

---

# 21. 前端状态设计

推荐统一类型：

```ts
interface StockAnalysisViewModel {
  symbol: string;
  name: string;
  quote: Quote;

  overview: AnalysisOverview;

  timeframes: Record<string, TimeframeAnalysis>;

  multiTimeframe: MultiTimeframeResult;

  decision: QuantDecision;

  ai?: AIAnalysisResult;
}
```

UI 不直接从多张表或多个 API 拼装业务含义。

由后端统一返回 ViewModel 或 Aggregated DTO。

---

# 22. 核心业务规则建议

## 22.1 大周期具有更高权重

默认权重建议：

```yaml
weights:
  strategic: 0.40
  tactical: 0.30
  confirmation: 0.20
  execution: 0.10
```

注意：

执行周期的权重小，不代表其不重要。

它主要控制：

```text
是否触发操作
```

而不是控制整体趋势。

---

## 22.2 不允许小周期直接推翻大周期趋势

例如：

```text
1D  bullish
1H  bullish
30m neutral
15m bearish
```

不得输出：

```text
综合趋势 bearish
```

更合理：

```text
战略趋势 bullish
短线回调 bearish
综合状态 bullish_pullback
```

---

## 22.3 周期冲突必须显式输出

例如：

```text
1D bullish
1H bearish
30m bearish
15m bearish
```

输出：

```text
strategic_trend = bullish
intraday_state = bearish
conflict_score = 72
summary_state = bullish_under_pressure
```

AI 可以解释：

```text
日线结构尚未破坏，但日内多个周期同步转弱，当前不适合新增多头仓位。
```

---

# 23. 决策状态机建议

推荐：

```text
AVOID
  ↓
WATCH
  ↓
WAIT_BUY
  ↓
BUY
  ↓
HOLD
  ↓
REDUCE
  ↓
WAIT_SELL
  ↓
SELL
```

实际转换由规则驱动，不要求严格线性。

示例：

```text
WAIT_BUY
  ├─ 突破确认 → BUY
  ├─ 条件失效 → WATCH
  └─ 大周期转弱 → AVOID
```

---

# 24. 历史分析与回测

多周期融合结果必须可被回测。

每次融合结果应保存：

```text
分析时间
使用的各周期数据版本
策略版本
参数版本
结果
决策
```

建议增加：

```text
strategy_version
profile_version
engine_version
```

便于未来判断：

```text
为什么今天结果与一个月前不同
```

---

# 25. 可观测性与调试

保留“原始 / 调试”页。

建议展示：

```text
输入周期
数据时间
策略执行耗时
策略输出
权重
融合规则
冲突评分
最终决策
AI 输入上下文
```

推荐所有分析具备：

```text
trace_id
analysis_id
version
```

便于定位问题。

---

# 26. 迁移计划

## Phase 1：抽象统一分析结果

先不要改 UI。

完成：

```text
TimeframeAnalysisResult
MultiTimeframeResult
QuantDecision
```

将现有量价分析结果适配到新结构。

---

## Phase 2：实现 MultiTimeframeAnalyzer

支持：

```text
1D
1H
30m
15m
```

完成：

- 周期角色映射
- 一致性
- 冲突
- 综合状态

---

## Phase 3：实现 QuantDecisionEngine

把现有 AI 中的一部分确定性规则迁移出来。

例如：

```text
突破
止损
失效条件
风险收益比
```

优先放到量化决策层。

---

## Phase 4：改造 AI 模块

AI 输入从：

```text
单周期 K 线 / 技术指标
```

改为：

```text
量化分析结果
+ 多周期分析
+ 决策结果
+ 新闻 / 情绪 / 事件
```

---

## Phase 5：整合前端

新增：

```text
StockAnalysisWorkbench
```

合并现有：

```text
AI 分析中心
支撑阻力 / 价格行为
```

---

## Phase 6：隐藏数据集概念

从普通用户界面逐渐移除：

```text
数据集 ID
XXX 根 K 线
数据文件
```

保留到：

```text
高级设置
调试模式
```

---

# 27. Codex 开发要求

## 必须遵守

1. 不允许删除现有单周期分析能力。
2. UI 合并，但后端分析模块必须保持解耦。
3. MultiTimeframeAnalyzer 不直接调用 AI。
4. QuantDecisionEngine 不依赖大模型。
5. AIAnalyzer 只消费结构化结果。
6. 所有新模块必须可单元测试。
7. 多周期角色不能硬编码在 UI。
8. 不允许将 `1d/1h/30m/15m` 写死为数据库字段。
9. 所有分析结果必须带时间戳与版本。
10. 保留原始分析输出用于调试。

---

# 28. 单元测试要求

至少覆盖：

## Case 1：全周期上涨

```text
1D bullish
1H bullish
30m bullish
15m bullish
```

期望：

```text
alignment_score 高
conflict_score 低
```

---

## Case 2：大周期上涨，小周期回调

```text
1D bullish
1H pullback
30m bearish
15m bearish
```

不得输出：

```text
overall bearish
```

应输出：

```text
strategic bullish
intraday pullback
```

---

## Case 3：大周期上涨，小周期重新转强

```text
1D bullish
1H pullback
30m consolidation
15m bullish
```

期望：

```text
WAIT_BUY / WATCH
```

具体状态由决策规则配置。

---

## Case 4：周期高度冲突

```text
1D bullish
1H bearish
30m bearish
15m bearish
```

期望：

```text
conflict_score 高
```

并禁止直接输出强 BUY。

---

## Case 5：缺失周期数据

只有：

```text
1D
15m
```

系统不能报错。

应：

- 降低 confidence
- 标记 missing_timeframes
- 正常输出可用结果

---

# 29. 验收标准

改造完成后需要满足：

### 前端

- 同一股票在主列表仅显示一次
- 可一眼看到日线趋势、日内状态、短线状态、决策状态
- 点击股票进入统一分析工作台
- 可切换查看不同周期详细结果
- 普通用户不再需要理解“数据集”

### 后端

- 每个周期独立分析
- 多周期统一融合
- 决策独立输出
- AI 可选
- AI 关闭后系统仍能正常运行

### 策略

- 支持新增策略插件
- 支持新增周期
- 支持不同周期配置
- 可回测
- 可追溯

### 性能

- 不因前端查看一个股票重复执行所有分析
- 使用 Redis 缓存最新分析结果
- AI 仅在必要情况下执行

---

# 30. 最终目标

最终系统的用户操作路径应从：

```text
选择数据集
→ 选择周期
→ 执行量价分析
→ 再执行 AI 分析
→ 自己比较不同周期
```

变成：

```text
选择股票
    ↓
系统自动加载 1D / 1H / 30m / 15m
    ↓
单周期量化分析
    ↓
多周期融合
    ↓
量化决策
    ↓
AI 综合解释
    ↓
统一展示
```

对用户最终只需要回答三个问题：

```text
1. 大方向怎么样？
2. 今天处于什么状态？
3. 现在应该关注什么条件？
```

系统内部则继续保留完整的：

```text
行情
量化策略
多周期
风控
决策
AI
回测
通知
```

能力。

---

# 31. 核心结论

本次改造建议采用：

> **页面合并、数据聚合、策略解耦、决策统一。**

多周期分析不是新的孤立页面，而应作为 X-QuantSentinel 的核心分析中间层。

最终关系：

```text
量价 / 技术策略
        ↓
单周期分析
        ↓
多周期融合
        ↓
量化决策
        ↓
AI 解释
        ↓
用户 / 通知
```

该结构应作为后续策略系统、股票筛选系统、持仓管理和自动提醒模块的统一分析基础。
