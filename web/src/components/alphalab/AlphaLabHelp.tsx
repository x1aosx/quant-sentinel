import { useState, type ReactNode } from 'react';
import { ChevronDown, HelpCircle } from 'lucide-react';

export interface AlphaLabHelpItem {
  heading: string;
  body: ReactNode;
}

/**
 * 板块说明面板：用中文解释「这块内容怎么看」，默认收起，避免干扰数据阅读。
 */
export function AlphaLabHelp({
  title = '怎么看这块内容',
  items,
  defaultOpen = false,
}: {
  title?: string;
  items: AlphaLabHelpItem[];
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  if (!items.length) return null;
  return (
    <div className="panel alphalab-help">
      <div className="alphalab-help-header">
        <span className="section-title-main">
          <HelpCircle size={15} />
          {title}
        </span>
        <button
          type="button"
          className="button alphalab-help-toggle"
          onClick={() => setOpen((current) => !current)}
          aria-expanded={open}
        >
          {open ? '收起' : '展开'}
          <ChevronDown
            size={14}
            className={open ? 'alphalab-help-chevron open' : 'alphalab-help-chevron'}
          />
        </button>
      </div>
      {open ? (
        <div className="alphalab-help-body">
          {items.map((item) => (
            <div className="alphalab-help-item" key={item.heading}>
              <div className="alphalab-help-item-heading">{item.heading}</div>
              <div className="alphalab-help-item-body">{item.body}</div>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/**
 * 中文标题 + 英文补充：中文为主标题，英文/技术标识作为次级信息展示。
 */
export function AlphaLabLabel({ zh, en }: { zh: string; en?: string }) {
  return (
    <span className="alphalab-label">
      <span className="alphalab-label-zh">{zh}</span>
      {en ? <span className="alphalab-label-en">{en}</span> : null}
    </span>
  );
}

/** 一行说明文字，用于图表或表格下方的一句话解释。 */
export function AlphaLabNote({ children }: { children: ReactNode }) {
  return <div className="alphalab-chart-note">{children}</div>;
}
