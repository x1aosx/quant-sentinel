# X-Quant v2：策略来源审查与整合决策

> 核查日期：2026-09-08。本文审查公开源码与策略文本，不是策略收益认证。  
> 本次已阅读两个指定仓库的核心策略路由、价格行为文本、支撑阻力检测、概率模型和标签/评测逻辑；另外核查 vn.py、Qlib、动量原始资料及相关验证工具。  
> 本次未在本地安装或完整执行上游仓库，未获取用户实际行情，未复现上游收益或预测指标。远端 `main` 是可变引用，部分检索快照可能不同步；未可靠取得固定提交 SHA。实施 P0 必须重新固定 SHA 并核对本文映射，禁止把本文视为固定版本全量审计。

## 1. 结论：提取策略资产，而不是拼装两个网站

PA_Agent 提供的主要是**价格行为策略知识、市场诊断路由、结构特征与决策校验**；支撑阻力仓库提供的主要是**关键位计算、事件标签与统计预测模型**。两者互补，但均不能直接充当本项目完整的持仓与实盘执行系统。[R01–R14]

本项目将这些资产拆成五类管理：`feature`、`regime_detector`、`setup`、`forecast_model`、`risk_policy`。随后组装为完整的 `strategy_version`。一个指标、一个提示词、一个预测器或一段股票分析文字，均不自动算作已完成的交易策略。

策略证据等级统一为：

| 等级 | 含义 | 本次交付能确认到哪里 |
|---|---|---|
| E0 | 找到来源 | 已有来源目录 |
| E1 | 已读关键逻辑并形成规格 | 核心资产达到；本文明确各自边界 |
| E2 | 本项目单元测试、因果性和状态回放通过 | 尚未达到，交给 Codex 实施 |
| E3 | 本项目有成本回测与独立样本外证据 | 尚未达到 |
| E4 | 仿真/影子运行与交易约束验证通过 | 尚未达到 |
| E5 | 有可审计的真实成交记录 | 尚未达到；也不等于未来有效 |

**“原仓库带回测”“开源社区使用广”“模型输出 80%”不能替代 E3–E5。**

## 2. PA_Agent：实际提取了什么

### 2.1 代码路径到策略资产

| 路径 | 实际逻辑 | 本系统映射 |
|---|---|---|
| `pa_agent/orchestrator/two_stage.py` [R02] | 行情校验→第一阶段诊断→选策略文本→第二阶段方案→输出校验/归档；闸门失败可短路 | `ResearchPipeline` 与不可变决策记录，不移植桌面编排 |
| `pa_agent/ai/router.py` [R03] | 通道、尖峰、交易区间等状态映射策略文件，并叠加形态文件 | `RegimeRouter`；生产路由由确定性特征产生 |
| `pa_agent/ai/decision_nodes.py` [R04] | 程序方向特征、部分决策节点和允许的模型覆盖 | 可审计的方向特征；硬风控不允许 LLM 覆盖 |
| `pa_agent/ai/market_features.py` [R05] | 区间位置、K 线重叠、结构枢轴、突破回收/测试、H/L 候选、测量目标 | `PriceActionFeatures`，逐项重新写契约与测试 |
| `prompt_engineering/上涨通道交易策略.txt` [R06] | 顺势回撤、EMA20、H1/H2、旗形和回踩，明确自身不管理持仓 | `pa_h2_pullback`；仓位和退出由本项目新增 |
| `prompt_engineering/震荡区间交易策略.txt` [R07] | 有方向的区间边缘交易；中部与无方向默认等待 | `biased_range_rejection`，不是无条件双边网格 |
| `prompt_engineering/文件18-突破失败与突破测试.txt` [R08] | 区分试探、突破、失败、回测、再次恢复 | `breakout_retest` 有限状态机 |
| `prompt_engineering/文件19-H1H2-L1L2计数.txt` [R09] | 两段回撤与两次入场尝试，计数有重置条件 | 严格双腿状态机，而非突破次数累加 |
| `prompt_engineering/文件17-止损和止盈与仓位管理.txt` [R10] | 下单前价格几何与交易者方程，文本明确禁止持仓管理 | 借鉴结构约束，不继承其全部止损调整规则 |

