import type {
  IntelligenceFeedConfig,
  IntelligenceSourceType,
} from '../types';

type SourceField =
  | 'id'
  | 'url'
  | 'source'
  | 'source_type'
  | 'language'
  | 'enabled';

export interface IntelligenceSourceImportResult {
  feeds: IntelligenceFeedConfig[];
  invalidRows: number;
  errors: string[];
}

export interface IntelligenceSourceMergeResult {
  feeds: IntelligenceFeedConfig[];
  added: number;
  duplicates: number;
}

const HEADERS: SourceField[] = [
  'id',
  'url',
  'source',
  'source_type',
  'language',
  'enabled',
];

const TEMPLATE_HEADERS = [
  '编号',
  '来源名称',
  '订阅地址',
  '类型',
  '语言',
  '启用',
];

const SOURCE_TYPE_ALIASES: Record<string, IntelligenceSourceType> = {
  NEWS: 'NEWS',
  POLICY: 'POLICY',
  ANNOUNCEMENT: 'ANNOUNCEMENT',
  新闻: 'NEWS',
  政策: 'POLICY',
  公告: 'ANNOUNCEMENT',
};

const HEADER_ALIASES: Record<SourceField, string[]> = {
  id: ['id', '编号', '标识', '来源id'],
  url: ['url', 'rssatomurl', 'rssurl', 'feedurl', '地址', '链接', '订阅地址'],
  source: ['source', 'sourcename', 'name', '来源', '来源名称', '名称'],
  source_type: ['sourcetype', 'type', '类型', '来源类型'],
  language: ['language', 'lang', '语言'],
  enabled: ['enabled', 'enable', '启用', '是否启用'],
};

export function exportIntelligenceSourcesCsv(
  feeds: IntelligenceFeedConfig[],
): string {
  const rows = [
    HEADERS,
    ...feeds.map((feed) => [
      feed.id,
      feed.url,
      feed.source,
      feed.source_type,
      feed.language,
      feed.enabled ? 'true' : 'false',
    ]),
  ];
  return rows.map((row) => row.map(csvCell).join(',')).join('\r\n');
}

export function downloadIntelligenceSourcesCsv(
  feeds: IntelligenceFeedConfig[],
): void {
  const content = exportIntelligenceSourcesCsv(feeds);
  const timestamp = new Date()
    .toISOString()
    .replace(/[-:]/g, '')
    .replace('T', '-')
    .slice(0, 15);
  downloadTextFile(
    `intelligence-sources-${timestamp}.csv`,
    content,
    'text/csv;charset=utf-8',
  );
}

export function downloadIntelligenceSourcesTemplate(): void {
  downloadTextFile(
    'intelligence-sources-template.csv',
    TEMPLATE_HEADERS.map(csvCell).join(','),
    'text/csv;charset=utf-8',
  );
}

export function parseIntelligenceSourcesCsv(
  text: string,
): IntelligenceSourceImportResult {
  const normalized = text.replace(/^\uFEFF/, '').trim();
  if (!normalized) {
    return { feeds: [], invalidRows: 0, errors: ['文件为空。'] };
  }

  const rows = parseCsv(normalized, detectDelimiter(normalized));
  if (rows.length < 2) {
    return {
      feeds: [],
      invalidRows: 0,
      errors: ['CSV 文件缺少数据行。'],
    };
  }

  const headerIndexes = mapHeaders(rows[0]);
  const missingHeaders = (['url', 'source'] as SourceField[]).filter(
    (field) => headerIndexes[field] === undefined,
  );
  if (missingHeaders.length) {
    return {
      feeds: [],
      invalidRows: rows.length - 1,
      errors: [`CSV 缺少必要列：${missingHeaders.join(', ')}。`],
    };
  }

  const feeds: IntelligenceFeedConfig[] = [];
  const errors: string[] = [];
  let invalidRows = 0;

  rows.slice(1).forEach((row, index) => {
    if (row.every((value) => !value.trim())) return;

    const rowNumber = index + 2;
    const url = cell(row, headerIndexes.url).trim();
    const source = cell(row, headerIndexes.source).trim();
    if (!isHttpUrl(url)) {
      invalidRows += 1;
      errors.push(`第 ${rowNumber} 行的 URL 无效。`);
      return;
    }
    if (!source) {
      invalidRows += 1;
      errors.push(`第 ${rowNumber} 行缺少来源名称。`);
      return;
    }

    const sourceTypeValue = cell(row, headerIndexes.source_type).trim();
    const sourceType = normalizeSourceType(sourceTypeValue);
    if (!sourceType) {
      invalidRows += 1;
      errors.push(`第 ${rowNumber} 行的类型无效。`);
      return;
    }

    const enabledValue = cell(row, headerIndexes.enabled).trim();
    const enabled = parseEnabled(enabledValue);
    if (enabled === undefined) {
      invalidRows += 1;
      errors.push(`第 ${rowNumber} 行的启用值无效。`);
      return;
    }

    const id = cell(row, headerIndexes.id).trim();
    feeds.push({
      id: id || `feed-${Date.now()}-${index}`,
      url,
      source,
      source_type: sourceType,
      language: cell(row, headerIndexes.language).trim() || 'zh-CN',
      enabled,
    });
  });

  return { feeds, invalidRows, errors };
}

