import { Fragment, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertTriangle,
  BarChart3,
  ChevronDown,
  ChevronRight,
  Crosshair,
  Loader2,
  RefreshCw,
  ShieldAlert,
  Sparkles,
  Target,
} from 'lucide-react';
import { api } from '../api/client';
import type {
  DiscoveryCandidate,
  DiscoveryCandidateState,
  DiscoveryScoreContribution,
  DiscoveryScoreMap,
} from '../types';

const CANDIDATE_LIMIT = 100;

type StateFilter = DiscoveryCandidateState | 'all';

const STATE_FILTERS: Array<{ value: StateFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'DISCOVERED', label: '已发现' },
  { value: 'WATCH', label: '观察' },
  { value: 'FOCUS', label: '重点' },
  { value: 'DEEP_ANALYSIS', label: '深度分析' },
  { value: 'SIGNAL_READY', label: '信号就绪' },
  { value: 'COOLDOWN', label: '冷却' },
  { value: 'REMOVE', label: '移除' },
];

const SCORE_LABELS: Record<string, string> = {
  market_fit_score: '市场匹配',
  theme_score: '主题强度',
  forward_theme_score: '未来热度',
  policy_score: '政策强度',
  global_event_score: '国际事件',
  sentiment_score: '情绪',
  attention_momentum_score: '关注动能',
  capital_score: '资金',
  price_action_score: '价格行为',
  alpha_score: 'Alpha',
  fundamental_score: '基本面',
  crowding_score: '拥挤度',
  risk_score: '风险',
  liquidity_score: '流动性',
};

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : '请求失败';
}

function formatNumber(value?: number | null, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '--';
  return value.toLocaleString('zh-CN', { maximumFractionDigits: digits });
}

function candidateName(candidate: DiscoveryCandidate): string {
  return candidate.name ?? candidate.stock_name ?? candidate.symbol ?? candidate.stock_id;
}

function candidateCode(candidate: DiscoveryCandidate): string {
  return candidate.code ?? candidate.stock_code ?? candidate.symbol ?? candidate.stock_id;
}

function stateLabel(state: string): string {
  const labels: Record<string, string> = {
    DISCOVERED: '已发现',
    WATCH: '观察',
    FOCUS: '重点',
    DEEP_ANALYSIS: '深度分析',
    SIGNAL_READY: '信号就绪',
    COOLDOWN: '冷却',
    REMOVE: '移除',
  };
  return labels[state] ?? state.replace(/_/g, ' ');
}

function stateTone(state: string): string {
  if (state === 'SIGNAL_READY') return 'discovery-state-ready';
  if (state === 'FOCUS') return 'discovery-state-focus';
  if (state === 'DEEP_ANALYSIS') return 'discovery-state-info';
  if (state === 'WATCH' || state === 'DISCOVERED') return 'discovery-state-watch';
  if (state === 'COOLDOWN' || state === 'REMOVE') return 'discovery-state-muted';
  return 'discovery-state-muted';
}

function candidateThemes(candidate: DiscoveryCandidate): string[] {
  if (candidate.theme_names?.length) return candidate.theme_names;
  if (candidate.themes?.length) {
    return candidate.themes.map((theme) => (typeof theme === 'string' ? theme : theme.name));
  }
  return candidate.theme_ids ?? [];
}

function scoreMapRows(
  scores?: DiscoveryScoreMap | null,
): Array<{ key: string; label: string; value: number | null | undefined }> {
  if (!scores) return [];
  return Object.entries(scores).map(([key, value]) => ({
    key,
    label: SCORE_LABELS[key] ?? key,
    value,
  }));
}

