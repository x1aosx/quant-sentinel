# AI 分析模块与 PA_Agent 对齐：差异审计与 Agent 实施方案

- 状态：**分析完成，尚未实施；供后续编码 Agent 执行**。
- 审计日期：2026-11-03。
- 本地基线：`quant-sentinel@2830562ba3a4bac0bdc1617695b9bd45bcf79286`。
- 对照项目：`https://github.com/rosemarycox5334-debug/PA_Agent`。
- 上游固定基线：`222badb6c34afae8daa59a47cbee1cdd59db05dd`，提交时间 `2026-11-02T16:50:39+08:00`，提交说明 `1.68`。
- 方法：直接检出上游固定 commit，对照双方源码、配置与现有测试；**未调用付费模型、未实际发送通知、未执行真实交易、未修改业务代码**。
- 路径约定：本地路径从本仓库根目录起算；`上游:` 路径从 PA_Agent 根目录起算。行号对应上述固定基线，实施时以符号定位为准。

## 1. 结论与约束

当前模块不是 PA_Agent 的等价迁移：它具备“两阶段分析、决策展示、飞书发送、多股票盯盘”的外形，但 Prompt、知识库、特征、策略路由、连续性、输出契约和校验执行链均有实质差异。仅补几句 Prompt 或把通知开关打开，无法达到“股票分析逻辑完全一样”。

用户目标应落实为：

1. **保留本系统的数据集管理、行情采集/存储、多股票与多周期监听拆分方式。** 不迁回 PA_Agent 的桌面端和数据源管理。
2. **其余 AI 股票诊断与决策行为按固定上游实现对齐**，包括影响判断的输入、规则文本、两阶段消息序列、路由、标准化、校验、重试、连续性与默认配置。
3. **有效操作机会自动通知本人**：在已启用且配置有效的通知渠道中，不能依赖用户在页面再次点击“发送通知”，也不能因切换分析入口漏掉。
4. 不增加券商连接、自动下单、仓位执行等功能。“操作”在本方案中指上游的限价单、突破单、市价单**研究信号**，不是替用户执行订单。
5. 不把本地确定性策略、量化解释器的结论冒充 PA_Agent 等价结果。

### 1.1 “一模一样”的可验收定义

| 层次 | 要求 |
| --- | --- |
| 确定性代码 | 同一规范化 K 线、元数据、配置、历史记录和模型回复 fixture 下，特征、路由、归一化、校验结果、短路结果、通知机会判定一致 |
| Prompt/资源 | 对齐原文、文件选择、拼接顺序、消息角色、历史续接方式；只允许明确列出的元数据适配差异，不自行改写策略文字 |
| 模型请求 | 相同受支持 Provider 配置下，请求参数与上下文语义一致；不同供应商/模型输出不能直接当作算法等价证明 |
| 模型自然输出 | 模型存在非确定性；不得承诺每次在线调用逐字一致，以录制回复重放与确定性契约对照作为主要验收证据 |
| 通知 | 有效机会判定与上游一致；后台可靠投递是用户额外要求，明确作为扩展，不谎称上游已有事务队列 |
| UI | 保留 Web 形态，但关键分析字段、真实决策节点、信号类型和通知状态不能丢失或改义；不要求复制 Qt 像素布局 |

**禁止以“差不多”“同样两次调用 LLM”代替兼容性验收。**

### 1.2 优先级

- **P0**：直接改变决策或造成漏通知、伪成功的差异，必须先完成。
- **P1**：分析完整性、增量/连续性、配置、显示与记录对齐，正式宣称兼容前必须完成。
- **P2**：可选通知附件、辅助体验等，不能掩盖 P0/P1 未完成。
- 本文中 P1/P2 是实施顺序，不代表允许永久省略上游存在的功能。

## 2. 双方链路与本地双 AI 入口

### 2.1 PA_Agent 主链路

```text
已收盘 KlineFrame + indicators + previous_record
  → PromptAssembler：系统规则 + 诊断框架 + K线/几何/市场特征
  → Stage1 模型回复 → 标准化、校验、反馈重试
  → gate_result=wait/unknown：程序生成标准 WAIT，跳过 Stage2 模型
  → 其余：按 cycle/direction/spike/patterns 路由策略文件 + 经验条目
  → 续接 Stage1 对话生成 Stage2 → 标准化、校验、连续性处理
  → 保存完整/部分记录 → 展示真实 trace 与决策
  → 机会判定 + 置信度门槛 → 提醒 + 交易信号记录 + 渠道发送
```

主证据：上游 `pa_agent/orchestrator/two_stage.py`，尤其 `:765-979`；`pa_agent/ai/prompt_assembler.py`；`pa_agent/gui/main_window.py:3415-3459,4063-4180`。

### 2.2 本地 AI 分析中心

```text
数据集 → build_snapshot（本地确定性 PA 与支撑阻力）
  → 简化 Stage1 Prompt → 取 JSON/默认值归一化
  → 简化 Stage2 Prompt → 取 JSON/默认值归一化
  → _finalize_record 标记 ok → 保存/展示
  → 单次/批量没有自动通知；盯盘按 auto_notify 调回调
```

入口：`api/src/xquant/api/routes/ai.py`；核心：`api/src/xquant/ai/service.py`；多股票：`api/src/xquant/ai/coordinator.py`。