### 2.2 保留的交易思想

保留“先判环境、再找形态”；保留趋势中顺势回撤、突破后等待回测、区间中部回避和无清晰机会时等待。保留信号必须解释到具体 K 线与结构位的要求。[R03][R06–R09]

新系统**不以大模型的文字形态诊断作为必须依赖**。日线核心策略离线可运行。LLM 可为候选解释、生成研究假设与复盘提供帮助，但不得制造权威价格、实证胜率或修改硬风控。

### 2.3 预测逻辑的真实定位

已审阅的两阶段执行路径是：把结构化行情、策略文本及可选经验记录交给语言模型，再校验其结构化输出。下一根 K 线或下一周期的预测属于这一流程中的模型判断，不能因为 JSON 校验通过就当作已校准概率。[R02]

本项目保留两种彼此隔离的记录：`llm_hypothesis` 保存原始模型判断，`statistical_forecast` 保存用冻结数据、明确标签训练并经样本外评测的概率/收益分布。任何展示给用户的百分比必须标注类型、目标、周期与验证状态。

## 3. 支撑阻力仓库：实际提取了什么

### 3.1 检测器、特征与统计模型

| 资产 | 已核查逻辑 | 集成决定 |
|---|---|---|
| V1 密度检测 [R12] | 旧成交量分箱基线，有跨箱重复累计问题 | 只作为历史对照，不作为生产默认 |
| V2 融合检测 [R12] | ATR 尺度分箱、衰减成交量/收盘密度、枢轴候选、历史触及 | 提取方法，分离位置、强度和概率 |
| V3 融合检测 [R12] | 候选定位与排序拆分；排序主要由历史事件数和陈旧度构成 | 首要参考检测器；必须复验而非照抄效果 |
| 因果枢轴 [R15] | 枢轴发生时间与确认时间分离，仅使用已确认枢轴 | 提升为全系统时间契约 |
| 双概率模型 [R13] | 标准化后的带正则逻辑回归；触及/守住×支撑/阻力分别建模 | 保留任务分解，修正事件条件与评测设计 |
| 事件标签 [R14] | 首次触及、反应/突破、未决及后续价格统计 | 拆成“区域研究标签”和“真实执行交易标签” |
| 走查/对照 [R16] | 截止时点切片；控制输出数量和距离范围 | 保留因果回放、公平对照和消融 |

V3 源码中的默认排序形式可概括为 `log1p(已判定历史事件数) + 0.8 × 陈旧度`。这只是**排序分**。同文件名为 `p_stall` 的字段由若干特征加权得到，并不是自动约束在 0–1 的概率。本项目分别命名为 `sr_structure_score`、`stall_score`，未经校准不得加百分号。[R12]

概率模型的主要特征为：距区域距离/ATR、距离平方、区域宽度/ATR、历史事件数量、陈旧度、量价密度与 ATR/价格。加载器检查特征顺序，模型缺失时可以不输出概率。这些都是值得保留的工程思想。[R13]

### 3.2 必须修正的统计解释

上游训练脚本把“触及但未判定为守住或突破”的样本排除在守住模型之外，因此该模型实际估计的是：

```text
P(守住 | 已触及，且结果已经判定，决策时特征)
```

而不是对所有已触及事件的守住概率。它不能直接与触及概率相乘后解释为“这笔交易获利概率”。上游按股票代码划分互斥训练/测试池，检验的是一定程度的跨标的泛化，不能替代训练早于测试的时间外验证。[R17]

新系统保留所有**结果窗口已完整结束**的触及样本，把结果分为 `hold / break / timeout`；窗口尚未完整结束属于 `censored`，不能当作失败、未触及或普通超时。详见策略规格中的预测任务定义。

### 3.3 关键位研究不等于成交回测

标签代码中，部分事件用最高/最低价与收盘价混合判定，同一根同时命中时偏向 `hold`；其收益代理又采用触及棒收盘作为入口。这样的区域研究口径不自动证明用户在看到收盘结果后仍能以该价格成交，也不证明触及棒内的价格顺序可执行。[R14]

