# X-Quant

本地策略优先的量化研究与策略管理系统原型。当前实现为 **DEMO/research 阶段**，不构成实盘或收益验证。

## 后端

```powershell
uv sync
uv run uvicorn xquant.api.app:create_app --factory --reload --port 8000
```

## 前端

```powershell
cd web
npm install
npm run dev
```

## 验收边界

- 策略内核与回放基于合成数据，显著标为 DEMO。
- 真实成交只能通过 execution import 进入账本，当前未实现券商连接。
- 未通过样本外验证前，任何策略不得进入 live_assist。