### 2.3 另一个入口：股票综合分析的 AI 解释器

`api/src/xquant/api/routes/stock_analysis.py:_with_ai`（`:229-248`）调用 `context_builder.py:build_quant_analysis_context/run_quant_analysis`。

`api/src/xquant/ai/context_builder.py:51-55` 明确限制模型只能解释量化结果，不能重新判断趋势、支撑阻力、评分或决策；还会过滤原始 K 线。这与 PA_Agent 的 AI 诊断/决策不是同一职责。

**实施决定：** AI 分析中心的 PA 引擎是本次必须对齐的权威诊断/决策来源。股票综合页可继续保留确定性量化模块和解释文字，但必须标明“量化解释”，不能声称它也是 PA_Agent 分析。若综合页展示 PA 分析或发送 PA 操作通知，必须引用同一 PA 引擎的结果及 `analysis_id`，不得另用解释器生成操作判断。

不得为了对齐而删除综合量化模块，也不得将多周期融合、资讯或本地量化分数额外注入严格兼容 Prompt。上游支持的 `htf_context` 等输入须走相同契约。

## 3. 需要调整的差异清单

### D01 / P0：Prompt 与策略知识库缺失

- 上游：`pa_agent/ai/prompt_assembler.py:765-962,1277-1331,1637-1811`；`prompt_engineering/`。
- 本地：`api/src/xquant/ai/service.py:431-489` 的两段内联 Prompt，没有等价的完整资源库和加载器。
- 实际差异：上游包括《二元决策》、诊断任务、逐棒检查、策略文件、形态判定表、术语/语言约束、尾部提醒；本地主要是字段要求与少量说明。
- 要求：按上游清单建立独立资源目录、加载器与组装模块；保持正文、命名、顺序和条件注入相同。不得用自行总结的“精简版策略”替代。
- 验收：生成资源 SHA-256 manifest；代表性 Stage1/Stage2 Prompt golden 对照；system 前缀复用和各动态块与上游一致。
- 注意：当前固定快照 `prompt_engineering/*.txt` 实测 **29 个文件、403492 字节**；数字只是快照清点，资源集合以实际加载清单为准，不写死文件数量作业务逻辑。

### D02 / P0：K 线表、指标与几何特征输入不等价

- 上游：`prompt_assembler.py:979-1050`；`pa_agent/ai/kline_features.py`；指标与 `KlineFrame` 构造链。
- 本地：`service.py:build_snapshot/_snapshot_table`（`:134-199`）；`api/src/xquant/features/indicators.py`。
- 差异：本地简表没有上游完整时间、阳阴、EMA20/ATR14 展示与逐棒几何表；初始指标种子、预热窗口、NaN/缺失量能语义也必须逐项核对，不能只看指标名称相同。
- 要求：数据适配层保留本系统数据源，但生成等价 frame，统一最新已收盘棒为 `K1`；上游表头为 `序号/时间/开盘价/最高价/最低价/收盘价/阳阴/成交量/EMA20/ATR14`。补齐 inside/outside、实体影线、重叠、ii/iii、ioi、微双、缺口、后续、量比等。
- 验收：同 fixture 的全窗口特征与渲染逐项比较；断言最后未收盘棒不进入分析。排序、去重、时区、价格精度、最小跳动和复权口径记入 fixture 元数据。
- 边界：数据来源可以不同，不意味着相同股票的不同复权/缺棒输入应产生相同输出；比较前先保证输入相同。

### D03 / P0：市场特征、阈值、周期/形态词表不兼容

- 上游：`market_features.py`、`trend_context.py`、`decision_nodes.py`、`cycle_enums.py`、`pattern_routing.py`。
- 本地：`api/src/xquant/analysis/price_action.py`；`service.py:build_market_feature_block`。
- 差异：缺铁丝网分数、区间宽度/距边界 ATR、MM 候选与 TP2 许可、尖峰余波、结构失效价等上游输入；同名方向/重叠判据亦有不同。当前形态标签不能直接作为上游路由输入。
- 上游周期顺序固定为：`spike, micro_channel, tight_channel, normal_channel, broad_channel, trending_tr, trading_range, extreme_tr`。`unknown` 在诊断/降级位置按上游处理，不是第九个可预测周期。
- 要求：在 PA 兼容引擎内采用上游特征、常量、枚举、pattern 合并和相同排序/并列规则。严格 Prompt 中移除本地独有概率/持有率等会改变模型判断的额外段落，或隔离到非兼容展示层。
- 验收：边界阈值 fixture、各周期/方向 fixture、pattern→策略路由对照。不能仅断言某些字段存在。
- 保护范围：不要直接全局替换 `price_action.py` 的规则，影响 discovery/quant 等其他功能；优先独立兼容实现/封装，让 AI 入口使用它。

### D04 / P0：Stage1/Stage2 JSON 契约及归一化不一致

