import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Pause, Play, RotateCcw, ShieldCheck, Square, Undo2 } from 'lucide-react';
import { api } from '../api/client';
import { StatusBadge } from '../components/StatusBadge';

const commands = [
  { command: 'start', label: '启动', icon: Play },
  { command: 'pause_entries', label: '暂停新增', icon: Pause },
  { command: 'exit_only', label: '只出不进', icon: Undo2 },
  { command: 'stop', label: '停止', icon: Square },
  { command: 'rollback', label: '回滚', icon: RotateCcw },
];

export function DeploymentsPage() {
  const queryClient = useQueryClient();
  const { data } = useQuery({ queryKey: ['instances'], queryFn: api.getInstances });

  const commandMutation = useMutation({
    mutationFn: ({ id, command }: { id: string; command: string }) => api.instanceCommand(id, command),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['instances'] }),
  });

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>部署中心</h1>
          <div className="muted">实例钉住策略版本，启停命令有明确语义，不覆盖历史证据。</div>
        </div>
      </div>

      {(data ?? []).map((instance) => (
        <div key={instance.id} className="panel">
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <div>
              <div className="code">{instance.id}</div>
              <div>{instance.strategy_id} · v{instance.version}</div>
            </div>
            <StatusBadge status={instance.status} />
          </div>
          <div className="inline-meta">
            <div className="meta-item">
              <div className="label">账户</div>
              <div className="value">{instance.account}</div>
            </div>
            <div className="meta-item">
              <div className="label">模式</div>
              <div className="value">{instance.mode}</div>
            </div>
            <div className="meta-item">
              <div className="label">股票池</div>
              <div className="value">{instance.universe.join(', ')}</div>
            </div>
            <div className="meta-item">
              <div className="label">权益预算</div>
              <div className="value">¥{instance.budget.equity.toLocaleString()}</div>
            </div>
          </div>
          <div className="section-title">命令</div>
          <div className="row">
            {commands.map((cmd) => {
              const Icon = cmd.icon;
              return (
                <button key={cmd.command} className="button" onClick={() => commandMutation.mutate({ id: instance.id, command: cmd.command })}>
                  <Icon size={14} />
                  {cmd.label}
                </button>
              );
            })}
          </div>
          <div className="section-title">
            <ShieldCheck size={15} />
            审批记录
          </div>
          {instance.approvals.length ? (
            instance.approvals.map((approval) => (
              <div key={approval.id} className="row">
                <span className="code">{approval.version}</span>
                <span>{approval.approved_at}</span>
                <span className="muted">{approval.report_ref}</span>
              </div>
            ))
          ) : (
            <div className="empty">尚无审批记录</div>
          )}
        </div>
      ))}
    </div>
  );
}
