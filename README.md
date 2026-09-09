# X-Quant

本地量化研究工作台。当前提供本地 OHLCV 数据导入、支撑阻力融合分析和价格行为结构分析，全部输出仅为研究/模拟用途，不连接券商，不构成投资建议。

## 后端

```powershell
cd api
uv sync
uv run uvicorn xquant.api.app:create_app --factory --reload --port 8000
```

## 前端

```powershell
cd web
npm install
npm run dev
```

## 一键启动

Windows 下双击或命令行运行：

```powershell
.\deploy\start_xquant.bat
```

脚本会自动创建 Python 环境、安装前端依赖，并分别启动 FastAPI 与 Vite 开发服务器。

## 核心功能

- 数据中心：导入本地 CSV/JSON OHLCV，或生成一份合成研究快照。
- 支撑阻力：融合成交密度、已确认 swing pivots、ATR 区间与更长周期共振。
- 价格行为：本地计算趋势背景、波段结构、突破质量、H/L 计数、支撑阻力与保守决策参考。
- 图表：K线与关键价位区间联动展示。

## 数据边界

- 只使用已导入的本地历史快照；没有行情供应商授权接入。
- 分析结果由确定性规则生成，不是盈利承诺，也不发送下单请求。
- 参考实现来自上游开源项目的方法，未复制其源码或预训练模型。
