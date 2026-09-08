# X-Quant 实施状态

更新时间：2026-09-08

## 已完成

- 项目骨架：Python FastAPI + React/Vite。
- 合成行情生成器与 OHLC 校验。
- 指标：EMA、ATR、CLV、body ratio、overlap、ER20、slope20。
- 因果枢轴与简化 SR 区域检测。
- S-BR 突破回测做多状态机（DEMO 合成数据回放）。
- SQLite 策略/实验注册表。
- CLI `xquant replay` 与 API 实验接口。

## 未完成

- 真实行情 Provider 授权接入。
- 市场日历、费用、交易规则生效日期配置。
- 完整 T01-T36 验收测试。
- 样本外回测、模型预测、仿真/实盘闭环。
- 上游仓库 commit SHA 固定与许可复核。

## 阻塞项

- 无真实行情授权快照。
- 上游仓库固定 SHA 未取得。