本项目默认采用**收盘形成信号、下一交易会话执行参考**，研究日线模式不假设盘中保护单能够成交。另有授权盘中数据与可验证执行条件时，建立独立执行版本，不混合成绩。

## 4. 需要进入实施缺陷清单的事项

以下区分“从代码可观察的事实”和“本项目的处理”。除数学关系外，均未宣称已在上游完整环境复现。

| 编号 | 观察/风险 | 严重度与本项目要求 |
|---|---|---|
| A01 | 策略文本里有主观成功率与模型估计胜率 [R08][R10] | P0：不得作可交易概率；统计模型单独训练 |
| A02 | 风控文本包含 RR>1 时扩止损的规则 [R10] | P0：不继承；止损以结构为依据，风险靠数量控制，不为凑比例扩风险 |
| A03 | 程序 H/L 候选计数不要求每次之间有完整回撤腿 [R05][R09] | P0：新 H2 采用明确状态机，连续上涨不能误识别二次回撤 |
| A04 | 枢轴端点候选与确认枢轴容易混用 [R05][R15] | P0：`occurred_at / confirmed_at` 分开，未确认值不进入交易状态 |
| A05 | PA 的铁丝网一项比较“包络宽度/平均单棒宽度 <0.3” [R05] | P1：合法 OHLC 下该比值至少为 1，此项不可作为有效过滤；独立重写并测试 |
| A06 | SR 的 `p_stall`、`edge` 名称易被当作概率或可实现收益 [R12] | P0：改名并记录量纲，不做虚假百分比展示 |
| A07 | 守住训练排除未决、训练/测试仅按股票划分 [R17] | P0：增加超时类别、成熟窗口与时间外验证 |
| A08 | 区域研究的同棒结果/入口代理不等于实际交易 [R14] | P0：执行引擎独立计算成本、可卖数量、跳空与实际收益 |
| A09 | 走查末端未必留出“等触及+反应”完整窗口 [R14][R16] | P0：按 `label_end_at` 成熟性筛选，而非只减一个 horizon |
| A10 | 未来几何召回计算遇到无同侧预测时跳过某些真值 [R14] | P1：新召回分母包含该侧全部真值；空输出不能提高召回 |
| A11 | 已查防未来测试未覆盖全部正式检测器，部分缺数据分支返回成功 [R18] | P0：正式检测器参数化测试；skip≠pass，无测试覆盖不能晋级 |
| A12 | 上游实验解释中使用测试结果选择因子/分数 [R12] | P1：不是断言所有结果无效；新系统必须留真正未参与选择的最终样本 |
| A13 | 主分支变化、缓存快照可能不一致 | P0：登记 SHA、文件哈希、抓取时间及差异，不能自动跟随远端 main |

A05 的依据是数学关系：同一组合法 K 线的整体最高价减最低价，不小于组内任一单棒的高低差，所以也不小于这些差的平均数。它只指出该条件不可触发，不代表整个市场诊断模块无用。

## 5. 社区策略如何整合，而不是堆指标

| 来源 | 核查到的可用逻辑 | 在本项目的位置 | 不继承的假设 |
|---|---|---|---|
| vn.py `TurtleSignalStrategy` [R19] | 入场/退出通道、ATR 风险与阶梯加仓示例 | `donchian_20_10_long` 简单基线；ATR 风控参考 | 不直接继承双向交易、加仓与固定手数 |
| vn.py `BollChannelStrategy` [R20] | Boll 通道、CCI 方向过滤、ATR 跟踪退出 | 独立挑战者 `boll_cci_long` | 不把原 15 分钟参数直接当日线最优值 |
| Kenneth French 动量构造 [R21] | 按过去表现排名，原构造使用滞后收益、跳过最近月 | `momentum_12_2_long` 月度基线；主策略的 RS 因子另作短周期改写 | 长多改写不等于原多空因子，不假设 A 股同样有效 |
| Qlib Alpha158 [R22] | 多周期价格/量价特征集合 | 预测研究的可选特征组 | 158 个特征不等于 158 个盈利策略 |
| Qlib LightGBM 工作流 [R23] | 配置化特征处理、训练、验证、测试与记录 | 预测挑战者，先与逻辑回归/线性基线比较 | 不直接用示例时间窗、模型参数和成绩 |
| Qlib TopkDropout [R24] | 按得分持有 Top-K、限制每次替换数量 | 月度/周度排名组合的换手控制参考 | 不把分数解释为上涨概率，不照搬风险仓位 |
| Freqtrade 偏差检查 [R25][R26] | 未来数据检查、指标启动长度一致性分析 | 策略管理中的必经测试 | 不引入其数字资产执行假设；“没发现”不等于数学证明 |
| MLflow Registry [R27] | 工件版本、血缘和版本别名 | 策略/模型注册中心的设计参考；可选适配器 | 首版不强制部署单独 Registry 服务 |

