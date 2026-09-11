import { useEffect, useMemo, useState } from 'react';
import { Pause, Play, RotateCcw, ZoomIn, ZoomOut } from 'lucide-react';
import type { AIAnalysisRecord } from '../../types';

interface Props {
  record?: AIAnalysisRecord | null;
}

export function DecisionVisualization({ record }: Props) {
  const layout = record?.decision_tree_layout;
  const [zoom, setZoom] = useState(1);
  const [activeIndex, setActiveIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [selectedId, setSelectedId] = useState<string>('');
  const nodes = layout?.nodes ?? [];
  const edges = layout?.edges ?? [];

  useEffect(() => {
    setActiveIndex(0);
    setPlaying(false);
    setSelectedId(nodes[0]?.id ?? '');
  }, [record?.id, nodes.length]);

  useEffect(() => {
    if (!playing || nodes.length === 0) return;
    const timer = window.setInterval(() => {
      setActiveIndex((current) => {
        if (current >= nodes.length - 1) {
          setPlaying(false);
          return current;
        }
        const next = current + 1;
        setSelectedId(nodes[next].id);
        return next;
      });
    }, 900);
    return () => window.clearInterval(timer);
  }, [nodes, playing]);

  const dimensions = useMemo(() => {
    const maxX = Math.max(5, ...nodes.map((node) => node.x));
    const maxY = Math.max(4, ...nodes.map((node) => node.y));
    return {
      width: Math.max(760, maxX * 150 + 160),
      height: Math.max(360, maxY * 120 + 100),
    };
  }, [nodes]);

  if (!layout || nodes.length === 0) {
    return <div className="empty">当前分析没有可用的决策路径。</div>;
  }

  const selected = nodes.find((node) => node.id === selectedId) ?? nodes[0];
  const nodeMap = new Map(nodes.map((node) => [node.id, node]));
  const tone = (phase?: string, status?: string) => {
    if (status === 'terminal' || phase === 'terminal') return 'terminal';
    if (phase === 'gate') return 'gate';
    if (phase === 'diagnosis') return 'diagnosis';
    return 'decision';
  };

  return (
    <div className="flow-viz">
      <div className="flow-toolbar">
        <button className="button button-primary" onClick={() => setPlaying((value) => !value)}>
          {playing ? <Pause size={14} /> : <Play size={14} />}
          {playing ? '暂停回放' : '自动回放'}
        </button>
        <button
          className="button"
          onClick={() => {
            const next = Math.min(nodes.length - 1, activeIndex + 1);
            setActiveIndex(next);
            setSelectedId(nodes[next].id);
          }}
          disabled={activeIndex >= nodes.length - 1}
        >
          下一步
        </button>
        <button className="button" onClick={() => setZoom((value) => Math.max(0.6, value - 0.15))}>
          <ZoomOut size={14} />
        </button>
        <button className="button" onClick={() => setZoom((value) => Math.min(1.8, value + 0.15))}>
          <ZoomIn size={14} />
        </button>
        <button
          className="button"
          onClick={() => {
            setZoom(1);
            setActiveIndex(0);
            setSelectedId(nodes[0].id);
            setPlaying(false);
          }}
        >
          <RotateCcw size={14} />
          重置
        </button>
        <span className="tag">
          步骤 {activeIndex + 1}/{nodes.length}
        </span>
      </div>

      <div className="flow-canvas">
        <svg
          viewBox={`0 0 ${dimensions.width} ${dimensions.height}`}
          style={{ transform: `scale(${zoom})`, transformOrigin: 'top left' }}
          role="img"
          aria-label="决策路径可视化"
        >
          <g>
            {edges.map((edge) => {
              const from = nodeMap.get(edge.from);
              const to = nodeMap.get(edge.to);
              if (!from || !to) return null;
              const x1 = from.x * 150 + 110;
              const y1 = from.y * 120 + 45;
              const x2 = to.x * 150 + 20;
              const y2 = to.y * 120 + 45;
              const active = nodes.indexOf(from) <= activeIndex && nodes.indexOf(to) <= activeIndex;
              const mid = (x1 + x2) / 2;
              return (
                <path
                  key={`${edge.from}-${edge.to}`}
                  d={`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`}
                  className={`flow-edge${active ? ' active' : ''}`}
                />
              );
            })}
            {nodes.map((node, index) => {
              const x = node.x * 150;
              const y = node.y * 120;
              return (
                <g
                  key={node.id}
                  transform={`translate(${x}, ${y})`}
                  className={`flow-node ${tone(node.phase, node.status)}${
                    index === activeIndex ? ' active' : ''
                  }${node.id === selected.id ? ' selected' : ''}`}
                  onClick={() => {
                    setSelectedId(node.id);
                    setActiveIndex(index);
                  }}
                  role="button"
                  tabIndex={0}
                >
                  <rect width="140" height="88" rx="7" />
                  <text x="12" y="22" className="flow-node-kicker">
                    {node.phase ?? 'step'}
                  </text>
                  <text x="12" y="43" className="flow-node-title">
                    {node.label}
                  </text>
                  <text x="12" y="63" className="flow-node-question">
                    {node.question}
                  </text>
                  <text x="12" y="79" className="flow-node-answer">
                    {node.answer}
                  </text>
                </g>
              );
            })}
          </g>
        </svg>
      </div>

      <div className="flow-detail">
        <div>
          <div className="label">当前节点</div>
          <strong>{selected.label}</strong>
        </div>
        <div>
          <div className="label">判断</div>
          <span>{selected.question}</span>
        </div>
        <div>
          <div className="label">结果</div>
          <span>{selected.answer}</span>
        </div>
        {selected.reasoning ? (
          <div className="flow-reasoning">
            <div className="label">说明</div>
            <p>{selected.reasoning}</p>
          </div>
        ) : null}
      </div>
    </div>
  );
}
