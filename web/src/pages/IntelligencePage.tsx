import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  Clock3,
  FileText,
  Loader2,
  Newspaper,
  Radio,
  RefreshCw,
  Tags,
  Zap,
} from 'lucide-react';
import { api } from '../api/client';
import type {
  IntelligenceBrief,
  IntelligenceThemeCategory,
  MarketEvent,
  ThemeSummary,
} from '../types';

const THEME_LIMIT = 50;
const EVENT_LIMIT = 50;

function formatTimestamp(value?: string | null): string {
  if (!value) return '时间未知';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function formatScore(value?: number | null): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '--';
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 1 });
}

function formatCount(value: number | undefined, loading: boolean): string {
  if (loading || value === undefined) return '--';
  return value.toLocaleString('zh-CN');
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : '请求失败';
}

function directionLabel(direction?: string): string {
  const labels: Record<string, string> = {
    POSITIVE: '正向',
    NEGATIVE: '负向',
    MIXED: '分化',
    NEUTRAL: '中性',
  };
  return direction ? labels[direction] ?? direction : '未分类';
}

function directionTone(direction?: string): string {
  if (direction === 'POSITIVE') return 'badge-ok';
  if (direction === 'NEGATIVE') return 'badge-danger';
  if (direction === 'MIXED') return 'badge-warn';
  return 'badge-neutral';
}

function briefLines(value: string | string[] | null | undefined): string[] {
  if (Array.isArray(value)) return value.filter(Boolean);
  if (typeof value === 'string' && value.trim()) return [value];
  return [];
}

function briefSections(brief: IntelligenceBrief): Array<{ title: string; lines: string[] }> {
  if (brief.sections?.length) {
    return brief.sections
      .map((section) => ({
        title: section.title,
        lines: briefLines(section.content),
      }))
      .filter((section) => section.lines.length > 0);
  }

  const sections = [
    { title: '情报摘要', lines: briefLines(brief.summary) },
    { title: '今日市场环境', lines: briefLines(brief.market_environment) },
    { title: '重要政策', lines: briefLines(brief.important_policies) },
    { title: '国际重大事件', lines: briefLines(brief.global_events) },
    { title: '当前市场热点', lines: briefLines(brief.current_hotspots) },
    { title: '潜在热点', lines: briefLines(brief.potential_hotspots) },
    { title: '重点股票', lines: briefLines(brief.focus_stocks) },
    { title: '持仓影响', lines: briefLines(brief.holding_impacts) },
    { title: '风险事件', lines: briefLines(brief.risk_events) },
    { title: '正文', lines: briefLines(brief.content) },
  ];

  return sections.filter((section) => section.lines.length > 0);
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="intelligence-error-state" role="alert">
      <span>
        <AlertTriangle size={15} />
        {message}
      </span>
      <button className="button" type="button" onClick={onRetry}>
        <RefreshCw size={14} />
        重试
      </button>
    </div>
  );
}

function ThemeLoadingRows() {
  return (
    <>
      {[0, 1, 2].map((row) => (
        <tr key={row}>
          <td>
            <span className="loading-block loading-block-wide" />
          </td>
          <td>
            <span className="loading-block" />
          </td>
          <td>
            <span className="loading-block" />
          </td>
          <td>
            <span className="loading-block" />
          </td>
        </tr>
      ))}
    </>
  );
}

function EventLoadingRows() {
  return (
    <div className="intelligence-event-loading" aria-label="正在加载重大事件">
      {[0, 1, 2].map((row) => (
        <div className="intelligence-event-loading-row" key={row}>
          <span className="loading-block loading-block-wide" />
          <span className="loading-block loading-block-full" />
        </div>
      ))}
    </div>
  );
}

interface ThemeView {
  key: IntelligenceThemeCategory;
  title: string;
  description: string;
}