- 上游：`pa_agent/ai/prompts/schemas.py`、`stage1_normalizer.py`、`stage2_normalizer.py`。
- 本地：`service.py:normalize_diagnosis/normalize_stage2`（`:1298-1452`）。
- 差异：本地将丰富诊断压缩为摘要，将操作主轴改成 `LONG/SHORT/WAIT`；很多上游字段只在 `raw` 中保留，不能被路由、UI、通知正确消费。
- 特别风险：本地 `normalize_stage2` 依次从 `action → order_type → order_direction` 取 action。直接传上游回复时，`order_type=限价单` 会成为 action，而不是 `LONG`；不能把订单类型当方向。
- 要求：上游 JSON 是核心 canonical 结构；UI 的 action 别名只能是派生视图。完整保留 gate/trace、周期、形态、逐棒摘要、diagnosis_summary 对象、终端、两个置信度、胜率、理由、三价和 TP2 等。
- 操作枚举：`order_type ∈ {限价单, 突破单, 市价单, 不下单}`；有效操作方向为 `做多/做空`。方向和订单类型分开解析。
- 验收：上游完整回复 fixture 经本地处理后 canonical JSON 一致；不能把缺失必填项补成空值再标记成功；保留 raw 与 normalized 的差异记录。

### D05 / P0：缺少同等校验、修复与反馈重试

- 上游：`json_validator.py`、`json_salvage.py`、`coherence_checks.py`、`trace_semantic_checks.py`、`node_coverage.py`、`retry_feedback.py`、`retry_policy.py`；`orchestrator/validation_retry.py`。
- 本地：`service.py:extract_json_object`（`:496-518`）与 `run_two_stage/stream_two_stage`；`extract_json_object(...) or {}` 后可继续默认归一化，`_finalize_record`（`:1770-1805`）直接写 `status=ok`。
- 影响：模型返回非 JSON、空对象或缺失重要字段，可能被包装为“成功”研究结果；与上游的可分类失败、反馈重试不同。
- 要求：复用或等价实现上游语法/Schema/语义处理、修复顺序、分类、反馈文本、重试次数与最终部分记录；失败不得触发操作通知。同步与 SSE 共享同一编排核心，不能两套各修一半。
- **默认值必须按上游真实配置迁移**：`normalization_mode=lenient`；四类 coherence/semantic/strict-bar 开关默认关闭；`retry_enabled=true, retry_max=3, retry_max_semantic=1, retry_stage2=true`。不能擅自把全部严格检查开启，再宣称和上游相同。
- 验收：录制回复序列覆盖截断、语法错、Schema 错、语义错、成功修复和重试耗尽；对比最终状态、重试请求、用量累加与部分记录。

### D06 / P0：Stage1 闸门没有短路 Stage2

- 上游：`orchestrator/two_stage.py:858-874` 附近的 gate 短路；`ai/decision_tree.py:build_stage2_gate_wait_response`。
- 本地：`run_two_stage`（`:1807-1872`）、`stream_two_stage`（`:1950-2016`）拿到诊断后继续调用 Stage2，没有等价 gate 分支。
- 要求：`gate_result=wait/unknown` 按上游生成标准不下单对象、trace/terminal 与不可预测字段；第二阶段模型请求数为 0，记录真实短路原因。
- 验收：两类 gate 在同步/SSE/批量/盯盘均不调用 Stage2、不产生机会通知；缺失/非法 gate 走上游归一化及校验路径，不任意设为 proceed。

### D07 / P0：缺少策略路由与 Stage2 对话续接

- 上游：`ai/router.py:89-246`；`prompt_assembler.py:build_stage2_continuation`；`two_stage.py:779-810,880-904`。
- 本地：`service.py:build_stage2_prompt`，使用新的两条消息，仅嵌入归一化诊断摘要。
- 差异：没有按 cycle、direction、spike_stage、alternative、patterns 选择策略文件，也没有等价保留 Stage1 assistant 回答及上下文。
- 要求：复制路由条件、文件顺序、形态 overlay、反向通道处理；`extreme_tr/unknown` 等规则按上游执行，禁止凭空统一为“通用策略”。按上游继续对话，不仅 json.dumps 摘要。
- 验收：周期×方向以及 spike/pattern/alternative 的表驱动 golden；对照完整 messages 角色顺序和正文。

### D08 / P0：本地无 Key 时静默切换另一套策略

- 上游：`gui/main_window.py:_on_startup_api_key_check/_refresh_api_key_ui_state`（`:4260-4314`），未配置时提示并限制分析。
- 本地：`service.py:run_two_stage`（`:1812-1841`）在无 Key 时调用 `_local_diagnosis/_local_stage2` 并返回 `status=ok`；SSE 同样转入“本地确定性研究模式”。
- 要求：严格兼容入口未配置 Provider 凭据时明确失败/未就绪，不返回伪 PA 结果。如保留本地演示，必须显式选择、独立 `engine/source`，且不冒充 PA 信号或触发生产操作通知。
- 注意：上游也有明确的 lite/fallback/local compatibility 分支，**不等于**本地“没 Key 自动当成功”的行为；应按实际分支移植，不能一律删除上游降级规则。
- 验收：无 Key、故意空回复、Provider 故障三类测试；只有合法配置下的指定 fallback 才能进入相应路径。

### D09 / P1：增量、持续跟踪和结构连续性没有落地

