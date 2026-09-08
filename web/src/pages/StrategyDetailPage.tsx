import { useQuery } from '@tanstack/react-query';
import { useParams } from 'react-router-dom';
import { GitBranch, Layers3, Microscope } from 'lucide-react';
import { api } from '../api/client';
import { StatusBadge } from '../components/StatusBadge';

export function StrategyDetailPage() {
  const { id = '' } = useParams();
  const { data } = useQuery({ queryKey: ['strategy', id], queryFn: () => api.getStrategy(id) });

  if (!data) {
    return <div className="empty">加载中</div>;
  }

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>{data.title}</h1>
          <div className="code">{data.id}</div>
        </div>
        <StatusBadge status={data.research_status} />
      </div>

      <div className="grid grid-3">
        <div className="panel">
          <div className="section-title">市场与频率</div>
          <div>{data.market}</div>
          <div className="muted">{data.frequency}</div>
        </div>
        <div className="panel">
          <div className="section-title">证据等级</div>
          <StatusBadge status={data.evidence_level} />
          <p className="muted">未完成收益验证，不得标注实盘验证通过。</p>
        </div>
        <div className="panel">
          <div className="section-title">来源</div>
          <div className="row">
            {data.source_refs.length ? data.source_refs.map((ref) => <span key={ref} className="tag">{ref}</span>) : <span className="tag">none</span>}
          </div>
        </div>
      </div>

      <div className="grid grid-2">
        <div className="panel">
          <div className="section-title">
            <span>交易假设</span>
          </div>
          <ul>
            {data.assumptions.map((assumption) => (
              <li key={assumption}>{assumption}</li>
            ))}
          </ul>
        </div>
        <div className="panel">
          <div className="section-title">参数 Schema 摘要</div>
          <table className="table">
            <thead>
              <tr>
                <th>参数</th>
                <th>类型</th>
                <th>默认</th>
                <th>说明</th>
              </tr>
            </thead>
            <tbody>
              {data.parameter_schema.map((param) => (
                <tr key={param.key}>
                  <td className="code">{param.key}</td>
                  <td>{param.type}</td>
                  <td>{String(param.default ?? '')}</td>
                  <td>{param.description}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="panel">
        <div className="section-title">
          <GitBranch size={16} />
          版本
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>版本</th>
              <th>配置哈希</th>
              <th>状态</th>
              <th>变更</th>
              <th>创建时间</th>
            </tr>
          </thead>
          <tbody>
            {data.versions.map((version) => (
              <tr key={version.version}>
                <td>{version.version}</td>
                <td className="code">{version.config_hash}</td>
                <td>
                  <StatusBadge status={version.status} />
                </td>
                <td>{version.changes.join(', ')}</td>
                <td>{version.created_at}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="grid grid-2">
        <div className="panel">
          <div className="section-title">
            <Microscope size={16} />
            相关实验
          </div>
          {data.related_experiments.length ? (
            data.related_experiments.map((exp) => (
              <div key={exp.id} className="row" style={{ justifyContent: 'space-between' }}>
                <span>{exp.title}</span>
                <StatusBadge status={exp.status} />
              </div>
            ))
          ) : (
            <div className="empty">暂无实验</div>
          )}
        </div>
        <div className="panel">
          <div className="section-title">
            <Layers3 size={16} />
            相关部署
          </div>
          {data.deployments.length ? (
            data.deployments.map((dep) => (
              <div key={dep.id} className="row" style={{ justifyContent: 'space-between' }}>
                <span>{dep.instance_id} · {dep.version}</span>
                <StatusBadge status={dep.status} />
              </div>
            ))
          ) : (
            <div className="empty">暂无部署</div>
          )}
        </div>
      </div>
    </div>
  );
}