const THEME_VIEWS: ThemeView[] = [
  {
    key: 'hot',
    title: '当前热点',
    description: '当前媒体与市场关注度最高的主题',
  },
  {
    key: 'emerging',
    title: '潜在热点',
    description: '未来热度上升、仍处于扩散阶段的主题',
  },
  {
    key: 'overcrowded',
    title: '拥挤主题',
    description: '热度较高且交易拥挤风险偏大的主题',
  },
];

function ThemeTable({
  themes,
  emptyText,
}: {
  themes: ThemeSummary[];
  emptyText: string;
}) {
  return (
    <div className="table-wrap intelligence-theme-table-wrap">
      <table className="table intelligence-theme-table">
        <thead>
          <tr>
            <th>主题</th>
            <th>当前热度</th>
            <th>未来热度</th>
            <th>拥挤度</th>
          </tr>
        </thead>
        <tbody>
          {themes.length ? (
            themes.map((theme) => (
              <tr key={theme.id}>
                <td>
                  <div className="intelligence-theme-name">
                    <strong>{theme.name}</strong>
                    {theme.status ? <span>{theme.status}</span> : null}
                  </div>
                </td>
                <td className="intelligence-number">{formatScore(theme.current_heat_score)}</td>
                <td className="intelligence-number">{formatScore(theme.forward_heat_score)}</td>
                <td className="intelligence-number">{formatScore(theme.crowding_score)}</td>
              </tr>
            ))
          ) : (
            <tr>
              <td colSpan={4}>
                <div className="intelligence-table-empty">{emptyText}</div>
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function EventItem({ event }: { event: MarketEvent }) {
  const affectedThemes = event.affected_themes?.slice(0, 4) ?? [];

  return (
    <article className="intelligence-event">
      <div className="intelligence-event-main">
        <div className="intelligence-event-meta">
          <span className={`badge ${directionTone(event.impact_direction)}`}>
            {directionLabel(event.impact_direction)}
          </span>
          <span className="tag">{event.event_type}</span>
          {event.country ? <span className="muted">{event.country}</span> : null}
          <span className="muted">重要度 {formatScore(event.importance)}</span>
        </div>
        <strong>{event.title}</strong>
        <p>{event.summary}</p>
        <div className="intelligence-event-footer">
          <span>
            <Clock3 size={13} />
            {formatTimestamp(event.event_time)}
          </span>
          <span>{event.source_count} 个来源</span>
          {affectedThemes.map((theme) => (
            <span className="tag" key={theme}>
              {theme}
            </span>
          ))}
        </div>
      </div>
      <div className="intelligence-event-score">
        <span>事件热度</span>
        <strong>{formatScore(event.heat_score)}</strong>
      </div>
    </article>
  );
}

export function IntelligencePage() {
  const queryClient = useQueryClient();
  const overviewQuery = useQuery({
    queryKey: ['intelligence', 'overview'],
    queryFn: api.getIntelligenceOverview,
  });
  const eventsQuery = useQuery({
    queryKey: ['intelligence', 'events', EVENT_LIMIT],
    queryFn: () => api.listMarketEvents(EVENT_LIMIT),
  });
  const hotThemesQuery = useQuery({
    queryKey: ['intelligence', 'themes', 'hot', THEME_LIMIT],
    queryFn: () => api.listThemes({ category: 'hot', limit: THEME_LIMIT }),
  });
  const emergingThemesQuery = useQuery({
    queryKey: ['intelligence', 'themes', 'emerging', THEME_LIMIT],
    queryFn: () => api.listThemes({ category: 'emerging', limit: THEME_LIMIT }),
  });
  const overcrowdedThemesQuery = useQuery({
    queryKey: ['intelligence', 'themes', 'overcrowded', THEME_LIMIT],
    queryFn: () => api.listThemes({ category: 'overcrowded', limit: THEME_LIMIT }),
  });
  const briefQuery = useQuery({
    queryKey: ['intelligence', 'brief', 'morning'],
    queryFn: api.getMorningBrief,
  });

  const themeQueries = {
    hot: hotThemesQuery,
    emerging: emergingThemesQuery,
    overcrowded: overcrowdedThemesQuery,
  };

  const runMutation = useMutation({
    mutationFn: (processOnly: boolean) =>
      api.runIntelligence(processOnly ? { process_only: true } : {}),
    onSuccess: async (result, processOnly) => {
      await queryClient.invalidateQueries({ queryKey: ['intelligence'] });
      const action = processOnly ? '处理' : '采集与处理';
      queryClient.setQueryData(['intelligence', 'last-run'], {
        action,
        collected: result.collected,
        deduplicated: result.deduplicated,
        event_count: result.event_count,
        theme_count: result.theme_count,
        generated_at: result.generated_at,
      });
    },
  });

  const overview = overviewQuery.data;
  const brief = briefQuery.data?.brief ?? overview?.brief ?? null;
  const briefSectionsData = brief ? briefSections(brief) : [];
  const metrics = [
    { label: '信息条目', value: overview?.information_count, icon: FileText },
    { label: '重大事件', value: overview?.event_count, icon: Radio },
    { label: '主题数量', value: overview?.theme_count, icon: Tags },
    { label: '数据来源', value: overview?.source_count, icon: Newspaper },
  ];
  const lastRun = queryClient.getQueryData<{
    action: string;
    collected: number;
    deduplicated: number;
    event_count: number;
    theme_count: number;
    generated_at: string;
  }>(['intelligence', 'last-run']);

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>市场情报</h1>
          <div className="muted">
            聚合多源信息、事件簇与主题热度，辅助盘前判断和信息审计。
          </div>
        </div>
        <span className="badge badge-info">
          <Newspaper size={14} />
          信息与事件
        </span>
      </div>

      <div className="panel intelligence-overview-panel">
        <div className="intelligence-overview-head">
          <div>
            <div className="section-title-main">
              <Radio size={16} />
              情报总览
            </div>
            <div className="muted intelligence-generated-at">
              最近生成：{formatTimestamp(overview?.generated_at)}
            </div>
          </div>
          <div className="intelligence-actions">
            <button
              className="button"
              type="button"
              disabled={runMutation.isPending}
              onClick={() => {
                runMutation.reset();
                runMutation.mutate(true);
              }}
            >
              {runMutation.isPending && runMutation.variables ? (
                <Loader2 className="spin" size={14} />
              ) : (
                <RefreshCw size={14} />
              )}
              仅处理存量
            </button>
            <button
              className="button button-primary"
              type="button"
              disabled={runMutation.isPending}
              onClick={() => {
                runMutation.reset();
                runMutation.mutate(false);
              }}
            >
              {runMutation.isPending && !runMutation.variables ? (
                <Loader2 className="spin" size={14} />
              ) : (
                <Zap size={14} />
              )}
              运行采集与处理
            </button>
          </div>
        </div>

        {overviewQuery.isError ? (
          <div className="intelligence-feedback">
            <ErrorState
              message={`情报总览加载失败：${errorText(overviewQuery.error)}`}
              onRetry={() => void overviewQuery.refetch()}
            />
          </div>
        ) : null}

        <div className="intelligence-overview-grid">
          {metrics.map((metric) => {
            const Icon = metric.icon;
            return (
              <div className="intelligence-metric" key={metric.label}>
                <span className="intelligence-metric-icon">
                  <Icon size={17} />
                </span>
                <div>
                  <div className="stat-label">{metric.label}</div>
                  <div className="stat-value">
                    {formatCount(metric.value, overviewQuery.isLoading)}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        {lastRun ? (
          <div className="intelligence-feedback intelligence-feedback-success" role="status">
            {lastRun.action}完成：采集 {lastRun.collected} 条，去重 {lastRun.deduplicated} 条，
            事件 {lastRun.event_count} 个，主题 {lastRun.theme_count} 个。
            生成时间 {formatTimestamp(lastRun.generated_at)}。
          </div>
        ) : null}
        {runMutation.isError ? (
          <div className="intelligence-feedback intelligence-feedback-error" role="alert">
            <span>运行失败：{errorText(runMutation.error)}</span>
            <button
              className="button"
              type="button"
              disabled={runMutation.isPending}
              onClick={() => runMutation.mutate(runMutation.variables ?? false)}
            >
              <RefreshCw size={14} />
              重试
            </button>
          </div>
        ) : null}
      </div>

      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">
            <Tags size={16} />
            主题热度
          </span>
          <span className="section-title-actions muted">当前、前瞻与拥挤度独立展示</span>
        </div>
        <div className="intelligence-theme-grid">
          {THEME_VIEWS.map((view) => {
            const query = themeQueries[view.key];
            const themes = query.data?.items ?? [];
            return (
              <section className="intelligence-theme-section" key={view.key}>
                <div className="intelligence-theme-head">
                  <div>
                    <strong>{view.title}</strong>
                    <span>{view.description}</span>
                  </div>
                  <span className="tag">{query.data?.count ?? 0}</span>
                </div>
                {query.isLoading ? (
                  <div className="table-wrap intelligence-theme-table-wrap">
                    <table className="table intelligence-theme-table">
                      <thead>
                        <tr>
                          <th>主题</th>
                          <th>当前热度</th>
                          <th>未来热度</th>
                          <th>拥挤度</th>
                        </tr>
                      </thead>
                      <tbody>
                        <ThemeLoadingRows />
                      </tbody>
                    </table>
                  </div>
                ) : query.isError ? (
                  <ErrorState
                    message={errorText(query.error)}
                    onRetry={() => void query.refetch()}
                  />
                ) : (
                  <ThemeTable themes={themes} emptyText={`暂无${view.title}。`} />
                )}
              </section>
            );
          })}
        </div>
      </div>

      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">
            <Radio size={16} />
            重大事件
          </span>
          <span className="tag">{eventsQuery.data?.count ?? 0} 个事件</span>
        </div>
        {eventsQuery.isLoading ? (
          <EventLoadingRows />
        ) : eventsQuery.isError ? (
          <ErrorState
            message={`重大事件加载失败：${errorText(eventsQuery.error)}`}
            onRetry={() => void eventsQuery.refetch()}
          />
        ) : eventsQuery.data?.items.length ? (
          <div className="intelligence-event-list">
            {eventsQuery.data.items.map((event) => (
              <EventItem event={event} key={event.id} />
            ))}
          </div>
        ) : (
          <div className="empty">暂无重大事件。运行采集后，达到重要性阈值的事件会显示在这里。</div>
        )}
      </div>

      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">
            <FileText size={16} />
            盘前快报
            {brief?.title ? <span className="tag">{brief.title}</span> : null}
          </span>
          <span className="section-title-actions muted">
            {formatTimestamp(brief?.generated_at ?? overview?.generated_at)}
          </span>
        </div>
        {briefQuery.isLoading && !brief ? (
          <div className="brief-loading" aria-label="正在加载盘前快报">
            {[0, 1, 2, 3].map((row) => (
              <span className="loading-block loading-block-full" key={row} />
            ))}
          </div>
        ) : briefQuery.isError && !brief ? (
          <ErrorState
            message={`盘前快报加载失败：${errorText(briefQuery.error)}`}
            onRetry={() => void briefQuery.refetch()}
          />
        ) : brief && briefSectionsData.length ? (
          <div className="brief-grid">
            {briefSectionsData.map((section, index) => (
              <section className="brief-section" key={`${section.title}-${index}`}>
                <h2>{section.title}</h2>
                {section.lines.map((line, lineIndex) => (
                  <p key={`${line}-${lineIndex}`}>{line}</p>
                ))}
              </section>
            ))}
          </div>
        ) : (
          <div className="empty">暂无盘前快报。生成快报后，会按市场环境、政策、事件与风险分栏展示。</div>
        )}
      </div>
    </div>
  );
}
