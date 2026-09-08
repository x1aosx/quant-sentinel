import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ArrowRight, BookOpen, Clock3, Globe2 } from 'lucide-react';
import { api } from '../api/client';
import { StatusBadge } from '../components/StatusBadge';

export function StrategiesPage() {
  const { data: strategies } = useQuery({ queryKey: ['strategies'], queryFn: api.getStrategies });

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>策略库</h1>
          <div className="muted">策略版本、证据等级、来源和参数变更都是不可变资产。</div>
        </div>
      </div>
      <div className="strategy-grid">
        {(strategies ?? []).map((strategy) => (
          <div key={strategy.id} className="strategy-card">
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <h3>{strategy.title}</h3>
              <StatusBadge status={strategy.research_status} />
            </div>
            <div className="code">{strategy.id}</div>
            <div className="inline-meta">
              <div className="meta-item">
                <div className="label">版本</div>
                <div className="value">{strategy.version}</div>
              </div>
              <div className="meta-item">
                <div className="label">证据</div>
                <div className="value">{strategy.evidence_level}</div>
              </div>
              <div className="meta-item">
                <div className="label">市场</div>
                <div className="value">{strategy.market}</div>
              </div>
              <div className="meta-item">
                <div className="label">频率</div>
                <div className="value">{strategy.frequency}</div>
              </div>
            </div>
            <div className="row">
              <Globe2 size={14} />
              <span className="muted">来源</span>
              {strategy.source_refs.length ? strategy.source_refs.map((ref) => <span key={ref} className="tag">{ref}</span>) : <span className="tag">none</span>}
            </div>
            <div className="row">
              <BookOpen size={14} />
              <span className="muted">标签</span>
              {strategy.tags.map((tag) => (
                <span key={tag} className="tag">{tag}</span>
              ))}
            </div>
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <span className="muted">
                <Clock3 size={13} style={{ verticalAlign: '-2px' }} /> {strategy.updated_at}
              </span>
              <Link className="button" to={`/strategies/${encodeURIComponent(strategy.id)}`}>
                详情 <ArrowRight size={14} />
              </Link>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
