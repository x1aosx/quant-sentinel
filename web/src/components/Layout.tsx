import { NavLink, Outlet, useLocation } from 'react-router-dom';
import {
  Activity,
  Bell,
  Database,
  FlaskConical,
  Gauge,
  Hexagon,
  Layers3,
  PlaySquare,
  ShieldCheck,
  TrendingUp,
} from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

const nav = [
  { to: '/', label: '驾驶台', icon: Gauge },
  { to: '/strategies', label: '策略库', icon: Layers3 },
  { to: '/experiments', label: '实验中心', icon: FlaskConical },
  { to: '/replay', label: '回放工作台', icon: PlaySquare },
  { to: '/deployments', label: '部署中心', icon: Activity },
  { to: '/risk', label: '持仓与风险', icon: ShieldCheck },
  { to: '/data', label: '数据中心', icon: Database },
  { to: '/notifications', label: '通知与复盘', icon: Bell },
];

const titles: Record<string, string> = {
  '/': '驾驶台',
  '/strategies': '策略库',
  '/experiments': '实验中心',
  '/replay': '回放工作台',
  '/deployments': '部署中心',
  '/risk': '持仓与风险',
  '/data': '数据中心',
  '/notifications': '通知与复盘',
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
        <div className="nav-label">管理</div>
        {nav.map((item) => {
          const Icon = item.icon;
          return (
            <NavLink key={item.to} to={item.to} end={item.to === '/'} className={({ isActive }) => `nav-link${isActive ? ' active' : ''}`}>
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
              <TrendingUp size={14} />
              {health?.status ?? 'unknown'}
            </span>
            <span className="badge badge-neutral">心跳 {health?.heartbeat_at ? new Date(health.heartbeat_at).toLocaleTimeString() : '--'}</span>
            <span className="badge badge-demo">DEMO</span>
          </div>
        </header>
        <main className="page">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