export function mergeIntelligenceSources(
  existing: IntelligenceFeedConfig[],
  incoming: IntelligenceFeedConfig[],
): IntelligenceSourceMergeResult {
  const feeds = [...existing];
  const knownUrls = new Set(existing.map((feed) => normalizeUrl(feed.url)));
  const knownIds = new Set(existing.map((feed) => feed.id));
  let added = 0;
  let duplicates = 0;

  incoming.forEach((feed) => {
    const urlKey = normalizeUrl(feed.url);
    if (knownUrls.has(urlKey)) {
      duplicates += 1;
      return;
    }

    const next = {
      ...feed,
      id: uniqueId(feed.id, knownIds),
      url: feed.url.trim(),
      source: feed.source.trim(),
    };
    feeds.push(next);
    knownUrls.add(urlKey);
    knownIds.add(next.id);
    added += 1;
  });

  return { feeds, added, duplicates };
}

function parseCsv(text: string, delimiter: string): string[][] {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = '';
  let quoted = false;

  for (let index = 0; index < text.length; index += 1) {
    const character = text[index];
    if (quoted) {
      if (character === '"') {
        if (text[index + 1] === '"') {
          field += '"';
          index += 1;
        } else {
          quoted = false;
        }
      } else {
        field += character;
      }
      continue;
    }

    if (character === '"' && field.length === 0) {
      quoted = true;
    } else if (character === delimiter) {
      row.push(field);
      field = '';
    } else if (character === '\n') {
      row.push(field);
      rows.push(row);
      row = [];
      field = '';
    } else if (character !== '\r') {
      field += character;
    }
  }

  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

function detectDelimiter(text: string): string {
  const firstLine = text.split(/\r?\n/, 1)[0] ?? '';
  const delimiters = [',', ';', '\t'];
  let selected = ',';
  let selectedCount = -1;

  delimiters.forEach((delimiter) => {
    let count = 0;
    let quoted = false;
    for (let index = 0; index < firstLine.length; index += 1) {
      const character = firstLine[index];
      if (character === '"') {
        if (quoted && firstLine[index + 1] === '"') {
          index += 1;
        } else {
          quoted = !quoted;
        }
      } else if (!quoted && character === delimiter) {
        count += 1;
      }
    }
    if (count > selectedCount) {
      selected = delimiter;
      selectedCount = count;
    }
  });

  return selected;
}

function mapHeaders(
  values: string[],
): Partial<Record<SourceField, number>> {
  const indexes: Partial<Record<SourceField, number>> = {};
  values.forEach((value, index) => {
    const normalized = normalizeHeader(value);
    const field = (Object.keys(HEADER_ALIASES) as SourceField[]).find((key) =>
      HEADER_ALIASES[key].includes(normalized),
    );
    if (field && indexes[field] === undefined) {
      indexes[field] = index;
    }
  });
  return indexes;
}

function normalizeHeader(value: string): string {
  return value
    .replace(/^\uFEFF/, '')
    .trim()
    .toLowerCase()
    .replace(/[\s_/\\-]+/g, '');
}

function normalizeSourceType(value: string): IntelligenceSourceType | null {
  if (!value) return 'NEWS';
  return SOURCE_TYPE_ALIASES[value.toUpperCase()] ?? SOURCE_TYPE_ALIASES[value] ?? null;
}

function parseEnabled(value: string): boolean | undefined {
  if (!value) return true;
  const normalized = value.trim().toLowerCase();
  if (['true', '1', 'yes', 'y', '是', '启用', '开启'].includes(normalized)) {
    return true;
  }
  if (['false', '0', 'no', 'n', '否', '停用', '关闭'].includes(normalized)) {
    return false;
  }
  return undefined;
}

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === 'http:' || url.protocol === 'https:';
  } catch {
    return false;
  }
}

function normalizeUrl(value: string): string {
  return value.trim().toLowerCase();
}

function uniqueId(value: string, knownIds: Set<string>): string {
  const base = value.trim() || `feed-${Date.now()}`;
  if (!knownIds.has(base)) return base;
  let suffix = 2;
  while (knownIds.has(`${base}-${suffix}`)) {
    suffix += 1;
  }
  return `${base}-${suffix}`;
}

function cell(
  row: string[],
  index: number | undefined,
): string {
  if (index === undefined) return '';
  return row[index] ?? '';
}

function csvCell(value: string): string {
  return `"${String(value).replace(/"/g, '""')}"`;
}

function downloadTextFile(
  filename: string,
  content: string,
  contentType: string,
): void {
  const blob = new Blob(['\uFEFF', content], { type: contentType });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}
