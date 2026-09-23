/** 事件流面板里各类流式事件的显示标签。 */
export const STREAM_LOG_LABELS: Record<string, string> = {
  log: '系统',
  stage1: '阶段一正文',
  stage1_reasoning: '阶段一推理',
  stage2: '阶段二正文',
  stage2_reasoning: '阶段二推理',
};

/**
 * 逐分片下发的事件类型：模型流式输出会把同一段文本拆成很多小分片，
 * 这些分片必须拼在同一行，否则每个分片都会被重复打上标签。
 */
const STREAMED_EVENT_TYPES = new Set([
  'stage1',
  'stage1_reasoning',
  'stage2',
  'stage2_reasoning',
]);

function joinLine(current: string, line: string) {
  if (!current) return line;
  return current.endsWith('\n') ? `${current}${line}` : `${current}\n${line}`;
}

/**
 * 把一条事件流消息追加到日志文本里。
 *
 * @param continues 本次事件是否与上一条事件属于同一段流式输出。
 */
export function appendStreamLogEvent(
  current: string,
  eventType: string,
  text: string,
  continues: boolean,
): string {
  if (!text) return current;
  const label = STREAM_LOG_LABELS[eventType] ?? eventType;
  // 系统日志一类的一次性事件独占一行，并结束当前正在续写的分段。
  if (!STREAMED_EVENT_TYPES.has(eventType)) {
    return `${joinLine(current, `[${label}] ${text}`)}\n`;
  }
  // 同一段的后续分片直接续写，只有分段切换时才重新打标签。
  if (continues) return `${current}${text}`;
  return joinLine(current, `[${label}] ${text}`);
}
