import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, ArrowRightLeft, ListTodo } from 'lucide-react';
import { api } from '../api/client';
import { StatusBadge } from '../components/StatusBadge';

export function ExperimentsPage() {
  const { data } = useQuery({ queryKey: ['experiments'], queryFn: api.getExperiments });

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>实验中心</h1>
          <div className="muted">所有尝试入库，包括失败、超时和无交易结果。</div>
        </div>
      </div>

      <div className="panel">
        <div className="section-title">
          <ListTodo size={16} />
          实验队列
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>实验</th>
              <th>策略</th>
              <th>状态</th>
              <th>进度</th>
              <th>比较运行</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((exp) => (
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
                <td className="code">{exp.comparison_runs.length ? exp.comparison_runs.join(', ') : '--'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="grid grid-2">
        <div className="panel">
          <div className="section-title">
            <ArrowRightLeft size={16} />
            同口径比较
          </div>
          <p className="muted">比较前核验数据快照、成本与执行模型可比性。</p>
          <div className="code">run-demo-sbr vs run-demo-cash</div>
        </div>
        <div className="panel">
          <div className="section-title">
            <AlertTriangle size={16} />
            失败原因
          </div>
          {(data ?? [])
            .filter((exp) => exp.failure_reason)
            .map((exp) => (
              <div key={exp.id} className="row" style={{ justifyContent: 'space-between' }}>
                <span>{exp.title}</span>
                <span className="muted">{exp.failure_reason}</span>
              </div>
            ))}
        </div>
      </div>
    </div>
  );
}
