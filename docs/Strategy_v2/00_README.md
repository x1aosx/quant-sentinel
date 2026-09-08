# X-Quant：本地量化策略优先设计 v2

本版本根据用户“核心是量化策略”的要求重排项目：**先从指定 GitHub 仓库提取价格行为、支撑阻力与预测逻辑，形成明确交易规则，再开发本地策略管理网站。**

## 交付内容

| 文件 | 用途 |
|---|---|
| [01_SOURCE_AUDIT.md](01_SOURCE_AUDIT.md) | 两个指定仓库的策略/预测源码审查、缺陷与条件概率问题、社区策略选用、34 项来源 |
| [02_SRPA_STRATEGY_SPEC.md](02_SRPA_STRATEGY_SPEC.md) | 核心交易规则书：突破回测、H2、偏多区间、基线、仓位、退出、预测与验证 |
| [03_STRATEGY_PLATFORM_DESIGN.md](03_STRATEGY_PLATFORM_DESIGN.md) | 本地策略库、版本/参数/实验/部署/复盘、架构、表结构、API、界面与可靠性 |
| [04_CODEX_IMPLEMENTATION.md](04_CODEX_IMPLEMENTATION.md) | 可直接交给 Codex 的启动指令、P0–P5、36 项必经验收测试 |
| [configs/srpa_breakout_retest_v0.1.yaml](configs/srpa_breakout_retest_v0.1.yaml) | 主策略机器可读参数规格；不是已安装可执行引擎 |
| [configs/strategy_catalog.yaml](configs/strategy_catalog.yaml) | 10 个策略/基准/挑战者的研究目录，均明确未实施、未获实盘批准 |
| [05_VALIDATION_NOTES.md](05_VALIDATION_NOTES.md) | 本次交付的实际检查结果与未完成验证边界 |

## 首先实施什么

首个完整策略为 **XQ-SRPA 突破回测做多**：大盘与个股状态→冻结阻力区域→收盘突破→保持→回测→收盘确认→下一交易会话核对入场→独立风控与退出。

先实现它与现金/EMA/Donchian 基线的离线回放，再增加网页。H2、区间反弹与机器学习分别作为可管理的后续策略，不无条件叠加为一个大而不可验证的“综合评分”。

## 使用方法

把整个目录放到项目 `docs/strategy_v2/`，将 `04_CODEX_IMPLEMENTATION.md` 第 1 节交给 Codex。代码实现前读取所有文档，并以策略规格中的交易定义作为权威。

旧 `X-Stock_Design.md` 中“先网站后策略”的阶段顺序、默认多服务部署被本版本替代；数据授权、账本真实性、人工确认与可靠性原则继续保留。本文档包按 X-Quant 项目名组织，不要求已有代码立即改名。

## 当前证据边界

这是经过源码核查形成的**策略规则与工程实施设计**，不是已经运行好的系统或已证明盈利的策略。没有使用用户的真实行情/持仓数据，没有运行上游完整测试，也没有进行本项目历史收益或实盘验证。所有默认阈值须接受本市场成本后样本外检验。

GitHub 来源的最终 SHA、真实市场规则、费用、基准、行情授权与个人风险容忍度均需要实施时固定。示例 YAML 中这些字段留空并要求正式部署失败关闭，而不是凭空填入可靠性。