- 上游：`prompt_assembler.py:1094-1183,1333-1496`；`incremental_shift.py`；`decision_continuity.py`；GUI 的上一记录/持续跟踪选择；`two_stage.py` 的 previous_record 传递。
- 本地：`coordinator.py:BatchAnalyzer._analyze_one`（`:207-245`）每次 `run_two_stage(snapshot, settings)`，不传上一记录；`incremental_max_new_bars` 虽可配置，却未驱动等价增量流程。
- 要求：保留本地监听调度，但每个分析键维护独立 previous_record。按上游触发条件选择全量/增量、窗口重建、K 引用平移、连续性/反向冷却、重试后 keep_analysis 行为。
- 增量消息必须是：system → **当前完整窗口重新构建**的 user → K 引用已平移的前一 Stage1 assistant → 只描述新棒的增量 user。不能反复复用最初快照，造成中间 K 线丢失。
- `K<n>`、`bar_from/bar_to` 需平移；`incremental_delta` 子树不平移，其他非 K 线数字不变。
- 上游 `structure_flip_cooldown_bars` 默认 3；本地没有等价流程。
- 验收：连续至少三轮，新棒 1/多根、超过增量上限、窗口滚动、修订旧棒、切周期、重启等场景；不跨股票/周期/复权口径复用状态。

### D10 / P1：决策倾向与默认值不统一

- 上游：`ai/decision_stance.py`、`config/settings.py:37-112`。
- 本地：`service.py:AISettings/normalize_ai_settings`、`system_config/settings.py:AnalysisSettings`、`analysis/price_action.py:_resolve_stance`。
- 要求：完整传递上游四档倾向及指导块，不让 `extreme_aggressive` 被本地三档逻辑降级。胜率/RR/止损处理采用上游规则，不套用本地确定性 `min_rr=1.5` 等规则作为 PA 结果的额外裁决。
- 已核实：**双方持久化 general/analysis 的 decision_stance 默认都是 balanced**；不要误改成 conservative。上游部分低层函数缺参 fallback 不代表产品默认值。
- 已核实：双方持久化 K 线默认 100；但本地运行时 `AISettings` 默认 120、限制 60–1000，上游 GeneralSettings 允许 2–5000。应核对上游实际执行最小输入条件并统一有效值/错误行为，不能只统一配置声明。
- 上游 next-bar 默认 false，本地 true；严格 profile 的新配置默认要对齐，上线迁移不能悄悄覆盖用户已经显式保存的选项。
- 补 PromptSettings、ValidationSettings、结构冷却等缺失配置，GET/保存/运行时必须同一解释。
- 验收：四档倾向全链透传；默认配置快照与上游一致；旧配置迁移带版本和有效值说明。

### D11 / P1：经验库能力与选项缺失

- 上游：`pa_agent/records/experience_reader.py`、`prompt_assembler.py` 的 experience 渲染及 `config/settings.py:PromptSettings`。
- 本地：没有等价 reader、排序/截断/渲染和对应配置。
- 要求：按周期、方向、pattern 的排序、数量和单条字符限制对齐，明确仅供参考。可适配到本地存储，但同输入条目的选择与渲染必须一致。
- 验收：0 条禁用、非 0 开启、排序并列、超长条目；注意上游默认 `experience_max_entries=0`，默认未启用不是必须塞入案例。

### D12 / P1：决策树展示并非上游真实节点与路径

- 上游：`ai/decision_tree.py` 从《二元决策》加载节点、gate、分支与终端；`decision_nodes.py`、`decision_display.py`、GUI trace 绑定。
- 本地：`service.py:build_decision_tree_layout`（`:1537` 起）生成简化节点；有 trace 时线性连接，无 trace 时拼装趋势/结构等占位节点。
- 要求：沿用 Web 组件，但展示上游真实 node_id、question、answer、branch、next_node、skip、terminal、程序与 AI override、bar_range。缺 trace 要显示缺失，不能用占位节点伪装已校验路径。
- 验收：交易、观望、gate 短路、跳过节点、override 的 fixture 路径与上游一致。允许布局不同，不允许流程含义不同。

### D13 / P1：Provider、fallback、token 与取消行为差异

- 上游：`client_factory.py`、`deepseek_client.py`、各 Provider connector、`response_extract.py`、`provider_errors.py`、`rate_limit.py`、`token_counter.py`、`session_ledger.py`，以及 `two_stage.py` 内实际选择分支。
- 本地：`service.py:_request_payload/stream_chat_completion_events/_consume_stream_reply`，主要是统一 OpenAI 风格请求和少量流式兼容处理。
- 已确认：本地没有完整上游 Provider/lite/备用链与同等 token 会话预算、取消/重试编排。**各 Provider 的请求细节本次没有逐行穷举，不能声称已全量核验。**
- 要求：后续先生成 Provider 能力/行为矩阵，再移植实际支持配置的同等行为。关键包括 thinking/reasoning 重发、空正文处理、流式与非流式恢复、格式反馈重试与网络重试分离、fallback 出处标识、用量累计、取消后部分记录。
- 严格目标下不能把未实现 Provider 标成支持；分期发布需显式兼容清单，未完成部分仍算差异，不自动豁免。
- 验收：mock transport 重放，不依赖真实额度；不能在失败时偷偷换成无关本地策略。

### D14 / P1：追问会话与分析记录不等价

