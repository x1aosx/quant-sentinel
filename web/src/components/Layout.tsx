import { NavLink, Outlet, useLocation } from 'react-router-dom';
import {
  Activity,
  Crosshair,
  Database,
  Gauge,
  Hexagon,
  Newspaper,
  Settings2,
  Telescope,
  Workflow,
} from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../api/client';

const navGroups = [
  {
    label: '分析',
    items: [
      { to: '/', label: '驾驶台', icon: Gauge },
      { to: '/data', label: '数据中心', icon: Database },
      { to: '/analysis', label: '股票分析', icon: Crosshair },
      { to: '/intelligence', label: '市场情报', icon: Newspaper },
      { to: '/discovery', label: '机会发现', icon: Telescope },
    ],
  },
  {
    label: '运营',
    items: [
      { to: '/scheduler', label: '任务中心', icon: Workflow },
      { to: '/settings', label: '系统配置', icon: Settings2 },
    ],
  },
];

const titles: Record<string, string> = {
  '/': '驾驶台',
  '/data': '数据中心',
  '/analysis': '股票分析',
  '/intelligence': '市场情报',
  '/discovery': '机会发现',
  '/ai': '股票分析',
  '/market-analysis': '股票分析',
  '/support-resistance': '股票分析',
  '/price-action': '股票分析',
  '/scheduler': '任务中心',
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
        {navGroups.map((group) => (
          <div key={group.label}>
            <div className="nav-label">{group.label}</div>
            {group.items.map((item) => {
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
          </div>
        ))}
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
          </div>
        </header>
        <main className="page">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
