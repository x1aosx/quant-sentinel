# X-Quant

本地策略优先的量化研究与策略管理系统原型。当前实现为 **DEMO/research 阶段**，不构成实盘或收益验证。

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

## 项目结构

- `api/`：FastAPI 服务、量化策略内核、配置与 Python 测试。
- `web/`：React + Vite 前端应用。
- `deploy/`：本地启动和部署相关文件。
- `docs/`：项目与策略设计文档。

## 验收边界

- 策略内核与回放基于合成数据，显著标为 DEMO。
- 真实成交只能通过 execution import 进入账本，当前未实现券商连接。
- 未通过样本外验证前，任何策略不得进入 live_assist。
