import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, Landmark, ShieldCheck, Wallet } from 'lucide-react';
import { api } from '../api/client';
import { StatusBadge } from '../components/StatusBadge';

export function RiskPage() {
  const { data: risk } = useQuery({ queryKey: ['risk'], queryFn: api.getRisk });
  const { data: positions } = useQuery({ queryKey: ['positions'], queryFn: api.getPositions });

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>持仓与风险</h1>
          <div className="muted">账本是权威，持仓由成交投影重建。</div>
        </div>
      </div>

      <div className="grid grid-3">
        <div className="panel stat">
          <div>
            <div className="stat-label">现金</div>
            <div className="stat-value">¥{(risk?.cash ?? 0).toLocaleString()}</div>
          </div>
          <span className="stat-icon"><Wallet size={18} /></span>
        </div>
        <div className="panel stat">
          <div>
            <div className="stat-label">权益</div>
            <div className="stat-value">¥{(risk?.equity ?? 0).toLocaleString()}</div>
          </div>
          <span className="stat-icon"><Landmark size={18} /></span>
        </div>
        <div className="panel stat">
          <div>
            <div className="stat-label">总多头市值</div>
            <div className="stat-value">{((risk?.gross_exposure ?? 0) * 100).toFixed(2)}%</div>
          </div>
          <span className="stat-icon"><ShieldCheck size={18} /></span>
        </div>
      </div>

      <div className="grid grid-2">
        <div className="panel">
          <div className="section-title">持仓</div>
          <table className="table">
            <thead>
              <tr>
                <th>证券</th>
                <th>数量</th>
                <th>可卖</th>
                <th>成本</th>
                <th>市值</th>
                <th>保护阈值</th>
              </tr>
            </thead>
            <tbody>
              {(positions ?? []).map((position) => (
                <tr key={position.instrument_id}>
                  <td>{position.instrument_id}</td>
                  <td>{position.quantity}</td>
                  <td>{position.available_quantity}</td>
                  <td>{position.avg_cost}</td>
                  <td>{position.market_value}</td>
                  <td>{position.protection_threshold ?? '--'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="panel">
          <div className="section-title">风控限额</div>
          <table className="table">
            <thead>
              <tr>
                <th>限额</th>
                <th>当前</th>
                <th>上限</th>
                <th>状态</th>
              </tr>
            </thead>
            <tbody>
              {(risk?.limits ?? []).map((limit) => (
                <tr key={limit.name}>
                  <td>{limit.name}</td>
                  <td>{limit.current}</td>
                  <td>{limit.limit}</td>
                  <td>
                    <StatusBadge status={limit.status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid grid-2">
        <div className="panel">
          <div className="section-title">行业敞口</div>
          {(risk?.sector_exposure ?? []).map((sector) => (
            <div key={sector.sector} className="row" style={{ justifyContent: 'space-between' }}>
              <span>{sector.sector}</span>
              <span>{sector.weight.toFixed(4)} / {sector.limit}</span>
            </div>
          ))}
        </div>
        <div className="panel">
          <div className="section-title">
            <AlertTriangle size={15} />
            风险标记
          </div>
          {(risk?.flags ?? []).map((flag) => (
            <div key={flag} className="row">
              <span className="tag">{flag}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
