import { useQuery } from '@tanstack/react-query';
import { Bell, Check, Inbox, X } from 'lucide-react';
import { api } from '../api/client';
import { StatusBadge } from '../components/StatusBadge';

export function NotificationsPage() {
  const { data } = useQuery({ queryKey: ['notifications'], queryFn: api.getNotifications });

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>通知与复盘</h1>
          <div className="muted">投递成功不等于成交；用户确认也不创建真实仓位。</div>
        </div>
      </div>

      <div className="grid">
        {(data ?? []).map((notification) => (
          <div key={notification.id} className="panel">
            <div className="row" style={{ justifyContent: 'space-between' }}>
              <div className="row">
                <Bell size={16} />
                <strong>{notification.title}</strong>
                <StatusBadge status={notification.status} />
              </div>
              <span className="muted">{notification.delivered_at}</span>
            </div>
            <p className="muted">{notification.body}</p>
            <div className="row">
              <button className="button">
                <Check size={14} />
                确认
              </button>
              <button className="button">
                <X size={14} />
                忽略
              </button>
              <span className="tag">{notification.feedback}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="panel">
        <div className="section-title">
          <Inbox size={15} />
          复盘入口
        </div>
        <p className="muted">逐笔复盘需要真实执行差异、信号时点图表、状态轨迹与数据版本。当前 demo 数据不能作为复盘证据。</p>
      </div>
    </div>
  );
}
