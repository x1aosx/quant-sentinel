import { NavLink, Outlet, useLocation } from 'react-router-dom';
import {
  Activity,
  BrainCircuit,
  Crosshair,
  Database,
  Gauge,
  Hexagon,
  Settings2,
} from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

const nav = [
  { to: '/', label: '驾驶台', icon: Gauge },
  { to: '/data', label: '数据中心', icon: Database },
  { to: '/ai', label: 'AI 分析', icon: BrainCircuit },
  { to: '/support-resistance', label: '支撑阻力', icon: Crosshair },
  { to: '/price-action', label: '价格行为', icon: BrainCircuit },
  { to: '/settings', label: '系统配置', icon: Settings2 },
];

const titles: Record<string, string> = {
  '/': '驾驶台',
  '/data': '数据中心',
  '/ai': 'AI 分析',
  '/support-resistance': '支撑阻力',
  '/price-action': '价格行为',
  '/settings': '系统配置',
};

export function Layout() {
  const location = useLocation();
  const { data: health } = useQuery({ queryKey: ['health'], queryFn: api.getHealth });
  const title = titles[location.pathname] ?? 'X-Quant';
  const healthBadgeClass = health?.status === 'ok' ? 'badge badge-ok' : 'badge badge-neutral';

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">
            <Hexagon size={18} />
          </span>
          X-Quant
        </div>
        <div className="nav-label">分析</div>
        {nav.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}
            >
              <Icon size={16} />
              {item.label}
            </NavLink>
          );
          })}
          <div className="sidebar-footnote">本地研究 · simulation only</div>
        </aside>
      <div className="main">
        <header className="topbar">
          <div className="topbar-left">
            <span className="topbar-title">{title}</span>
          </div>
          <div className="topbar-right">
            <span className={healthBadgeClass}>
              <Activity size={14} />
              健康 {health?.status ?? 'unknown'}
            </span>
            <span className="badge badge-neutral">
              <Database size={14} />
              数据集 {health?.dataset_count ?? 0}
            </span>
          </div>
        </header>
        <main className="page">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
