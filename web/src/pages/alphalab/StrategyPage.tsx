import { useMemo, useState, type FormEvent } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Boxes,
  CircleDot,
  FileJson,
  GitBranch,
  Search,
  ShieldCheck,
  Upload,
} from 'lucide-react';
import { strategyApi } from '../../api/alphalab/strategies';
import {
  AlphaLabEmpty,
  AlphaLabError,
  AlphaLabLoading,
  AlphaLabMetric,
  AlphaLabQueryStatus,
  AlphaLabStatus,
} from '../../components/alphalab/AlphaLabStates';
import {
  errorText,
  formatCompact,
  formatDateTime,
  formatNumber,
  shortId,
} from '../../components/alphalab/format';
import type {
  StrategyArtifact,
  StrategyImportRequest,
} from '../../types/alphalab/strategy';
import '../../styles/alphalab.css';

function rangeLabel(range: StrategyArtifact['training_range']): string {
  if (!range) return '--';
  const start = range.start || range.start_at || '--';
  const end = range.end || range.end_at || '--';
  return `${start} -> ${end}`;
}

function JsonBlock({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <AlphaLabEmpty>暂无数据</AlphaLabEmpty>;
  return <pre className="alphalab-json">{JSON.stringify(value, null, 2)}</pre>;
}

export function StrategyPage() {
  const queryClient = useQueryClient();
  const [importName, setImportName] = useState('');
  const [importJson, setImportJson] = useState('');
  const [filter, setFilter] = useState('');
  const [formError, setFormError] = useState('');
  const [actionMessage, setActionMessage] = useState('');
  const [selectedId, setSelectedId] = useState('');

  const overviewQuery = useQuery({
    queryKey: ['alphalab', 'strategy', 'overview'],
    queryFn: strategyApi.overview,
    refetchInterval: 20_000,
  });
  const strategiesQuery = useQuery({
    queryKey: ['alphalab', 'strategy', 'list'],
    queryFn: strategyApi.list,
    refetchInterval: 20_000,
  });
  const detailQuery = useQuery({
    queryKey: ['alphalab', 'strategy', 'detail', selectedId],
    queryFn: () => strategyApi.get(selectedId),
    enabled: Boolean(selectedId),
  });

  const strategies = strategiesQuery.data ?? [];
  const selected =
    detailQuery.data ?? strategies.find((strategy) => strategy.id === selectedId) ?? null;
  const filtered = useMemo(() => {
    const keyword = filter.trim().toLowerCase();
    if (!keyword) return strategies;
    return strategies.filter((strategy) =>
      [
        strategy.id,
        strategy.name,
        strategy.version,
        strategy.market,
        strategy.timeframe,
        strategy.symbols?.join(' '),
        strategy.formula_expression,
        strategy.status,
      ]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(keyword)),
    );
  }, [filter, strategies]);
  const queryError = overviewQuery.error ?? strategiesQuery.error ?? detailQuery.error;
  const updatedAt = Math.max(
    overviewQuery.dataUpdatedAt,
    strategiesQuery.dataUpdatedAt,
    detailQuery.dataUpdatedAt,
  );

  const importMutation = useMutation({
    mutationFn: strategyApi.import,
    onSuccess: async (strategy) => {
      setImportJson('');
      setImportName('');
      setFormError('');
      setActionMessage(`策略已导入：${strategy.id}`);
      setSelectedId(strategy.id);
      await queryClient.invalidateQueries({ queryKey: ['alphalab', 'strategy'] });
    },
    onError: (error) => setFormError(errorText(error)),
  });

  const refresh = () => {
    void overviewQuery.refetch();
    void strategiesQuery.refetch();
    if (selectedId) void detailQuery.refetch();
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setFormError('');
    setActionMessage('');
    if (!importJson.trim()) {
      setFormError('请粘贴策略 JSON');
      return;
    }
    let parsed: unknown;
    try {
      parsed = JSON.parse(importJson);
    } catch {
      setFormError('策略内容不是合法 JSON');
      return;
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      setFormError('策略 JSON 必须是对象');
      return;
    }
    const payload: StrategyImportRequest = {
      ...(parsed as Record<string, unknown>),
    };
    if (importName.trim()) payload.name = importName.trim();
    importMutation.mutate(payload);
  };

  return (
    <div className="stack alphalab-page">
      <div className="page-header">
        <div>
          <h1>策略资产</h1>
          <div className="row">
            <AlphaLabStatus status={selected?.status} />
            <span className="tag">{strategies.length} 个版本</span>
          </div>
        </div>
        <AlphaLabQueryStatus
          isFetching={
            overviewQuery.isFetching || strategiesQuery.isFetching || detailQuery.isFetching
          }
          isError={Boolean(queryError)}
          updatedAt={updatedAt}
          onRefresh={refresh}
        />
      </div>

      {actionMessage ? <div className="notice">{actionMessage}</div> : null}
      {queryError ? <AlphaLabError error={queryError} onRetry={refresh} /> : null}

      <div className="alphalab-metric-grid">
        <div className="panel">
          <AlphaLabMetric
            label="候选策略"
            value={
              overviewQuery.data?.candidate_strategies ??
              overviewQuery.data?.strategies?.recent?.filter(
                (strategy) =>
                  String(strategy.status ?? '').toUpperCase() === 'CANDIDATE',
              ).length ??
              strategies.filter((strategy) => strategy.status === 'CANDIDATE').length
            }
          />
        </div>
        <div className="panel">
          <AlphaLabMetric
            label="已验证"
            value={
              overviewQuery.data?.validated_strategies ??
              strategies.filter((strategy) => strategy.status === 'VALIDATED').length
            }
          />
        </div>
        <div className="panel">
          <AlphaLabMetric
            label="生产策略"
            value={
              overviewQuery.data?.production_strategies ??
              overviewQuery.data?.strategies?.production ??
              strategies.filter((strategy) => strategy.status === 'PRODUCTION').length
            }
          />
        </div>
        <div className="panel">
          <AlphaLabMetric label="Schema" value={selected?.factor_schema_version || '--'} />
        </div>
      </div>

      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">
            <FileJson size={15} />
            导入策略
          </span>
        </div>
        <form onSubmit={submit}>
          <div className="form-grid alphalab-form-grid">
            <div className="field">
              <label htmlFor="alpha-strategy-name">名称覆盖</label>
              <input
                id="alpha-strategy-name"
                value={importName}
                onChange={(event) => setImportName(event.target.value)}
                placeholder="留空则使用 JSON 内名称"
              />
            </div>
            <div className="field alphalab-json-field">
              <label htmlFor="alpha-strategy-json">策略 JSON</label>
              <textarea
                id="alpha-strategy-json"
                value={importJson}
                onChange={(event) => setImportJson(event.target.value)}
                placeholder='{"name":"strategy","version":"1","formula_expression":"..."}'
                spellCheck={false}
              />
            </div>
          </div>
          {formError ? <div className="error-text">{formError}</div> : null}
          <div className="row alphalab-form-actions">
            <button
              type="submit"
              className="button button-primary"
              disabled={importMutation.isPending}
            >
              <Upload size={14} />
              {importMutation.isPending ? '导入中' : '导入'}
            </button>
          </div>
        </form>
      </div>

      <div className="panel">
        <div className="section-title">
          <span className="section-title-main">
            <Boxes size={15} />
            策略注册表
          </span>
          <div className="alphalab-search">
            <Search size={14} />
            <input
              value={filter}
              onChange={(event) => setFilter(event.target.value)}
              placeholder="筛选策略、标的、公式"
            />
          </div>
        </div>
        {strategiesQuery.isLoading ? (
          <AlphaLabLoading label="加载策略列表" />
        ) : strategiesQuery.isError ? (
          <AlphaLabError
            error={strategiesQuery.error}
            onRetry={() => void strategiesQuery.refetch()}
          />
        ) : filtered.length ? (
          <div className="table-wrap">
            <table className="table alphalab-table">
              <thead>
                <tr>
                  <th>策略</th>
                  <th>版本</th>
                  <th>市场</th>
                  <th>标的</th>
                  <th>周期</th>
                  <th>公式</th>
                  <th>Schema</th>
                  <th>Train</th>
                  <th>Validation</th>
                  <th>Holdout</th>
                  <th>Backtest</th>
                  <th>状态</th>
                  <th>创建时间</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((strategy) => (
                  <tr
                    key={strategy.id}
                    className={selectedId === strategy.id ? 'selected-row' : undefined}
                    onClick={() => setSelectedId(strategy.id)}
                  >
                    <td>
                      <div className="alphalab-cell-main">{strategy.name}</div>
                      <div className="code">{shortId(strategy.id)}</div>
                    </td>
                    <td>{strategy.version}</td>
                    <td>{strategy.market || '--'}</td>
                    <td className="alphalab-compact-cell">
                      {strategy.symbols?.join(', ') || strategy.symbol_scope || '--'}
                    </td>
                    <td>{strategy.timeframe || '--'}</td>
                    <td className="alphalab-formula-cell">
                      {strategy.formula_expression || '--'}
                    </td>
                    <td className="code">{strategy.factor_schema_version || '--'}</td>
                    <td>{formatNumber(strategy.train_score)}</td>
                    <td>{formatNumber(strategy.validation_score)}</td>
                    <td>{formatNumber(strategy.holdout_score)}</td>
                    <td>{formatNumber(strategy.backtest_score)}</td>
                    <td>
                      <AlphaLabStatus status={strategy.status} />
                    </td>
                    <td>{formatDateTime(strategy.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <AlphaLabEmpty>{filter ? '没有匹配的策略' : '暂无策略制品'}</AlphaLabEmpty>
        )}
      </div>

      <div className="panel alphalab-detail-panel">
        <div className="section-title">
          <span className="section-title-main">
            <ShieldCheck size={15} />
            策略详情
          </span>
          {selected ? <AlphaLabStatus status={selected.status} /> : null}
        </div>
        {!selectedId ? (
          <AlphaLabEmpty>选择一条策略查看详情</AlphaLabEmpty>
        ) : detailQuery.isLoading ? (
          <AlphaLabLoading label="加载策略详情" />
        ) : detailQuery.isError && !selected ? (
          <AlphaLabError
            error={detailQuery.error}
            onRetry={() => void detailQuery.refetch()}
          />
        ) : selected ? (
          <div className="stack compact">
            <div className="inline-meta">
              <div className="meta-item">
                <div className="label">策略 ID</div>
                <div className="value code">{selected.id}</div>
              </div>
              <div className="meta-item">
                <div className="label">版本</div>
                <div className="value">{selected.version}</div>
              </div>
              <div className="meta-item">
                <div className="label">Content Hash</div>
                <div className="value code">{selected.content_hash || '--'}</div>
              </div>
              <div className="meta-item">
                <div className="label">Signal Kernel</div>
                <div className="value">{selected.signal_kernel_version || '--'}</div>
              </div>
              <div className="meta-item">
                <div className="label">Min Exposure</div>
                <div className="value">{formatNumber(selected.min_exposure, 4)}</div>
              </div>
              <div className="meta-item">
                <div className="label">Training Range</div>
                <div className="value">{rangeLabel(selected.training_range)}</div>
              </div>
            </div>

            <div className="alphalab-detail-metrics">
              <AlphaLabMetric
                label="Train"
                value={formatNumber(selected.train_score ?? selected.metrics_json?.train_score)}
              />
              <AlphaLabMetric
                label="Validation"
                value={formatNumber(
                  selected.validation_score ?? selected.metrics_json?.validation_score,
                )}
              />
              <AlphaLabMetric
                label="Holdout"
                value={formatNumber(
                  selected.holdout_score ?? selected.metrics_json?.holdout_score,
                )}
              />
              <AlphaLabMetric
                label="Backtest"
                value={formatNumber(
                  selected.backtest_score ?? selected.metrics_json?.backtest_score,
                )}
              />
              <AlphaLabMetric
                label="Sharpe"
                value={formatNumber(selected.metrics_json?.sharpe)}
              />
              <AlphaLabMetric
                label="Max DD"
                value={formatNumber(selected.metrics_json?.max_drawdown)}
              />
            </div>

            <div className="alphalab-formula">
              <div className="label">Formula</div>
              <div className="code">{selected.formula_expression || '--'}</div>
              <div className="alphalab-token-row">
                {(selected.formula_tokens ?? []).map((token, index) => (
                  <span className="tag" key={`${token}-${index}`}>
                    {token}
                  </span>
                ))}
              </div>
            </div>

            <div className="alphalab-detail-columns">
              <div className="alphalab-subsection">
                <div className="section-title">
                  <span className="section-title-main">
                    <GitBranch size={15} />
                    Lineage
                  </span>
                </div>
                <JsonBlock value={selected.lineage} />
              </div>
              <div className="alphalab-subsection">
                <div className="section-title">
                  <span className="section-title-main">
                    <CircleDot size={15} />
                    Robustness
                  </span>
                </div>
                <JsonBlock value={selected.robustness_json} />
              </div>
            </div>

            <div className="alphalab-subsection">
              <div className="section-title">
                <span className="section-title-main">
                  <Boxes size={15} />
                  Realtime References
                </span>
                <span className="tag">
                  {selected.realtime_references?.length ?? 0} 条
                </span>
              </div>
              {selected.realtime_references?.length ? (
                <div className="alphalab-reference-grid">
                  {selected.realtime_references.map((reference, index) => (
                    <div className="alphalab-reference" key={index}>
                      <span>{formatCompact(reference.source)}</span>
                      <strong>{formatCompact(reference.symbol)}</strong>
                      <span>{formatCompact(reference.timeframe)}</span>
                      <AlphaLabStatus status={formatCompact(reference.state)} />
                    </div>
                  ))}
                </div>
              ) : (
                <AlphaLabEmpty>暂无实时引用</AlphaLabEmpty>
              )}
            </div>
          </div>
        ) : (
          <AlphaLabEmpty>暂无策略详情</AlphaLabEmpty>
        )}
      </div>
    </div>
  );
}