- 上游：`orchestrator/free_chat.py:FreeChatSession`，锚定分析记录、保存多轮历史，支持最新已收盘快照和用量累计；`records/schema.py/pending_writer.py/trade_logger.py`。
- 本地：`service.py:build_followup_prompt`（`:490-494`）每次将整个 record 加单个问题拼成两条消息；`routes/ai.py:followup_ai` 不提供等价会话历史。
- 要求：保留原始回复、标准化回复、重试/错误、策略文件、历史版本、增量来源、用量、追问轮次、信号事件。前端重载记录不应重发操作通知。
- 记录 ID 需有并发唯一性；当前 `ai-<毫秒时间戳>` 不宜作为多股票可靠去重的唯一依据。
- 验收：多轮追问上下文、最新行情注入、重载记录、部分失败与并发 ID。追问解释不自动当作新的操作决策；产生新操作应通过正式分析契约。

## 4. 重点：操作机会与通知

### 4.1 上游真实触发语义（已核对调用链）

1. `pa_agent/gui/order_opportunity.py:15-47`：只有 `order_type` 为 `限价单/突破单/市价单` 才算机会；门槛 >0 时，解析 `trade_confidence` 并与门槛比较，缺失/非法不得通过。
2. `main_window.py:3415-3459`：Stage2 展示后调用 `_maybe_alert_order_opportunity`，通过才 `_spawn_post_order_followup`。
3. `main_window.py:4063-4180`：全局机会提醒开关、提示音、弹窗、切决策页；后台保存信号与图，然后发飞书，PushPlus 启用时也发。
4. `notify/feishu_notifier.py:send_order_signal`：检查 enabled/webhook，支持签名；有 app 凭据与图时可上传图片，否则发文字卡片。
5. 上游发送失败主要记录 warning；这**不是**可靠投递保证。本项目要满足“需要操作时通知本人”，应补下述可恢复投递机制。
6. 上游远程推送实际被桌面提醒开关链路间接控制。Web 迁移要保留机会/阈值语义，但不应让“关闭页面声音或弹窗”意外关闭后台推送；这一解耦属于明确的 Web 适配扩展。

### 4.2 当前系统的通知缺口

| 场景 | 当前代码证据 | 现状与必须调整 |
| --- | --- | --- |
| 单次非流式 | `routes/ai.py:analyze_ai` | 只保存 record 后返回，无自动通知。改为统一完成事件触发 |
| 单次 SSE | `routes/ai.py:analyze_ai_stream` | done 时只保存，页面完成后也不自动发。不能依赖浏览器最终回调投递 |
| 批量分析 | `routes/ai.py:batch_analyze` | 各条只持久化，不逐条自动发机会通知 |
| 常规实时盯盘 | `coordinator.py:640-644`、`api/app.py:notify_monitor` | 必须 `_auto_notify`、回调存在且 status=ok 才调用；回调返回的 sent=false 不落入状态，异常只 warning |
| 前端启停盯盘 | `AIAnalysisPage.tsx:650-708` | UI 启动时传 `auto_notify=config.feishu.enabled`，正常路径已有接线，不应误称“全部盯盘从不通知” |
| 一次盯盘检查 | `AIAnalysisPage.tsx:716-731` | 未运行时先用 **auto_notify=false** 启动，之后 run-once；本轮且后续该实例可能静默不通知 |
| 手动发送 | `AIAnalysisPage.tsx:640-647` | 仅按钮调用 sendFeishu，不满足自动通知诉求；保留为手动重发，不是主链路 |
| 机会识别 | `notifications/feishu.py:_action_label/send_feishu_message` | 主要从 action 判断 WAIT，不能等价表达上游订单类型机会；“未知 action 但非 WAIT”可能误入发送 |
| 置信度 | `feishu.py:send_feishu_message` | 优先 confidence，再 trade_confidence；本地标准化还可回退 diagnosis_confidence。须专用 trade_confidence，避免诊断把握度代替交易把握度 |
| 投递失败 | `feishu.py:193-244`、`coordinator.py:640-644` | 无持久化投递队列、失败重试与用户可见结果闭环；分析 ok 不等于本人收到 |
| 附件与备用渠道 | 本地 `FeishuSettings` vs 上游 notifier | 本地配置有 app_id/app_secret，但现发送函数未使用图片上传；无等价 PushPlus 通路 |

### 4.3 统一机会判定与通知真值表

所有入口共享纯函数 `has_order_opportunity(canonical_decision, confidence_threshold)`（名称建议），不能 UI、后端、飞书各自实现一套。

| 输入/状态 | 机会通知行为 |
| --- | --- |
| 校验通过，限价/突破/市价单，trade_confidence 达配置门槛，渠道有效 | 自动创建投递事件并发送；无需再次点击 |
| `不下单` 或 gate 短路 | 不发“可操作”通知；可保留独立观望摘要选项 |
| trade_confidence 低于门槛 | 跳过并记录可见原因；默认门槛 0，不额外加 70/80 等主观门槛 |
| 非法 JSON、校验耗尽、Provider 失败、用户取消、演示数据 | 禁止操作通知；可发送独立系统错误告警 |
| enabled=false 或缺 webhook | 明确标记 disabled/config_missing；页面显示未就绪，不假报已发送 |
| 同一事件重复完成、重复 SSE done、刷新/重载历史 | 不重复创建通知；允许用户显式手动重发 |
| 下一根 K 线产生新的有效分析 | 默认按上游“每次有效结果”处理，不能以 symbol/方向相同为由永久屏蔽后续机会 |
| 相同方向但 entry/stop/TP/订单类型变化 | 不能被过宽去重漏掉；通知展示方案更新 |
| 网络/平台业务失败 | 有限重试、最终失败可见、可手动重试；不把分析成功改成投递成功 |