**主策略只整合角色互补的模块**：SR 负责位置，PA 负责事件确认，动量负责候选顺序，ATR 负责资金风险。Boll/CCI、Donchian、机器学习作为独立基线或挑战者，经过消融证明有增益后才参与主组合。不得因为“指标更多”而自动提高置信度。

## 6. 许可、代码获取与隔离策略

PA_Agent 许可文件声明 AGPL-3.0-or-later，SR 仓库提供 GPL-3.0 许可；vn.py CTA 模块和 Qlib 提供 MIT 许可。[R28–R31]

本交付不包含复制的上游代码或整篇策略提示词。默认 `reference_only`：依据新规格独立实现，记录借鉴来源。若要实际导入代码，必须检查具体文件及依赖许可、保留所需声明，并确认组合、修改、分发或远程使用所涉及的义务。隔离进程不是规避许可的保证；把实现改写或改名也不能自动推定没有许可义务。

代码获取流程：用户允许的仓库→固定提交→只读源码区→依赖/许可/网络行为检查→合成夹具测试→受限子进程→人工批准。`git clone`、安装依赖、运行安装脚本和执行策略是不同权限。网页提供一个仓库地址，不代表允许它读取本地密钥、持仓、文件系统或发送通知。

## 7. 来源索引

以下均为本次阅读或核查的第一方源码/官方资料。网页可随版本变化；实施时记录最终抓取结果。策略文本中的经验性收益/概率说法不视为经本项目验证的事实。