function contributionRows(
  candidate: DiscoveryCandidate,
): Array<{
  key: string;
  label: string;
  score?: number | null;
  weight?: number | null;
  contribution: number | null | undefined;
}> {
  const contributions = candidate.score_contributions;
  if (Array.isArray(contributions)) {
    return contributions.map((item: DiscoveryScoreContribution, index) => {
      const key = item.key ?? item.factor ?? item.name ?? `factor_${index + 1}`;
      return {
        key,
        label: item.label ?? SCORE_LABELS[key] ?? key,
        score: item.score,
        weight: item.weight,
        contribution: item.contribution ?? item.score,
      };
    });
  }

  return scoreMapRows(contributions).map((item) => ({
    key: item.key,
    label: item.label,
    contribution: item.value,
  }));
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

function DiscoveryLoadingRows() {
  return (
    <>
      {[0, 1, 2, 3, 4].map((row) => (
        <tr key={row}>
          <td>
            <span className="loading-block" />
          </td>
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
          <td>
            <span className="loading-block loading-block-full" />
          </td>
          <td />
        </tr>
      ))}
    </>
  );
}

function CandidateDetails({ candidate }: { candidate: DiscoveryCandidate }) {
  const contributions = contributionRows(candidate);
  const factors = scoreMapRows(candidate.scores);
  const reasons = candidate.reasons ?? [];
  const risks = candidate.risks ?? [];

  return (
    <div className="discovery-detail">
      <div className="discovery-detail-head">
        <div>
          <strong>候选审计</strong>
          <span>
            排名 {candidate.rank} · 置信度 {formatNumber(candidate.confidence, 2)}
          </span>
        </div>
        <span className="tag">model_version: {candidate.model_version}</span>
      </div>
      <div className="discovery-detail-grid">
        <section className="discovery-detail-section">
          <h4>
            <Sparkles size={15} />
            入选原因
          </h4>
          {reasons.length ? (
            <ul className="discovery-reason-list">
              {reasons.map((reason, index) => (
                <li key={`${reason}-${index}`}>{reason}</li>
              ))}
            </ul>
          ) : (
            <span className="muted">暂无原因记录</span>
          )}
        </section>

        <section className="discovery-detail-section">
          <h4>
            <ShieldAlert size={15} />
            风险提示
          </h4>
          {risks.length ? (
            <ul className="discovery-risk-list">
              {risks.map((risk, index) => (
                <li key={`${risk}-${index}`}>{risk}</li>
              ))}
            </ul>
          ) : (
            <span className="muted">暂无风险记录</span>
          )}
        </section>

        <section className="discovery-detail-section discovery-score-section">
          <h4>
            <BarChart3 size={15} />
            分数贡献
          </h4>
          {contributions.length ? (
            <div className="discovery-score-grid">
              {contributions.map((item) => (
                <div className="discovery-score-item" key={item.key}>
                  <span>{item.label}</span>
                  <strong>{formatNumber(item.contribution, 3)}</strong>
                  <small>
                    {item.weight !== null && item.weight !== undefined
                      ? `权重 ${formatNumber(item.weight, 3)}`
                      : item.score !== null && item.score !== undefined
                        ? `因子分 ${formatNumber(item.score, 3)}`
                        : '贡献值'}
                  </small>
                </div>
              ))}
            </div>
          ) : (
            <span className="muted">暂无分数贡献记录</span>
          )}
          {factors.length ? (
            <div className="discovery-factor-scores">
              <span className="label">因子得分</span>
              <div>
                {factors.map((item) => (
                  <span className="tag" key={item.key}>
                    {item.label} {formatNumber(item.value, 2)}
                  </span>
                ))}
              </div>
            </div>
          ) : null}
        </section>
      </div>
    </div>
  );
}

export function DiscoveryPage() {
  const queryClient = useQueryClient();
  const [stateFilter, setStateFilter] = useState<StateFilter>('all');
  const [expandedCandidateId, setExpandedCandidateId] = useState<string | null>(null);

  const candidatesQuery = useQuery({
    queryKey: ['discovery', 'candidates', stateFilter, CANDIDATE_LIMIT],
    queryFn: () =>
      api.listDiscoveryCandidates({ state: stateFilter, limit: CANDIDATE_LIMIT }),
  });

  const refreshMutation = useMutation({
    mutationFn: api.refreshDiscovery,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['discovery', 'candidates'] });
    },
  });

  const candidates = candidatesQuery.data?.items ?? [];

  const toggleCandidate = (candidate: DiscoveryCandidate) => {
    setExpandedCandidateId((current) =>
      current === candidate.stock_id ? null : candidate.stock_id,
    );
  };

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>机会发现</h1>
          <div className="muted">
            按 DiscoveryScore 查看候选排序、状态与入选依据，并保留因子级审计信息。
          </div>
        </div>
        <span className="badge badge-info">
          <Crosshair size={14} />
          候选池
        </span>
      </div>

      <div className="panel discovery-panel">
        <div className="discovery-toolbar">
          <div className="segmented-scroll discovery-filter-scroll">
            {STATE_FILTERS.map((filter) => (
              <button
                className={`button discovery-filter-button${
                  stateFilter === filter.value ? ' active' : ''
                }`}
                type="button"
                key={filter.value}
                onClick={() => {
                  setStateFilter(filter.value);
                  setExpandedCandidateId(null);
                }}
              >
                {filter.label}
              </button>
            ))}
          </div>
          <div className="discovery-toolbar-actions">
            <span className="muted">
              交易日 {candidatesQuery.data?.trade_date ?? '--'} · {candidatesQuery.data?.count ?? 0}{' '}
              只
            </span>
            <button
              className="button button-primary"
              type="button"
              disabled={refreshMutation.isPending}
              onClick={() => {
                refreshMutation.reset();
                refreshMutation.mutate();
              }}
            >
              {refreshMutation.isPending ? (
                <Loader2 className="spin" size={14} />
              ) : (
                <RefreshCw size={14} />
              )}
              刷新候选池
            </button>
          </div>
        </div>

        {refreshMutation.isError ? (
          <div className="intelligence-error-state" role="alert">
            <span>
              <AlertTriangle size={15} />
              刷新失败：{errorText(refreshMutation.error)}
            </span>
            <button
              className="button"
              type="button"
              disabled={refreshMutation.isPending}
              onClick={() => refreshMutation.mutate()}
            >
              <RefreshCw size={14} />
              重试
            </button>
          </div>
        ) : null}

        {refreshMutation.isSuccess ? (
          <div className="intelligence-feedback intelligence-feedback-success" role="status">
            已刷新 {refreshMutation.data.trade_date} 候选池，共 {refreshMutation.data.count} 只。
          </div>
        ) : null}

        {candidatesQuery.isError ? (
          <ErrorState
            message={`候选股票加载失败：${errorText(candidatesQuery.error)}`}
            onRetry={() => void candidatesQuery.refetch()}
          />
        ) : (
          <div className="table-wrap discovery-table-wrap">
            <table className="table discovery-table">
              <thead>
                <tr>
                  <th>排名</th>
                  <th>名称</th>
                  <th>代码</th>
                  <th>状态</th>
                  <th>DiscoveryScore</th>
                  <th>主题</th>
                  <th>未来热度</th>
                  <th>拥挤</th>
                  <th>风险</th>
                  <th>原因提示</th>
                  <th aria-label="展开详情" />
                </tr>
              </thead>
              <tbody>
                {candidatesQuery.isLoading ? (
                  <DiscoveryLoadingRows />
                ) : candidates.length ? (
                  candidates.map((candidate) => {
                    const expanded = expandedCandidateId === candidate.stock_id;
                    const themes = candidateThemes(candidate);
                    const forwardHeat =
                      candidate.forward_heat_score ?? candidate.forward_theme_score;
                    return (
                      <Fragment key={candidate.stock_id}>
                        <tr
                          className={`discovery-row${expanded ? ' expanded' : ''}`}
                          tabIndex={0}
                          aria-expanded={expanded}
                          onClick={() => toggleCandidate(candidate)}
                          onKeyDown={(event) => {
                            if (event.key === 'Enter' || event.key === ' ') {
                              event.preventDefault();
                              toggleCandidate(candidate);
                            }
                          }}
                        >
                          <td className="discovery-rank">{candidate.rank}</td>
                          <td>
                            <strong>{candidateName(candidate)}</strong>
                          </td>
                          <td className="discovery-code">{candidateCode(candidate)}</td>
                          <td>
                            <span className={`badge ${stateTone(candidate.state)}`}>
                              {stateLabel(candidate.state)}
                            </span>
                          </td>
                          <td className="discovery-score">
                            {formatNumber(candidate.discovery_score, 1)}
                          </td>
                          <td>
                            <div className="discovery-theme-tags">
                              {themes.slice(0, 3).map((theme) => (
                                <span className="tag" key={theme}>
                                  {theme}
                                </span>
                              ))}
                              {themes.length > 3 ? (
                                <span className="tag">+{themes.length - 3}</span>
                              ) : null}
                              {!themes.length ? <span className="muted">--</span> : null}
                            </div>
                          </td>
                          <td className="discovery-number">{formatNumber(forwardHeat)}</td>
                          <td className="discovery-number">
                            {formatNumber(candidate.crowding_score)}
                          </td>
                          <td className="discovery-number">{formatNumber(candidate.risk_score)}</td>
                          <td>
                            <span
                              className="discovery-reason-preview"
                              title={candidate.reasons?.join('\n')}
                            >
                              {candidate.reasons?.[0] ?? '暂无原因记录'}
                            </span>
                          </td>
                          <td className="discovery-expand-cell">
                            {expanded ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
                          </td>
                        </tr>
                        {expanded ? (
                          <tr className="discovery-expanded-row">
                            <td colSpan={11}>
                              <CandidateDetails candidate={candidate} />
                            </td>
                          </tr>
                        ) : null}
                      </Fragment>
                    );
                  })
                ) : (
                  <tr>
                    <td colSpan={11}>
                      <div className="empty discovery-empty">
                        <Target size={18} />
                        当前筛选条件下暂无候选股票。可刷新候选池或切换状态。
                      </div>
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