说明：上游的 order helper 本身只判订单类型/置信度；本方案要求它在**已通过对应校验的结果**上执行，额外投递状态不是修改交易策略。

### 4.4 后端可靠投递方案（用户要求的扩展）

推荐统一分析完成服务：

```text
所有正式分析入口
  → 核心分析引擎返回 canonical record
  → 持久化 record + 同步记录通知意图（优先同一事务）
  → 幂等写入 notification outbox
  → dispatcher 调用 Feishu / 可选 PushPlus
  → 保存 sent/failed/skipped + 回执摘要
  → UI/日志展示投递状态和手动重试
```

实施要求：

- 唯一键至少包括 `analysis_id + event_kind + channel`；`analysis_id` 本身并发唯一。跨入口同一任务重放应复用 execution/idempotency 标识，不能每次重试都新造“新机会”。
- 多股票引擎可保留，但不能只由 MonitorManager 独占自动通知。单次、SSE、批量、持续跟踪、run-once 都通过同一完成服务。
- outbox 需持久化，可在已有存储机制内实现；记录写入与投递意图必须避免“一边成功一边丢失”。若无法事务，设计明确的幂等补偿扫描。
- 状态至少包括 pending/sending/sent/retry_wait/failed/skipped，记录 attempts、last_error、next_attempt_at、provider_receipt、sent_at；分析状态和投递状态分开。
- 重试为有限、可配置的退避策略；不可永久自旋。平台非成功业务码同样是失败，不只检查 HTTP 200。
- Webhook 通常不提供端到端 exactly-once 保证。超时但平台已接收时，重试可能重复；采用 at-least-once、事件编号与本地幂等，并在文档/界面如实说明。
- 通知工作不阻塞分析/SSE 主输出；后端成功结果应可在浏览器断开后继续完成持久化和投递。若要保证分析本身也不随 SSE 断开取消，应先落 durable job，再由 SSE 订阅进度，不能仅把 send 挪进生成器 finally。
- UI 关闭声音/弹窗、离开页面或加载历史，不应影响后台渠道投递。停止监听则不再创建新分析任务；已创建通知按明示策略处理。
- 需要用户填写本人可接收的渠道地址并做一次获授权的测试发送；本次审计没有读取/验证任何真实 webhook，不声称已打通个人接收。
- 仅做字段脱敏和配置存在性检查，不把密钥、完整 webhook、Authorization 或敏感 Prompt 写入日志。

### 4.5 通知内容与 Web 提醒

至少展示：股票代码/名称、周期、最后已收盘棒时间及行情截至时间、order_type、order_direction、entry、stop、TP1、TP2、trade_confidence、诊断摘要、下个周期预期、watch_points、失效条件、模型与分析 ID、通知产生时间和研究用途声明。

上游可选图表上传及 PushPlus 按原选项支持，不配置时不阻塞飞书文本。Web 内提醒至少提供醒目操作提示与进入详情；声音/浏览器通知需用户许可且可关闭，不能作为唯一通知渠道。

## 5. 实施边界与 File Touch List

以下为**预计触达清单**，编码前须按当前代码确认；不是让 Agent 无差别重写全部文件。

| 路径 | 改动职责 |
| --- | --- |
| `api/src/xquant/ai/service.py` | 收缩为兼容 facade；保留 API 可用入口，统一核心编排，去掉静默替代和伪成功 |
| `api/src/xquant/ai/pa_compat/`（建议新增） | Frame 适配、特征、Prompt、策略路由、canonical 契约、normalizer、validator、continuity、重试编排 |
| `api/src/xquant/ai/pa_compat/resources/`（建议新增） | 固定版本原文资源、校验 manifest、来源版本；必须随 wheel/Docker 正确打包 |
| `api/src/xquant/ai/coordinator.py` | 多目标状态与 previous_record 隔离；调用同一核心/完成服务，不变更数据采集职责 |
| `api/src/xquant/api/routes/ai.py` | 单次/SSE/批量/盯盘统一完成语义、错误契约、幂等标识 |
| `api/src/xquant/api/app.py` | 生命周期注册 outbox dispatcher；替代监控专属通知接线 |
| `api/src/xquant/notifications/feishu.py` | canonical 订单语义、完整卡片、业务码解析、可选图片上传；transport 可替换便于测试 |
| `api/src/xquant/notifications/`（按需新增） | 共享机会判定、投递服务、outbox、PushPlus adapter |
| `api/src/xquant/system_config/settings.py` | Prompt/Validation/Provider/通知选项与版本迁移，统一运行时有效值 |
| `api/src/xquant/registry/database.py`、`sqlite.py` 及必要 migration | 分析版本、通知意图/状态、幂等唯一约束；遵循已有双存储接口 |
| `api/src/xquant/api/routes/stock_analysis.py`、`ai/context_builder.py` | 明确量化解释器身份；PA 展示引用统一结果，不另造 PA 操作信号 |
| `web/src/types.ts`、`api/client.ts`、`utils/aiRecord.ts` | canonical 类型/派生视图、投递状态、API 契约 |
| `web/src/pages/AIAnalysisPage.tsx` | 补操作机会提醒、真实订单类型、阈值、通知就绪/结果与重试；修复 run-once 静默关闭通知 |
| `web/src/components/ai/DecisionVisualization.tsx` | 展示真实 trace/节点/短路，不以简化占位冒充上游路径 |
| 现有模型/通知配置页与直接相关组件 | 依据实际定位扩展表单，不能借机重构整站 |
| `api/tests/unit/`、直接对应 Web 测试 | 最小范围兼容与通知验收 fixtures，见第 7 节 |
| `THIRD_PARTY_NOTICES.md`、必要许可证文件、打包配置 | 上游来源/commit/许可证/改动说明；保证资源存在于发布制品 |