| 编号 | 来源与链接 |
|---|---|
| R01 | [PA_Agent 仓库](https://github.com/rosemarycox5334-debug/PA_Agent) |
| R02 | [PA 两阶段执行源码](https://raw.githubusercontent.com/rosemarycox5334-debug/PA_Agent/main/pa_agent/orchestrator/two_stage.py) |
| R03 | [PA 策略路由](https://raw.githubusercontent.com/rosemarycox5334-debug/PA_Agent/main/pa_agent/ai/router.py) |
| R04 | [PA 决策节点](https://raw.githubusercontent.com/rosemarycox5334-debug/PA_Agent/main/pa_agent/ai/decision_nodes.py) |
| R05 | [PA 程序结构特征](https://raw.githubusercontent.com/rosemarycox5334-debug/PA_Agent/main/pa_agent/ai/market_features.py) |
| R06 | [上涨通道策略文本](https://raw.githubusercontent.com/rosemarycox5334-debug/PA_Agent/main/prompt_engineering/上涨通道交易策略.txt) |
| R07 | [震荡区间策略文本](https://raw.githubusercontent.com/rosemarycox5334-debug/PA_Agent/main/prompt_engineering/震荡区间交易策略.txt) |
| R08 | [突破失败与突破测试文本](https://raw.githubusercontent.com/rosemarycox5334-debug/PA_Agent/main/prompt_engineering/文件18-突破失败与突破测试.txt) |
| R09 | [H1/H2 与 L1/L2 文本](https://raw.githubusercontent.com/rosemarycox5334-debug/PA_Agent/main/prompt_engineering/文件19-H1H2-L1L2计数.txt) |
| R10 | [下单前止损止盈文本](https://raw.githubusercontent.com/rosemarycox5334-debug/PA_Agent/main/prompt_engineering/文件17-止损和止盈与仓位管理.txt) |
| R11 | [SR 仓库](https://github.com/rosemarycox5334-debug/Detect_support_and_resistance_levels) |
| R12 | [SR 检测器源码](https://raw.githubusercontent.com/rosemarycox5334-debug/Detect_support_and_resistance_levels/main/src/srlab/detectors.py) |
| R13 | [SR 概率模型](https://raw.githubusercontent.com/rosemarycox5334-debug/Detect_support_and_resistance_levels/main/src/srlab/probability.py) |
| R14 | [SR 标签与几何评测](https://raw.githubusercontent.com/rosemarycox5334-debug/Detect_support_and_resistance_levels/main/src/srlab/labeling.py) |
| R15 | [SR 枢轴确认](https://raw.githubusercontent.com/rosemarycox5334-debug/Detect_support_and_resistance_levels/main/src/srlab/pivots.py) |
| R16 | [SR 走查逻辑](https://raw.githubusercontent.com/rosemarycox5334-debug/Detect_support_and_resistance_levels/main/src/srlab/walkforward.py) |
| R17 | [SR 概率训练与切分](https://raw.githubusercontent.com/rosemarycox5334-debug/Detect_support_and_resistance_levels/main/scripts/train_probability.py) |
| R18 | [SR 防泄漏测试](https://raw.githubusercontent.com/rosemarycox5334-debug/Detect_support_and_resistance_levels/main/src/srlab/leakage.py) |
| R19 | [vn.py TurtleSignalStrategy](https://raw.githubusercontent.com/vnpy/vnpy_ctastrategy/main/vnpy_ctastrategy/strategies/turtle_signal_strategy.py) |
| R20 | [vn.py BollChannelStrategy](https://raw.githubusercontent.com/vnpy/vnpy_ctastrategy/main/vnpy_ctastrategy/strategies/boll_channel_strategy.py) |
| R21 | [Kenneth French 月度动量构造](https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library/det_mom_factor.html) |
| R22 | [Qlib Alpha158 特征](https://raw.githubusercontent.com/microsoft/qlib/main/qlib/contrib/data/loader.py) |
| R23 | [Qlib LightGBM/Alpha158 工作流](https://raw.githubusercontent.com/microsoft/qlib/main/examples/benchmarks/LightGBM/workflow_config_lightgbm_Alpha158.yaml) |
| R24 | [Qlib TopkDropout](https://raw.githubusercontent.com/microsoft/qlib/main/qlib/contrib/strategy/signal_strategy.py) |
| R25 | [Freqtrade Lookahead Analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/) |
| R26 | [Freqtrade Recursive Analysis](https://www.freqtrade.io/en/stable/recursive-analysis/) |
| R27 | [MLflow 模型注册中心](https://mlflow.org/docs/latest/ml/model-registry/) |
| R28 | [PA 许可](https://raw.githubusercontent.com/rosemarycox5334-debug/PA_Agent/main/LICENSE) |
| R29 | [SR 许可](https://raw.githubusercontent.com/rosemarycox5334-debug/Detect_support_and_resistance_levels/main/LICENSE) |
| R30 | [vn.py CTA 许可](https://raw.githubusercontent.com/vnpy/vnpy_ctastrategy/main/LICENSE) |
| R31 | [Qlib 许可](https://raw.githubusercontent.com/microsoft/qlib/main/LICENSE) |
| R32 | [scikit-learn 概率校准](https://scikit-learn.org/stable/modules/calibration.html) |
| R33 | [scikit-learn TimeSeriesSplit](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html) |
| R34 | [上交所交易规则：2026 年修订发布页](https://www.sse.com.cn/lawandrules/sselawsrules2025/stocks/exchange/c/c_20260424_10816482.shtml) |

R34 发布页显示新规则于 2026-07-06 生效，且存在暂缓实施条文。因此实施不能仅按“文档标题为最新”就启用所有条款，必须结合适用品种、生效时间和实施状态，建立有日期的市场规则配置。本文不硬编码一套对所有市场、所有板块、所有年份通用的交易限制。
