import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Activity, CheckCircle2, Database, FileClock, FlaskConical, ShieldAlert } from 'lucide-react';
import { api } from '../api/client';
import { DemoWatermark } from '../components/DemoWatermark';
import { StatusBadge } from '../components/StatusBadge';

export function DashboardPage() {
  const { data } = useQuery({ queryKey: ['dashboard'], queryFn: api.getDashboard });
  const items = [
    { label: '运行实例', value: data?.running_instances ?? 0, icon: Activity },
    { label: '待处理计划', value: data?.pending_plans ?? 0, icon: FileClock },
    { label: '待审批', value: data?.pending_approvals ?? 0, icon: ShieldAlert },
    { label: '近期实验', value: data?.recent_experiments?.length ?? 0, icon: FlaskConical },
  ];

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>策略驾驶台</h1>
          <div className="muted">数据截止 {data?.data_last_updated ?? '--'} · 模式 {data?.mode ?? 'demo'}</div>
        </div>
        {data?.demo ? <DemoWatermark /> : null}
      </div>

      <div className="grid grid-4">
        {items.map((item) => {
          const Icon = item.icon;
          return (
            <div key={item.label} className="panel stat">
              <div>
                <div className="stat-label">{item.label}</div>
                <div className="stat-value">{item.value}</div>
              </div>
              <span className="stat-icon">
                <Icon size={18} />
              </span>
            </div>
          );
        })}
      </div>

      <div className="grid grid-3">
        <div className="panel">
          <div className="section-title">数据状态</div>
          <div className="row">
            <Database size={16} />
            <StatusBadge status={data?.data_status ?? 'UNKNOWN'} />
          </div>
          <p className="muted">最后数据时间 {data?.data_last_updated ?? '--'}</p>
        </div>
        <div className="panel">
          <div className="section-title">风控状态</div>
          <div className="row">
            {data?.risk_status === 'OK' ? <CheckCircle2 size={16} color="#1f6f5e" /> : <ShieldAlert size={16} color="#a15c07" />}
            <StatusBadge status={data?.risk_status ?? 'UNKNOWN'} />
          </div>
          <p className="muted">组合硬风险优先于新策略信号。</p>
        </div>
        <div className="panel">
          <div className="section-title">运行健康</div>
          <div>心跳 {data?.heartbeat_at ?? '--'}</div>
          <p className="muted">本地进程休眠或退出后不会自动发送过期提醒。</p>
        </div>
      </div>

      <div className="panel">
        <div className="section-title">
          <span>近期实验</span>
          <Link className="button" to="/experiments">
            打开实验中心
          </Link>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>实验</th>
              <th>策略</th>
              <th>状态</th>
              <th>进度</th>
            </tr>
          </thead>
          <tbody>
            {(data?.recent_experiments ?? []).map((exp) => (
              <tr key={exp.id}>
                <td>{exp.title}</td>
                <td className="code">{exp.strategy_id}</td>
                <td>
                  <StatusBadge status={exp.status} />
                </td>
                <td>
                  <div className="progress">
                    <div className="progress-fill" style={{ width: `${exp.progress}%` }} />
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