不应修改：行情数据中心采集职责、既有数据集 ID 体系、多股票监听拆分、AlphaLab、资讯引擎、非 AI 的全局量化规则。确需共享改动时先证明直接必要性并另列影响，不顺手重构。

## 6. 建议执行顺序（每阶段独立可回退）

| 阶段 | 工作与完成标准 | 回退策略 |
| --- | --- | --- |
| A：冻结基线 | 取固定 upstream commit，生成资源/行为 manifest、Provider 矩阵、输入 fixture、双入口职责与配置迁移表；检查许可证边界 | 纯资源/测试提交，可独立撤销 |
| B：兼容核心 | D01–D08：输入/特征/Prompt/路由/Schema/校验/反馈重试/闸门/同一编排；golden 与上游对照通过 | 兼容引擎独立模块，旧记录保留来源；不可将旧引擎伪装为兼容回退 |
| C：状态与配套 | D09–D14：增量、连续性、配置、Provider 适配、追问、真实 trace 与记录 | 按引擎版本隔离 previous_record，失败时明确暂停兼容模式 |
| D：通知闭环 | 一致机会判定、所有入口完成事件、outbox、发送回执、有限重试、前端可见；run-once 漏发修复 | 停止 dispatcher 不丢 outbox；恢复后可继续；显式通知禁用状态 |
| E：交付验证 | 第 7 节对应测试通过；对接本人渠道的获授权测试；形成兼容矩阵与未完成清单 | 不满足门槛不宣称“完全一致”，保留可回退配置/存储迁移 |

建议阶段 B 与通知纯函数/测试准备可并行；实际发送接入依赖稳定 canonical Schema。不要在 Schema 尚未定稿时两名 Writer 同改 service/config/锁文件。

## 7. 定向验收标准

本次仅产出方案，以下是**后续编码 Agent 必须执行的验收**，不是已经通过的测试。

### 7.1 兼容引擎

- [ ] **C01** 资源 manifest、加载集合、Prompt 标题/顺序/动态文本与上游一致；只屏蔽明示非语义元数据。
- [ ] **C02** 最新已收盘 K1、时间/量能/EMA/ATR/逐棒几何/市场特征对照；含缺失量能与指标预热边界。
- [ ] **C03** 八周期与方向、spike_stage、alternative、pattern overlay 路由文件序列一致。
- [ ] **C04** gate wait/unknown 的 Stage2 调用次数为 0，完整短路 JSON 与上游一致；proceed 不错误短路。
- [ ] **C05** Stage1/Stage2 canonical JSON 保留字段；限价/突破/市价/不下单全覆盖；类型与方向不混淆。
- [ ] **C06** 语法/截断/Schema/语义错误的修复、重试 feedback、次数、状态与上游相同；非法响应不标 ok。
- [ ] **C07** lenient 与可选严格开关分别对照，不能仅用更严格校验“代替兼容”。
- [ ] **C08** 三轮以上增量：全窗口更新、K 引用平移/跳过 incremental_delta、超阈值回全量、旧棒修订与状态失效。
- [ ] **C09** 四档 stance、默认 next-bar false、其他 Prompt/Validation 默认、配置迁移与有效输入限制对照。
- [ ] **C10** 同结构反向冷却、三价/RR/胜率、trade/diagnosis confidence 与上游 normalizer/validator 一致。
- [ ] **C11** 同步、SSE、批量、盯盘在相同模型回复序列下产生相同 canonical 结果；并发股票不串状态、不冲突 ID。
- [ ] **C12** no-key 明确未就绪；Provider/fallback/lite 按能力矩阵重放；取消/失败有部分记录，用量不丢。
- [ ] **C13** 决策树真实节点与路径、追问多轮历史、旧版本记录只读展示、量化解释器标签与 PA 来源分离。
- [ ] **C14** wheel/容器发布制品包含所需资源；资源缺失明确失败，不退回简化 Prompt。

### 7.2 操作通知

