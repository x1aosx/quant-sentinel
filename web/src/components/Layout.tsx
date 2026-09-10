import { NavLink, Outlet, useLocation } from 'react-router-dom';
import {
  Activity,
  BrainCircuit,
  Crosshair,
  Database,
  Gauge,
  Hexagon,
} from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

const nav = [
  { to: '/', label: '驾驶台', icon: Gauge },
  { to: '/data', label: '数据中心', icon: Database },
  { to: '/ai', label: 'AI 分析', icon: BrainCircuit },
  { to: '/support-resistance', label: '支撑阻力', icon: Crosshair },
  { to: '/price-action', label: '价格行为', icon: BrainCircuit },
];

const titles: Record<string, string> = {
  '/': '驾驶台',
  '/data': '数据中心',
  '/ai': 'AI 分析',
  '/support-resistance': '支撑阻力',
  '/price-action': '价格行为',
};

export function Layout() {
  const location = useLocation();
  const { data: health } = useQuery({ queryKey: ['health'], queryFn: api.getHealth });
  const title = titles[location.pathname] ?? 'X-Quant';

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
      </aside>
      <div className="main">
        <header className="topbar">
          <div className="topbar-left">
            <span className="topbar-title">{title}</span>
          </div>
          <div className="topbar-right">
            <span className="badge badge-neutral">
              <Activity size={14} />
              {health?.status ?? 'unknown'}
            </span>
            <span className="badge badge-neutral">数据集 {health?.dataset_count ?? 0}</span>
            <span className="badge badge-info">本地研究</span>
          </div>
        </header>
        <main className="page">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
