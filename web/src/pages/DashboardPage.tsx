import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { ArrowRight, BrainCircuit, CircleCheck, CloudUpload, Crosshair, Database, Gauge, Layers, ShieldAlert, ShieldCheck } from 'lucide-react';
import { api } from '../api/client';

export function DashboardPage() {
  const healthQuery = useQuery({ queryKey: ['health'], queryFn: api.getHealth });
  const datasetsQuery = useQuery({ queryKey: ['datasets'], queryFn: api.listDatasets });
  const datasets = datasetsQuery.data?.items ?? [];
  const latestDataset = datasets[0];
  const healthReady = healthQuery.data?.status === 'ok';
  const ready = Boolean(healthQuery.data?.status === 'ok' && datasets.length);
  const HealthIcon = healthReady ? ShieldCheck : ShieldAlert;

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>本地量化研究驾驶台</h1>
          <div className="muted">先导入已收盘 OHLCV 快照，再做支撑阻力与价格行为分析。</div>
        </div>
        <span className="badge badge-info">simulation only</span>
      </div>

      <div className="grid grid-4">
        <div className="panel stat">
          <div>
            <div className="stat-label">数据集</div>
            <div className="stat-value">{datasets.length}</div>
          </div>
          <span className="stat-icon"><Layers size={18} /></span>
        </div>
        <div className="panel stat">
          <div>
            <div className="stat-label">最新快照</div>
            <div className="stat-value">{latestDataset?.symbol ?? '--'}</div>
          </div>
          <span className="stat-icon"><Gauge size={18} /></span>
        </div>
        <div className="panel stat">
          <div>
            <div className="stat-label">API 状态</div>
            <div className="stat-value">{healthQuery.data?.status ?? '--'}</div>
          </div>
          <span className={healthReady ? 'stat-icon info' : 'stat-icon warn'}>
            <HealthIcon size={18} />
          </span>
        </div>
        <div className="panel stat">
          <div>
            <div className="stat-label">研究模式</div>
            <div className="stat-value">离线</div>
          </div>
          <span className="stat-icon"><BrainCircuit size={18} /></span>
        </div>
      </div>

      <div className="grid grid-2">
        <div className="panel highlight-card">
          <div className="section-title">
            <span>开始使用</span>
            <Link className="button button-primary" to="/data">
              <Database size={15} />
              打开数据中心
            </Link>
          </div>
          <div className="stat">
            <div>
              <div className="stat-label">数据准备</div>
              <div className="stat-value">
                {ready ? '已完成' : datasetsQuery.isLoading ? '加载中' : '待导入'}
              </div>
            </div>
            <span className={ready ? 'stat-icon' : 'stat-icon warn'}>
              {ready ? <CircleCheck size={18} /> : <CloudUpload size={18} />}
            </span>
          </div>
          {ready ? (
            <p className="muted">数据准备完成，可以直接进入支撑阻力或价格行为分析。</p>
          ) : datasetsQuery.isLoading ? (
            <p className="muted">正在连接本地 API...</p>
          ) : (
            <p className="muted">尚未导入数据。上传本地 CSV/JSON，或先生成一份合成研究快照。</p>
          )}
        </div>
        <div className="panel highlight-card">
          <div className="section-title">分析入口</div>
          <div className="row">
            <Link className="button button-primary" to="/support-resistance">
              <Crosshair size={15} />
              支撑阻力
              <ArrowRight size={15} />
            </Link>
            <Link className="button" to="/price-action">
              <BrainCircuit size={15} />
              价格行为
              <ArrowRight size={15} />
            </Link>
          </div>
          <p className="muted">
            分析结果基于本地历史K线结构，不连接券商，不发送下单请求，也不构成投资建议。
          </p>
        </div>
      </div>

      <div className="panel">
        <div className="section-title">
          <span>最近数据集</span>
          <span className={ready ? 'badge badge-ok' : 'badge badge-warn'}>
            {ready ? '数据就绪' : '等待数据'}
          </span>
        </div>
        {datasetsQuery.isLoading ? (
          <div className="empty">正在加载数据集...</div>
        ) : datasetsQuery.isError ? (
          <div className="empty">数据集加载失败：{(datasetsQuery.error as Error).message}</div>
        ) : datasets.length ? (
          <table className="table">
            <thead>
              <tr>
                <th>品种</th>
                <th>周期</th>
                <th>K线数</th>
                <th>首根</th>
                <th>末根</th>
              </tr>
            </thead>
            <tbody>
              {datasets.slice(0, 8).map((item) => (
                <tr key={item.id}>
                  <td>{item.symbol}</td>
                  <td>{item.timeframe}</td>
                  <td>{item.bar_count}</td>
                  <td className="code">{item.first_session}</td>
                  <td className="code">{item.last_session}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="empty">暂无数据集</div>
        )}
      </div>
    </div>
  );
}