- [ ] **N01** 单次普通、SSE、批量、自动盯盘、run-once、持续跟踪分别出现有效机会时，均自动创建投递事件。
- [ ] **N02** 方向 LONG/SHORT 派生准确；三种订单类型均通知；不下单、gate、错误、取消、演示不误报操作。
- [ ] **N03** 正好等于门槛、低于门槛、门槛 0、非法 trade_confidence；不得用 diagnosis_confidence 顶替。
- [ ] **N04** 重复 done/同 execution 重放只创建一条同渠道事件；新棒新机会、参数变化不会被过宽去重吞掉。
- [ ] **N05** disabled/缺 webhook/网络失败/HTTP 200 业务失败/无效 JSON/超时均有真实状态；只有平台成功回执才标 sent。
- [ ] **N06** 服务重启后 pending/retry 可恢复，有限退避可验证；发送成功后的重复调度不重复发。
- [ ] **N07** 浏览器离开、SSE 断开、关闭声音不漏后台投递；历史载入不重发；手动重发有独立审计。
- [ ] **N08** run-once 不再暗设 auto_notify=false；暂停/恢复监控与通知配置改变时有效值可见。
- [ ] **N09** 卡片核心字段、TP2/失效条件/时间/模型/分析 ID 正确；签名和可选图片失败降级合理；敏感字段脱敏。
- [ ] **N10** 使用 mock transport 完成上述自动化后，再经用户授权对本人渠道做一次测试，提供时间、analysis/event ID 和脱敏回执。没有授权不真实发送。

建议最小测试落点：扩展 `api/tests/unit/test_ai_core.py`、`test_ai_records.py`、`test_pa_agent_features.py`、`test_monitor_schedule.py`，新增仅 PA 兼容与通知 outbox 的定向测试。`test_ai_context_builder.py` 只覆盖解释器身份边界；Web 只测 AI 页面/相关组件。**不因本方案自动跑全仓测试、全量 lint/build 或无关业务测试。**

## 8. 来源、许可证与复现证据

### 8.1 版本复现

```bash
git clone https://github.com/rosemarycox5334-debug/PA_Agent.git <reference-dir>
git -C <reference-dir> checkout --detach 222badb6c34afae8daa59a47cbee1cdd59db05dd
git -C <reference-dir> rev-parse HEAD
```

不要使用审计时 `/tmp/quant-sentinel-pa-agent-reference` 作为长期运行依赖；这只是本次阅读位置。

已核验关键文件 SHA-256：

| 上游文件 | SHA-256 |
| --- | --- |
| `pa_agent/ai/prompt_assembler.py` | `976a5d6d5876474ebc4a5ee38a6a8d570addd69861f72f075d9ca3dbf671005c` |
| `pa_agent/ai/json_validator.py` | `f7f263e6ee5dd4858e6019bcd59a44074fae522f4c11ded2694d657c7fbac312` |
| `pa_agent/orchestrator/two_stage.py` | `bf5ef1dd2f96c5dc91209d91b160fa07c8973396eda3e59b83874b3c645638cc` |
| `LICENSE` | `420086a8dd2b68fdcc824a588cc3d93524a2963bb4aecbc4dfaf1b89eb9b9ad4` |

### 8.2 许可证实施前置条件

固定上游 `LICENSE` 文件标题为 **GNU AFFERO GENERAL PUBLIC LICENSE Version 3**，并标注 `SPDX-License-Identifier: AGPL-3.0-or-later`，不是普通 MIT 或 GPL 声明。本地 `THIRD_PARTY_NOTICES.md` 目前记录的是 AlphaMaster，而非本次 PA_Agent。

本次没有复制上游业务代码或 Prompt 资源进入项目。后续若为“原文/代码一致”引入上游代码、策略文本或资源，必须先确认项目发布/部署方式的许可要求，保留来源、许可证、版权和修改声明，并更新 PA_Agent 专属 notice。不能将复制后的实现仍表述为“未包含任何上游代码/资源”的完全独立实现。

这是来源与交付约束，不构成许可证合规法律意见；编码 Agent 不得自行宣称已完成法律审核。

## 9. 交给编码 Agent 的执行摘要

> 目标：在 quant-sentinel 中建立固定 `PA_Agent@222badb6c34afae8daa59a47cbee1cdd59db05dd` 的兼容 AI 分析引擎，除数据集和多股票监听拆分外，不自行改写股票分析规则。按 D01–D14 与 C01–C14 实施并留下对照证据。所有正式分析入口共享 canonical 结果与完成服务；符合上游订单类型/置信度规则的操作机会，在有效启用渠道自动通知本人，按 N01–N10 验收。可靠投递/幂等/可见失败是本项目明确扩展，不改变上游策略判断。保护现有非 AI 功能和用户设置，不连接券商、不实际交易、不未经授权发送真实通知。先检查当前 Git 和文件状态，只改本任务文件，分阶段提交并报告未完成兼容项。

### 完成定义

- 引擎/资源/配置/Provider 兼容矩阵没有被悄悄删除的必需项；未知项需有明确结果。
- 全部适用 C/N 定向测试通过，提供测试范围和真实结果，不拿在线模型的一次相似回答当兼容证据。
- 有效机会无需页面按钮即可产生可追踪的自动通知；禁用/缺配置/失败都不会假报成功。
- 用户保留现有数据集、多股票监听与 Web 使用方式。
- 文档状态更新为已实施并附实际改动/版本/验收证据；届时按仓库规则归档。本次方案尚未实施，**现在不归档**。
