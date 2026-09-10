import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Activity, Bell, Bot, CalendarClock, Download, Gauge, LineChart, ListTree, Network, Send, Sparkles, TrendingUp } from 'lucide-react';
import { api } from '../api/client';
import { KlineChart } from '../components/KlineChart';
import type { AIAnalysisRecord, DatasetSummary, SrLevel } from '../types';

const VIEWS = [
  { key: 'decision', label: '决策' },
  { key: 'tree', label: '决策树' },
  { key: 'prompt', label: '原始提示词' },
  { key: 'debug', label: '调试' },
] as const;

type ViewKey = (typeof VIEWS)[number]['key'];

export function AIAnalysisPage() {
  const [datasetId, setDatasetId] = useState('');
  const [symbol, setSymbol] = useState('GC=F');
  const [timeframe, setTimeframe] = useState('1d');
  const [source, setSource] = useState<'yfinance' | 'akshare'>('yfinance');
  const [baseUrl, setBaseUrl] = useState('https://api.deepseek.com/v1');
  const [model, setModel] = useState('deepseek-chat');
  const [apiKey, setApiKey] = useState('');
  const [thinking, setThinking] = useState(false);
  const [barCount, setBarCount] = useState('120');
  const [stance, setStance] = useState('balanced');
  const [view, setView] = useState<ViewKey>('decision');
  const [question, setQuestion] = useState('');
  const [webhookUrl, setWebhookUrl] = useState('');
  const [feishuSecret, setFeishuSecret] = useState('');
  const [notifyOnlyOrder, setNotifyOnlyOrder] = useState(false);
  const [notice, setNotice] = useState('');
  const [pageError, setPageError] = useState('');
  const [streamLog, setStreamLog] = useState('');

  const datasetsQuery = useQuery({ queryKey: ['datasets'], queryFn: api.listDatasets });
  const datasets: DatasetSummary[] = datasetsQuery.data?.items ?? [];

  useEffect(() => {
    if (!datasetId && datasets.length) setDatasetId(datasets[0].id);
  }, [datasetId, datasets]);

  const settingsPayload = () => ({
    provider: {
      base_url: baseUrl,
      model,
      api_key: apiKey,
      thinking,
      reasoning_effort: 'medium',
      context_window: 128000,
    },
    analysis_bar_count: Number(barCount) || 120,
    decision_stance: stance,
    enable_next_bar_prediction: true,
  });

  const importRemote = useMutation({
    mutationFn: () => api.importRemoteDataset({ source, symbol, timeframe, lookback: 500 }),
    onSuccess: (created) => {
      setDatasetId(created.id);
      setNotice(`远程数据导入成功：${created.symbol} ${created.timeframe}`);
      setPageError('');
    },
    onError: (error: Error) => {
      setNotice('');
      setPageError(error.message);
    },
  });

  const analysis = useMutation({
    mutationFn: () => api.analyzeAI({ dataset_id: datasetId, ...settingsPayload() }),
    onSuccess: () => {
      setPageError('');
      setNotice('AI 分析完成');
    },
    onError: (error: Error) => {
      setNotice('');
      setPageError(error.message);
    },
  });

  const readModelStream = async () => {
    setStreamLog('');
    const response = await fetch('/api/v1/ai/analyze/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ dataset_id: datasetId, ...settingsPayload() }),
    });
    if (!response.ok || !response.body) throw new Error(`模型日志流请求失败（${response.status}）`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split('\n\n');
      buffer = events.pop() ?? '';
      for (const event of events) {
        if (!event.startsWith('data:')) continue;
        const payload = JSON.parse(event.slice(5).trim());
        if (payload.type === 'snapshot') setStreamLog((prev) => `${prev}快照 ${payload.symbol} ${payload.timeframe} ${payload.bar_count} 根\n`);
        if (payload.type === 'log' || payload.type === 'stage1' || payload.type === 'stage2') {
          setStreamLog((prev) => `${prev}${payload.text ?? ''}`);
        }
      }
    }
  };

  const runAnalysis = async () => {
    try {
      await readModelStream();
      analysis.mutate();
    } catch (error) {
      setPageError(error instanceof Error ? error.message : String(error));
    }
  };

  const followup = useMutation({
    mutationFn: () => api.followupAI({ record: analysis.data, question, ...settingsPayload() }),
    onSuccess: () => setPageError(''),
    onError: (error: Error) => setPageError(error.message),
  });

  const notify = useMutation({
    mutationFn: () =>
      api.sendFeishu({
        record: analysis.data,
        webhook_url: webhookUrl,
        secret: feishuSecret,
        enabled: true,
        notify_on_order_only: notifyOnlyOrder,
      }),
    onSuccess: (result) =>
      setNotice(result.sent ? '飞书通知已发送' : `飞书通知未发送：${result.reason ?? '未知原因'}`),
    onError: (error: Error) => setPageError(error.message),
  });

  const record: AIAnalysisRecord | undefined = analysis.data;
  const decision = (record?.stage2_decision?.decision ?? {}) as Record<string, any>;
  const future = (record?.stage2_decision?.future_trend ?? {}) as Record<string, any>;
  const diagnosis = record?.stage1_diagnosis ?? {};
  const snapshot = record?.snapshot;
  const supports = (snapshot?.support_resistance?.supports_only ?? []) as SrLevel[];
  const resistances = (snapshot?.support_resistance?.resistances ?? []) as SrLevel[];

  return (
    <div className="stack">
      <div className="page-header">
        <div>
          <h1>AI 分析中心</h1>
          <div className="muted">数据快照、市场诊断、价格行为决策、未来走势与追问一体化。</div>
        </div>
        <span className="tag">{datasets.length} 个可用数据集</span>
      </div>

      <div className="grid grid-2">
        <div className="panel">
          <div className="section-title">
            <Download size={15} />
            网络数据导入
          </div>
          <div className="upload-panel">
            <div className="form-grid">
              <div className="field">
                <label htmlFor="ai-source">数据源</label>
                <select id="ai-source" value={source} onChange={(event) => setSource(event.target.value as 'yfinance' | 'akshare')}>
                  <option value="yfinance">YFinance</option>
                  <option value="akshare">AkShare/A股</option>
                </select>
              </div>
              <div className="field">
                <label htmlFor="ai-symbol">标的代码</label>
                <input id="ai-symbol" value={symbol} onChange={(event) => setSymbol(event.target.value)} placeholder="GC=F 或 600519" />
              </div>
              <div className="field">
                <label htmlFor="ai-timeframe">周期</label>
                <select id="ai-timeframe" value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>
                  {['1m', '5m', '15m', '30m', '1h', '1d', '1w'].map((item) => (
                    <option key={item} value={item}>{item}</option>
                  ))}
                </select>
              </div>
            </div>
          </div>
          <div className="row" style={{ marginTop: 14 }}>
            <button className="button" onClick={() => importRemote.mutate()} disabled={importRemote.isPending}>
              <Download size={14} />
              {importRemote.isPending ? '下载中...' : '下载行情'}
            </button>
            <span className="muted">回看 500 根K线，导入后自动选择数据集。</span>
          </div>
        </div>

        <div className="panel">
          <div className="section-title">
            <Bot size={15} />
            大模型配置
          </div>
          <div className="upload-panel">
            <div className="form-grid">
              <div className="field">
                <label htmlFor="ai-base-url">Base URL</label>
                <input id="ai-base-url" value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="https://api.deepseek.com/v1" />
              </div>
              <div className="field">
                <label htmlFor="ai-model">模型名</label>
                <input id="ai-model" value={model} onChange={(event) => setModel(event.target.value)} placeholder="deepseek-chat" />
              </div>
              <div className="field">
                <label htmlFor="ai-api-key">API Key（仅本次会话）</label>
                <input id="ai-api-key" value={apiKey} onChange={(event) => setApiKey(event.target.value)} type="password" />
              </div>
            </div>
          </div>
          <div className="form-grid" style={{ marginTop: 14 }}>
            <div className="field">
              <label htmlFor="ai-bar-count">分析K线数</label>
              <input id="ai-bar-count" value={barCount} onChange={(event) => setBarCount(event.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="ai-stance">决策风格</label>
              <select id="ai-stance" value={stance} onChange={(event) => setStance(event.target.value)}>
                <option value="conservative">保守</option>
                <option value="balanced">均衡</option>
                <option value="aggressive">积极</option>
              </select>
            </div>
            <div className="field">
              <span>思考模式</span>
              <label className="checkbox-row">
                <input type="checkbox" checked={thinking} onChange={(event) => setThinking(event.target.checked)} />
                {thinking ? '已开启' : '已关闭'}
              </label>
            </div>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="section-title">
          <Sparkles size={15} />
          分析执行
          <span className="tag">两阶段</span>
        </div>
        <div className="upload-panel">
          <div className="field">
            <label htmlFor="ai-dataset">分析数据集</label>
            <select id="ai-dataset" value={datasetId} onChange={(event) => setDatasetId(event.target.value)}>
              {datasets.length === 0 ? <option value="">暂无数据集</option> : null}
              {datasets.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.symbol} · {item.timeframe} · {item.bar_count} 根
                </option>
              ))}
            </select>
          </div>
          <div className="row" style={{ marginTop: 14 }}>
            <button className="button button-primary" onClick={() => void runAnalysis()} disabled={analysis.isPending || !datasetId}>
              <Sparkles size={14} />
              {analysis.isPending ? '分析中...' : '开始两阶段分析'}
            </button>
            <span className="muted">未配置 API Key 时使用本地确定性研究模式。</span>
          </div>
        </div>
        {streamLog ? (
          <div className="stack" style={{ marginTop: 14 }}>
            <div className="section-title"><Activity size={15} />模型日志</div>
            <pre className="raw-prompt">{streamLog}</pre>
          </div>
        ) : null}
      </div>

      {pageError ? <div className="empty">错误：{pageError}</div> : null}
      {notice ? <div className="notice">{notice}</div> : null}

      {record ? (
        <>
          <div className="grid grid-4">
            <div className="panel stat">
              <div>
                <div className="stat-label">现价</div>
                <div className="stat-value">{snapshot?.current_price ?? '--'}</div>
              </div>
              <div className="stat-icon"><Gauge size={18} /></div>
            </div>
            <div className="panel stat">
              <div>
                <div className="stat-label">趋势方向</div>
                <div className="stat-value">{diagnosis?.current_trend?.direction ?? '--'}</div>
              </div>
              <div className="stat-icon info"><TrendingUp size={18} /></div>
            </div>
            <div className="panel stat">
              <div>
                <div className="stat-label">当前周期</div>
                <div className="stat-value">{diagnosis?.current_cycle ?? '--'}</div>
              </div>
              <div className="stat-icon"><CalendarClock size={18} /></div>
            </div>
            <div className="panel stat">
              <div>
                <div className="stat-label">下一周期</div>
                <div className="stat-value">{diagnosis?.next_cycle ?? '--'}</div>
              </div>
              <div className="stat-icon warn"><Activity size={18} /></div>
            </div>
          </div>

          <div className="result-grid">
            <div className="panel highlight-card">
              <div className="section-title">关键结果</div>
              <div className="row">
                <span className="badge badge-info">{decision.action ?? decision.order_type ?? 'WAIT'}</span>
                <span className="tag">置信度 {decision.confidence ?? '--'}</span>
              </div>
              <div className="metric-list" style={{ marginTop: 16 }}>
                <div><div className="label">Entry</div><div className="value">{decision.entry ?? '--'}</div></div>
                <div><div className="label">Stop</div><div className="value">{decision.stop ?? '--'}</div></div>
                <div><div className="label">Target</div><div className="value">{decision.target ?? '--'}</div></div>
                <div><div className="label">RR</div><div className="value">{decision.rr ?? '--'}</div></div>
              </div>
              {decision.reasoning ? <p className="muted" style={{ marginBottom: 0, marginTop: 16 }}>{decision.reasoning}</p> : null}
              <div className="feature-list" style={{ marginTop: 16 }}>
                <div className="feature-item">
                  <div className="label">未来走势</div>
                  <div className="value">{future.label ?? '--'}</div>
                </div>
                <div className="feature-item">
                  <div className="label">下根K线</div>
                  <div className="value">{record.stage2_decision?.next_bar_prediction?.direction ?? '--'}</div>
                </div>
              </div>
            </div>
            <div className="panel">
              <div className="section-title"><LineChart size={15} />K线与关键区</div>
              <KlineChart
                candles={(snapshot?.candles ?? []).slice(-150)}
                levels={[...supports, ...resistances]}
              />
              <div className="row">
                <span className="zone-chip support">支撑 {supports.length}</span>
                <span className="zone-chip resistance">阻力 {resistances.length}</span>
                <span className="tag">展示最近 150 根</span>
              </div>
            </div>
          </div>

          <div className="panel">
            <div className="section-title"><ListTree size={15} />结果视图</div>
            <div className="segmented">
              {VIEWS.map((item) => (
                <button key={item.key} className={view === item.key ? 'button button-primary' : 'button'} onClick={() => setView(item.key)}>
                  {item.label}
                </button>
              ))}
            </div>
            {view === 'decision' ? (
              <div className="stack">
                <div className="metric-list">
                  <div><div className="label">Action</div><div className="value">{decision.action ?? decision.order_type ?? 'WAIT'}</div></div>
                  <div><div className="label">Confidence</div><div className="value">{decision.confidence ?? '--'}</div></div>
                  <div><div className="label">Entry</div><div className="value">{decision.entry ?? '--'}</div></div>
                  <div><div className="label">Stop</div><div className="value">{decision.stop ?? '--'}</div></div>
                  <div><div className="label">Target</div><div className="value">{decision.target ?? '--'}</div></div>
                  <div><div className="label">RR</div><div className="value">{decision.rr ?? '--'}</div></div>
                </div>
                {decision.reasoning ? <p className="muted">{decision.reasoning}</p> : null}
              </div>
            ) : null}
            {view === 'tree' ? (
              <div className="decision-tree">
                <svg viewBox="0 0 520 300" role="img" aria-label="决策树">
                  {record.decision_tree_layout?.edges.map((edge) => {
                    const from = record.decision_tree_layout?.nodes.find((node) => node.id === edge.from);
                    const to = record.decision_tree_layout?.nodes.find((node) => node.id === edge.to);
                    if (!from || !to) return null;
                    return <line key={`${edge.from}-${edge.to}`} x1={from.x * 120 + 80} y1={from.y * 70 + 30} x2={to.x * 120 + 80} y2={to.y * 70 + 30} className="tree-edge" />;
                  })}
                  {record.decision_tree_layout?.nodes.map((node) => (
                    <g key={node.id} transform={`translate(${node.x * 120 + 30}, ${node.y * 70 + 10})`}>
                      <rect className="tree-node" rx="5" width="100" height="38" />
                      <text x="8" y="16" className="tree-node-label">{node.label}</text>
                      <text x="8" y="30" className="tree-node-answer">{node.answer}</text>
                    </g>
                  ))}
                </svg>
              </div>
            ) : null}
            {view === 'prompt' ? (
              <div className="stack">
                <strong>阶段一</strong>
                <pre className="raw-prompt">{JSON.stringify(record.raw_prompt.stage1, null, 2)}</pre>
                <strong>阶段二</strong>
                <pre className="raw-prompt">{JSON.stringify(record.raw_prompt.stage2, null, 2)}</pre>
              </div>
            ) : null}
            {view === 'debug' ? (
              <div className="stack">
                <strong>模型响应</strong>
                <pre className="raw-prompt">{record.stage2_response || record.stage1_response || '无模型响应'}</pre>
                <strong>异常</strong>
                <pre className="raw-prompt">{JSON.stringify(record.exception ?? {}, null, 2)}</pre>
              </div>
            ) : null}
          </div>

          <div className="grid grid-2">
            <div className="panel">
              <div className="section-title"><Network size={15} />分析后追问</div>
              <div className="upload-panel">
                <div className="field">
                  <label htmlFor="ai-question">追问内容</label>
                  <textarea id="ai-question" className="followup-input" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="例如：当前方案在哪里失效？" />
                </div>
                <div className="row" style={{ marginTop: 14 }}>
                  <button className="button" onClick={() => followup.mutate()} disabled={followup.isPending || !question}>
                    <Send size={14} />
                    发送追问
                  </button>
                  <span className="muted">追问将复用当前模型配置。</span>
                </div>
              </div>
              {followup.data ? <pre className="raw-prompt">{followup.data.answer}</pre> : null}
            </div>
            <div className="panel">
              <div className="section-title"><Bell size={15} />飞书通知</div>
              <div className="upload-panel">
                <div className="form-grid">
                  <div className="field">
                    <label htmlFor="feishu-webhook">Webhook URL</label>
                    <input id="feishu-webhook" value={webhookUrl} onChange={(event) => setWebhookUrl(event.target.value)} />
                  </div>
                  <div className="field">
                    <label htmlFor="feishu-secret">签名 Secret</label>
                    <input id="feishu-secret" value={feishuSecret} onChange={(event) => setFeishuSecret(event.target.value)} type="password" />
                  </div>
                </div>
                <div className="row" style={{ marginTop: 14 }}>
                  <label className="checkbox-row">
                    <input type="checkbox" checked={notifyOnlyOrder} onChange={(event) => setNotifyOnlyOrder(event.target.checked)} />
                    仅交易信号时通知
                  </label>
                  <button className="button" onClick={() => notify.mutate()} disabled={notify.isPending}>
                    <Bell size={14} />
                    {notify.isPending ? '发送中...' : '发送通知'}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </>
      ) : (
        <div className="empty">选择数据集后开始分析，结果会在这里展示。</div>
      )}
    </div>
  );
}
