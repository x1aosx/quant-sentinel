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

## 持久化存储

应用默认使用 `legacy_sqlite` 便于本地研究；生产或联调环境可将 `STORAGE_BACKEND` 设为 `postgres`，并把连接信息写入 `deploy/.env` 或通过 `XQUANT_STORAGE_CONFIG_FILE` 指向一个仓库外的配置文件。三个存储的分工是：

- PostgreSQL：策略、实验、实例、计划和数据集元数据；
- Redis：可重建缓存、状态和事件流，普通缓存都必须带 TTL；
- InfluxDB：K 线等时序明细。

应用启动后会执行幂等的建表检查；正式部署建议继续使用数据库迁移工具，避免在应用进程内管理生产 Schema。`GET /api/v1/health/storage` 会返回三项存储的分项状态和延迟，不会输出凭据。本地如果要临时启动存储容器，可以运行 `docker compose --profile storage-local up -d`，但生产环境应使用独立部署的中间件和最小权限账号。
