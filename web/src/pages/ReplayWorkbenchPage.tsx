import { useState } from 'react';
import { useMutation } from '@tanstack/react-query';
import { Play, RotateCcw } from 'lucide-react';
import { Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { api } from '../api/client';
import { StatusBadge } from '../components/StatusBadge';
import type { ReplayRun } from '../types';

export function ReplayWorkbenchPage() {
  const [run, setRun] = useState<ReplayRun | null>(null);
  const mutation = useMutation({
    mutationFn: () => api.startReplay({ strategy_id: 'xq.srpa.breakout_retest.long', instrument_id: 'DEMO.EXAMPLE', snapshot_id: 'demo-snapshot' }),
    onSuccess: setRun,
  });

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>回放工作台</h1>
          <div className="muted">逐棒查看状态、信号与权益变化，前端不执行策略逻辑。</div>
        </div>
        <div className="row">
          <button className="button" onClick={() => mutation.mutate()} disabled={mutation.isPending}>
            {mutation.isPending ? <RotateCcw size={15} /> : <Play size={15} />}
            {mutation.isPending ? '运行中' : '开始回放'}
          </button>
        </div>
      </div>

      {!run && !mutation.data ? (
        <div className="empty">点击开始回放，将调用后端 API 生成事件、交易计划与权益曲线。</div>
      ) : (
        <div className="stack">
          <div className="panel">
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <div>
                <div className="code">{run?.run_id}</div>
                <div className="muted">{run?.strategy_id} · {run?.instrument_id} · {run?.snapshot_id}</div>
              </div>
              <StatusBadge status={run?.status ?? 'RUNNING'} />
            </div>
          </div>

          <div className="grid grid-2">
            <div className="panel">
              <div className="section-title">事件轨迹</div>
              <table className="table">
                <thead>
                  <tr>
                    <th>会话</th>
                    <th>事件</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {(run?.events ?? []).map((event, idx) => (
                    <tr key={`${event.session}-${idx}`}>
                      <td>{event.session}</td>
                      <td>{event.kind}</td>
                      <td className="code">{event.state ?? '--'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="panel">
              <div className="section-title">交易计划</div>
              <table className="table">
                <thead>
                  <tr>
                    <th>计划</th>
                    <th>状态</th>
                    <th>入场区间</th>
                    <th>止损</th>
                    <th>目标</th>
                  </tr>
                </thead>
                <tbody>
                  {(run?.trade_plans ?? []).map((plan) => (
                    <tr key={plan.plan_id}>
                      <td className="code">{plan.plan_id}</td>
                      <td>
                        <StatusBadge status={plan.status} />
                      </td>
                      <td>{plan.entry_min} - {plan.entry_max}</td>
                      <td>{plan.stop_threshold}</td>
                      <td>{plan.target_threshold}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="panel">
            <div className="section-title">权益曲线</div>
            <div className="chart-wrap">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={run?.equity_curve ?? []} margin={{ top: 6, right: 16, left: 8, bottom: 0 }}>
                  <XAxis dataKey="session" tick={{ fontSize: 12 }} />
                  <YAxis tick={{ fontSize: 12 }} domain={['auto', 'auto']} />
                  <Tooltip />
                  <Line type="monotone" dataKey="equity" stroke="#1f6f5e" strokeWidth={2} dot={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
