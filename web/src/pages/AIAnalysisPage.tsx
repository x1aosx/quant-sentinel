import { useMutation, useQuery } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { Bot, Download, Network, Send, Sparkles } from 'lucide-react';
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
      </div>

      <div className="grid grid-2">
        <div className="panel">
          <div className="section-title">
            <Download size={15} />
            网络数据导入
          </div>
          <div className="row">
            <select value={source} onChange={(event) => setSource(event.target.value as 'yfinance' | 'akshare')}>
              <option value="yfinance">YFinance</option>
              <option value="akshare">AkShare/A股</option>
            </select>
            <input value={symbol} onChange={(event) => setSymbol(event.target.value)} placeholder="GC=F 或 600519" />
            <select value={timeframe} onChange={(event) => setTimeframe(event.target.value)}>
              {['1m', '5m', '15m', '30m', '1h', '1d', '1w'].map((item) => (
                <option key={item} value={item}>{item}</option>
              ))}
            </select>
            <button className="button" onClick={() => importRemote.mutate()} disabled={importRemote.isPending}>
              {importRemote.isPending ? '下载中...' : '下载行情'}
            </button>
          </div>
        </div>

        <div className="panel">
          <div className="section-title">
            <Bot size={15} />
            大模型配置
          </div>
          <div className="row">
            <input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} placeholder="Base URL" />
            <input value={model} onChange={(event) => setModel(event.target.value)} placeholder="模型名" />
            <input value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="API Key（仅本次会话）" type="password" />
            <label className="muted">思考</label>
            <input type="checkbox" checked={thinking} onChange={(event) => setThinking(event.target.checked)} />
          </div>
          <div className="row">
            <input value={barCount} onChange={(event) => setBarCount(event.target.value)} placeholder="分析K线数" />
            <select value={stance} onChange={(event) => setStance(event.target.value)}>
              <option value="conservative">保守</option>
              <option value="balanced">均衡</option>
              <option value="aggressive">积极</option>
            </select>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="section-title">
          <Sparkles size={15} />
          分析执行
        </div>
        <div className="row">
          <select value={datasetId} onChange={(event) => setDatasetId(event.target.value)}>
            {datasets.length === 0 ? <option value="">暂无数据集</option> : null}
            {datasets.map((item) => (
              <option key={item.id} value={item.id}>
                {item.symbol} · {item.timeframe} · {item.bar_count} 根
              </option>
            ))}
          </select>
          <button className="button button-primary" onClick={() => void runAnalysis()} disabled={analysis.isPending || !datasetId}>
            {analysis.isPending ? '分析中...' : '开始两阶段分析'}
          </button>
          <span className="muted">未配置 API Key 时使用本地确定性研究模式。</span>
        </div>
        {streamLog ? <pre className="raw-prompt">{streamLog}</pre> : null}
      </div>

      {pageError ? <div className="empty">错误：{pageError}</div> : null}
      {notice ? <div className="notice">{notice}</div> : null}

      {record ? (
        <>
          <div className="grid grid-4">
            <div className="panel stat"><div><div className="stat-label">现价</div><div className="stat-value">{snapshot?.current_price ?? '--'}</div></div></div>
            <div className="panel stat"><div><div className="stat-label">趋势方向</div><div className="stat-value">{diagnosis?.current_trend?.direction ?? '--'}</div></div></div>
            <div className="panel stat"><div><div className="stat-label">当前周期</div><div className="stat-value">{diagnosis?.current_cycle ?? '--'}</div></div></div>
            <div className="panel stat"><div><div className="stat-label">下一周期</div><div className="stat-value">{diagnosis?.next_cycle ?? '--'}</div></div></div>
          </div>

          <div className="panel">
            <div className="section-title">K线与关键区</div>
            <KlineChart
              candles={(snapshot?.candles ?? []).slice(-150)}
              levels={[...supports, ...resistances]}
            />
          </div>

          <div className="panel">
            <div className="section-title">结果视图</div>
            <div className="row">
              {VIEWS.map((item) => (
                <button key={item.key} className={view === item.key ? 'button button-primary' : 'button'} onClick={() => setView(item.key)}>
                  {item.label}
                </button>
              ))}
            </div>
            {view === 'decision' ? (
              <div className="stack">
                <div className="row">
                  <span className="badge badge-info">{decision.action ?? decision.order_type ?? 'WAIT'}</span>
                  <span className="tag">置信度 {decision.confidence ?? '--'}</span>
                </div>
                <div className="row">
                  <span className="muted">entry {decision.entry ?? '--'}</span>
                  <span className="muted">stop {decision.stop ?? '--'}</span>
                  <span className="muted">target {decision.target ?? '--'}</span>
                  <span className="muted">rr {decision.rr ?? '--'}</span>
                </div>
                <div>{decision.reasoning}</div>
                <div className="grid grid-2">
                  <div><strong>未来走势</strong><div className="muted">{future.label ?? '--'}</div></div>
                  <div><strong>下根K线</strong><div className="muted">{record.stage2_decision?.next_bar_prediction?.direction ?? '--'}</div></div>
                </div>
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
              <textarea className="followup-input" value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="例如：当前方案在哪里失效？" />
              <button className="button" onClick={() => followup.mutate()} disabled={followup.isPending || !question}>
                <Send size={14} />
                发送追问
              </button>
              {followup.data ? <pre className="raw-prompt">{followup.data.answer}</pre> : null}
            </div>
            <div className="panel">
              <div className="section-title">飞书通知</div>
              <div className="row">
                <input value={webhookUrl} onChange={(event) => setWebhookUrl(event.target.value)} placeholder="Webhook URL" />
                <input value={feishuSecret} onChange={(event) => setFeishuSecret(event.target.value)} placeholder="签名 Secret" type="password" />
                <label className="muted">仅交易</label>
                <input type="checkbox" checked={notifyOnlyOrder} onChange={(event) => setNotifyOnlyOrder(event.target.checked)} />
                <button className="button" onClick={() => notify.mutate()} disabled={notify.isPending}>发送通知</button>
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
