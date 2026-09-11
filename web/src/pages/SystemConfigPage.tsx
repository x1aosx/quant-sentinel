import { useEffect, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Bell,
  Check,
  Plus,
  RefreshCcw,
  Save,
  ServerCog,
  ShieldCheck,
  Trash2,
  Wifi,
} from 'lucide-react';
import { api } from '../api/client';
import type { MonitorTarget, SystemConfig } from '../types';

const EMPTY_TARGET: MonitorTarget = {
  symbol: '',
  timeframe: '15m',
  source: 'yfinance',
  enabled: true,
  analysis: {
    analysis_bar_count: 120,
    decision_stance: 'balanced',
    enable_next_bar_prediction: true,
  },
};

export function SystemConfigPage() {
  const queryClient = useQueryClient();
  const configQuery = useQuery({ queryKey: ['system-config'], queryFn: api.getSystemConfig });
  const [form, setForm] = useState<SystemConfig | null>(null);
  const [notice, setNotice] = useState('');
  const [error, setError] = useState('');

  useEffect(() => {
    if (configQuery.data) setForm(configQuery.data);
  }, [configQuery.data]);

  const save = useMutation({
    mutationFn: (value: SystemConfig) => api.saveSystemConfig(value),
    onSuccess: (value) => {
      setForm(value);
      setNotice('系统配置已保存');
      setError('');
      void queryClient.invalidateQueries({ queryKey: ['system-config'] });
    },
    onError: (reason: Error) => {
      setNotice('');
      setError(reason.message);
    },
  });

  const testFeishu = useMutation({
    mutationFn: () => api.testFeishu(),
    onSuccess: (result) =>
      setNotice(result.sent ? '飞书测试通知已发送' : `飞书测试未发送：${result.reason ?? '未知原因'}`),
    onError: (reason: Error) => setError(reason.message),
  });

  const reset = useMutation({
    mutationFn: api.resetSystemConfig,
    onSuccess: (value) => {
      setForm(value);
      setNotice('已恢复默认配置');
      setError('');
      void queryClient.invalidateQueries({ queryKey: ['system-config'] });
    },
    onError: (reason: Error) => setError(reason.message),
  });

  if (!form) {
    return (
      <div className="empty">{configQuery.isError ? '系统配置加载失败。' : '正在加载系统配置...'}</div>
    );
  }

  const updateProvider = (patch: Partial<SystemConfig['provider']>) =>
    setForm({ ...form, provider: { ...form.provider, ...patch } });
  const updateAnalysis = (patch: Partial<SystemConfig['analysis']>) =>
    setForm({ ...form, analysis: { ...form.analysis, ...patch } });
  const updateFeishu = (patch: Partial<SystemConfig['feishu']>) =>
    setForm({ ...form, feishu: { ...form.feishu, ...patch } });
  const updateTarget = (index: number, patch: Partial<MonitorTarget>) => {
    const monitor_watchlist = form.monitor_watchlist.map((target, targetIndex) =>
      targetIndex === index ? { ...target, ...patch } : target,
    );
    setForm({ ...form, monitor_watchlist });
  };

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>系统配置</h1>
          <div className="muted">
            统一管理模型连接、网络代理、分析默认值和飞书通知。敏感值仅由本地后端保存并脱敏返回。
          </div>
        </div>
        <span className="badge badge-ok">
          <ShieldCheck size={14} />
          本地持久化
        </span>
      </div>

      {notice ? <div className="notice">{notice}</div> : null}
      {error ? <div className="empty">错误：{error}</div> : null}

      <div className="grid grid-2">
        <div className="panel">
          <div className="section-title">
            <ServerCog size={15} />
            大模型连接
          </div>
          <div className="form-grid">
            <div className="field">
              <label htmlFor="config-model">模型名</label>
              <input
                id="config-model"
                value={form.provider.model}
                onChange={(event) => updateProvider({ model: event.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="config-base-url">Base URL</label>
              <input
                id="config-base-url"
                value={form.provider.base_url}
                onChange={(event) => updateProvider({ base_url: event.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="config-api-key">API Key</label>
              <input
                id="config-api-key"
                type="password"
                autoComplete="off"
                value={form.provider.api_key}
                onChange={(event) => updateProvider({ api_key: event.target.value })}
                placeholder={form.provider.api_key_configured ? '已配置，留空则保留' : ''}
              />
            </div>
            <div className="field">
              <label htmlFor="config-reasoning">推理强度</label>
              <select
                id="config-reasoning"
                value={form.provider.reasoning_effort}
                onChange={(event) => updateProvider({ reasoning_effort: event.target.value })}
              >
                <option value="low">low</option>
                <option value="medium">medium</option>
                <option value="high">high</option>
                <option value="max">max</option>
              </select>
            </div>
            <div className="field">
              <label htmlFor="config-timeout">请求超时（秒）</label>
              <input
                id="config-timeout"
                type="number"
                min="1"
                max="600"
                value={form.provider.timeout_seconds}
                onChange={(event) =>
                  updateProvider({ timeout_seconds: Number(event.target.value) || 60 })
                }
              />
            </div>
            <div className="field">
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={form.provider.thinking}
                  onChange={(event) => updateProvider({ thinking: event.target.checked })}
                />
                启用思考模式
              </label>
            </div>
          </div>
        </div>

        <div className="panel">
          <div className="section-title">
            <Wifi size={15} />
            网络代理
          </div>
          <div className="upload-panel">
            <div className="field">
              <label htmlFor="config-proxy">HTTP / SOCKS 代理</label>
              <input
                id="config-proxy"
                value={form.provider.proxy_url}
                onChange={(event) => updateProvider({ proxy_url: event.target.value })}
                placeholder="http://127.0.0.1:7890"
              />
            </div>
            <p className="muted">
              代理同时用于模型请求和飞书通知；远程行情请求沿用系统网络环境。
            </p>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="section-title">
          <Check size={15} />
          分析默认值
        </div>
        <div className="form-grid">
          <div className="field">
            <label htmlFor="config-bars">分析 K 线数</label>
            <input
              id="config-bars"
              type="number"
              min="60"
              max="1000"
              value={form.analysis.analysis_bar_count}
              onChange={(event) =>
                updateAnalysis({ analysis_bar_count: Number(event.target.value) || 120 })
              }
            />
          </div>
          <div className="field">
            <label htmlFor="config-stance">决策风格</label>
            <select
              id="config-stance"
              value={form.analysis.decision_stance}
              onChange={(event) =>
                updateAnalysis({
                  decision_stance: event.target.value as SystemConfig['analysis']['decision_stance'],
                })
              }
            >
              <option value="conservative">保守</option>
              <option value="balanced">均衡</option>
              <option value="aggressive">积极</option>
              <option value="extreme_aggressive">激进</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="config-concurrency">并发分析数</label>
            <input
              id="config-concurrency"
              type="number"
              min="1"
              max="8"
              value={form.analysis.concurrency}
              onChange={(event) =>
                updateAnalysis({ concurrency: Number(event.target.value) || 3 })
              }
            />
          </div>
          <div className="field">
            <label htmlFor="config-interval">盯盘轮询秒数</label>
            <input
              id="config-interval"
              type="number"
              min="1"
              value={form.analysis.monitor_interval_seconds}
              onChange={(event) =>
                updateAnalysis({
                  monitor_interval_seconds: Number(event.target.value) || 60,
                })
              }
            />
          </div>
        </div>
        <div className="row" style={{ marginTop: 14 }}>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={form.analysis.enable_next_bar_prediction}
              onChange={(event) =>
                updateAnalysis({ enable_next_bar_prediction: event.target.checked })
              }
            />
            默认预测下一根 K 线
          </label>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={form.analysis.keep_analysis}
              onChange={(event) => updateAnalysis({ keep_analysis: event.target.checked })}
            />
            默认持续分析
          </label>
        </div>
      </div>

      <div className="panel">
        <div className="section-title">
          <Bell size={15} />
          飞书通知
        </div>
        <div className="form-grid">
          <div className="field">
            <label htmlFor="config-webhook">Webhook URL</label>
            <input
              id="config-webhook"
              value={form.feishu.webhook_url}
              onChange={(event) => updateFeishu({ webhook_url: event.target.value })}
            />
          </div>
          <div className="field">
            <label htmlFor="config-secret">签名 Secret</label>
            <input
              id="config-secret"
              type="password"
              autoComplete="off"
              value={form.feishu.secret}
              onChange={(event) => updateFeishu({ secret: event.target.value })}
            />
          </div>
          <div className="field">
            <label htmlFor="config-confidence">最低置信度</label>
            <input
              id="config-confidence"
              type="number"
              min="0"
              max="100"
              value={form.feishu.confidence_threshold}
              onChange={(event) =>
                updateFeishu({ confidence_threshold: Number(event.target.value) || 0 })
              }
            />
          </div>
        </div>
        <div className="row" style={{ marginTop: 14 }}>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={form.feishu.enabled}
              onChange={(event) => updateFeishu({ enabled: event.target.checked })}
            />
            启用飞书通知
          </label>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={form.feishu.notify_on_order_only}
              onChange={(event) => updateFeishu({ notify_on_order_only: event.target.checked })}
            />
            仅交易意图时通知
          </label>
          <button
            className="button"
            onClick={() => testFeishu.mutate()}
            disabled={testFeishu.isPending}
          >
            <Bell size={14} />
            {testFeishu.isPending ? '发送中...' : '发送测试'}
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="section-title">
          <ServerCog size={15} />
          默认监控列表
          <button
            className="button"
            onClick={() =>
              setForm({
                ...form,
                monitor_watchlist: [...form.monitor_watchlist, { ...EMPTY_TARGET }],
              })
            }
          >
            <Plus size={14} />
            添加标的
          </button>
        </div>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>启用</th>
                <th>数据源</th>
                <th>标的</th>
                <th>周期</th>
                <th>K线数</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {form.monitor_watchlist.length === 0 ? (
                <tr>
                  <td colSpan={6}>
                    <div className="empty">尚未配置默认监控标的。</div>
                  </td>
                </tr>
              ) : (
                form.monitor_watchlist.map((target, index) => (
                  <tr key={`watchlist-${index}`}>
                    <td>
                      <input
                        type="checkbox"
                        checked={target.enabled}
                        onChange={(event) => updateTarget(index, { enabled: event.target.checked })}
                      />
                    </td>
                    <td>
                      <select
                        value={target.source}
                        onChange={(event) => updateTarget(index, { source: event.target.value })}
                      >
                        <option value="yfinance">YFinance</option>
                        <option value="akshare">AkShare</option>
                      </select>
                    </td>
                    <td>
                      <input
                        value={target.symbol}
                        onChange={(event) => updateTarget(index, { symbol: event.target.value })}
                        placeholder="GC=F / 600519"
                      />
                    </td>
                    <td>
                      <select
                        value={target.timeframe}
                        onChange={(event) => updateTarget(index, { timeframe: event.target.value })}
                      >
                        {['1m', '5m', '15m', '30m', '1h', '1d', '1w'].map((timeframe) => (
                          <option key={timeframe} value={timeframe}>
                            {timeframe}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input
                        type="number"
                        min="60"
                        max="1000"
                        value={target.analysis?.analysis_bar_count ?? 120}
                        onChange={(event) =>
                          updateTarget(index, {
                            analysis: {
                              ...target.analysis,
                              analysis_bar_count: Number(event.target.value) || 120,
                            },
                          })
                        }
                      />
                    </td>
                    <td>
                      <button
                        className="button button-danger"
                        onClick={() =>
                          setForm({
                            ...form,
                            monitor_watchlist: form.monitor_watchlist.filter(
                              (_, targetIndex) => targetIndex !== index,
                            ),
                          })
                        }
                      >
                        <Trash2 size={14} />
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      <div className="row">
        <button className="button button-primary" onClick={() => save.mutate(form)} disabled={save.isPending}>
          <Save size={14} />
          {save.isPending ? '保存中...' : '保存系统配置'}
        </button>
        <button className="button" onClick={() => reset.mutate()} disabled={reset.isPending}>
          <RefreshCcw size={14} />
          恢复默认
        </button>
      </div>
    </div>
  );
}
