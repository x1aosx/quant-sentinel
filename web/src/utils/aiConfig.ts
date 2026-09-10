export interface AIFormConfig {
  baseUrl: string;
  model: string;
  apiKey: string;
  thinking: boolean;
  analysisBarCount: string;
  decisionStance: string;
  rememberApiKey: boolean;
}

export interface SavedAIConfig extends AIFormConfig {
  savedAt: string | null;
}

const STORAGE_KEY = 'xquant.ai-config.v1';

export const DEFAULT_AI_CONFIG: SavedAIConfig = {
  baseUrl: 'https://api.deepseek.com/v1',
  model: 'deepseek-chat',
  apiKey: '',
  thinking: false,
  analysisBarCount: '120',
  decisionStance: 'balanced',
  rememberApiKey: false,
  savedAt: null,
};

export function loadAIConfig(): SavedAIConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return DEFAULT_AI_CONFIG;

    const value = JSON.parse(raw) as Record<string, unknown>;
    return {
      baseUrl: typeof value.baseUrl === 'string' && value.baseUrl ? value.baseUrl : DEFAULT_AI_CONFIG.baseUrl,
      model: typeof value.model === 'string' && value.model ? value.model : DEFAULT_AI_CONFIG.model,
      apiKey: value.rememberApiKey && typeof value.apiKey === 'string' ? value.apiKey : '',
      thinking: value.thinking === true,
      analysisBarCount: typeof value.analysisBarCount === 'string' ? value.analysisBarCount : DEFAULT_AI_CONFIG.analysisBarCount,
      decisionStance: typeof value.decisionStance === 'string' ? value.decisionStance : DEFAULT_AI_CONFIG.decisionStance,
      rememberApiKey: value.rememberApiKey === true,
      savedAt: typeof value.savedAt === 'string' ? value.savedAt : null,
    };
  } catch {
    return DEFAULT_AI_CONFIG;
  }
}

export function saveAIConfig(config: AIFormConfig): SavedAIConfig {
  const saved: SavedAIConfig = {
    ...config,
    apiKey: config.rememberApiKey ? config.apiKey : '',
    savedAt: new Date().toISOString(),
  };
  localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
  return saved;
}

export function clearAIConfig(): SavedAIConfig {
  localStorage.removeItem(STORAGE_KEY);
  return DEFAULT_AI_CONFIG;
}

export function isAIConfigDirty(current: AIFormConfig, saved: SavedAIConfig): boolean {
  return (
    current.baseUrl !== saved.baseUrl ||
    current.model !== saved.model ||
    current.apiKey !== saved.apiKey ||
    current.thinking !== saved.thinking ||
    current.analysisBarCount !== saved.analysisBarCount ||
    current.decisionStance !== saved.decisionStance ||
    current.rememberApiKey !== saved.rememberApiKey
  );
}
