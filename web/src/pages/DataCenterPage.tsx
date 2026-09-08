import { useQuery } from '@tanstack/react-query';
import { Database, FileCheck2, RefreshCcw } from 'lucide-react';
import { api } from '../api/client';
import { StatusBadge } from '../components/StatusBadge';

export function DataCenterPage() {
  const { data } = useQuery({ queryKey: ['data'], queryFn: api.getDataStatus });

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>数据中心</h1>
          <div className="muted">数据快照不可变，切换来源必须生成新 revision。</div>
        </div>
      </div>

      <div className="grid">
        {(data ?? []).map((source) => (
          <div key={source.source} className="panel">
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <div className="row">
                <Database size={16} />
                <strong>{source.source}</strong>
              </div>
              <span className="badge badge-info">{source.revision}</span>
            </div>
            <div className="inline-meta">
              <div className="meta-item">
                <div className="label">覆盖率</div>
                <div className="value">{source.coverage}%</div>
              </div>
              <div className="meta-item">
                <div className="label">最后更新</div>
                <div className="value">{source.last_updated}</div>
              </div>
              <div className="meta-item">
                <div className="label">快照数</div>
                <div className="value">{source.snapshots}</div>
              </div>
              <div className="meta-item">
                <div className="label">延迟</div>
                <div className="value">{source.delay_minutes ?? 'n/a'}</div>
              </div>
            </div>
            <div className="row">
              <FileCheck2 size={14} />
              <span className="muted">质量标记</span>
              {source.quality_flags.map((flag) => (
                <StatusBadge key={flag} status={flag} />
              ))}
            </div>
          </div>
        ))}
      </div>

      <div className="panel">
        <div className="section-title">
          <RefreshCcw size={15} />
          数据策略
        </div>
        <p className="muted">策略不直接抓取网站；DataHub 统一配额、缓存、增量水位与熔断。无外网时只能研究已持有的授权快照。</p>
      </div>
    </div>
  );
}
