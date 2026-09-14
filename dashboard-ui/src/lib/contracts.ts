import type { AlertItem, JsonObject, JsonValue } from './types';

/**
 * Typed contracts for the byte-pinned `/dashboard/api/v2/state/*` sections.
 *
 * These types are the frontend half of the dashboard state contract. The
 * backend half lives in `tests/integration/test_dashboard_state_contracts.py`,
 * which pins the exact payload shapes of the stable sections into the JSON
 * fixtures under `contract-fixtures/` and fails CI when a backend field
 * drifts. The check file `contracts-check.ts` validates those same fixtures
 * against these types, so a fixture regenerated without updating a consumer
 * here also fails CI (`svelte-check` via `scripts/build_dashboard_ui.py
 * --check`). Volatile sections (models, routing, pricing, providers,
 * inventory, inventory-keys) are pinned backend-side by key-path shape
 * signatures instead and intentionally have no fixture types here.
 *
 * SQLite booleans arrive as 0/1 numbers; timestamps are strings.
 */

export interface SectionEnvelope<D extends JsonObject> {
  section: string;
  alerts: AlertItem[];
  data: D;
  meta: JsonObject;
}

export interface UsageDailyPoint extends JsonObject {
  date?: string;
  cost?: number;
  requests?: number;
}

export interface UsageStats extends JsonObject {
  total_cost: number;
  total_requests: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_creation_tokens: number;
  total_cache_read_tokens: number;
  daily: UsageDailyPoint[];
  by_model: { model: string; requests: number; input_tokens: number; output_tokens: number }[];
  period_days: number;
  reporting_timezone: string;
}

export interface UsageState extends JsonObject {
  stats: UsageStats;
}

export interface OverviewState extends JsonObject {
  stats: UsageStats;
  provider_count: number;
  combos: Record<string, string[]>;
  today_cost: number;
  reporting_timezone: string;
  global_budget: BudgetStatus | null;
  base_url: string;
  live: { type: string; seq: number; inflight: number; recent: JsonValue[] };
  cooldown_count: number;
  setup_checklist: { has_providers: boolean; has_keys: boolean; has_requests: boolean };
}

export interface AnalyticsBreakdownRow extends JsonObject {
  model?: string;
  provider?: string;
  account?: string;
  client_key?: string;
  requests: number;
  input_tokens: number;
  output_tokens: number;
  cache_creation_tokens: number;
  cache_read_tokens: number;
  cost: number;
}

export interface SuccessBreakdown extends JsonObject {
  success_2xx: number;
  client_4xx: number;
  server_5xx: number;
  total: number;
}

export interface AnalyticsState extends JsonObject {
  summary: Omit<UsageStats, 'by_model' | 'period_days' | 'reporting_timezone'>;
  breakdown: AnalyticsBreakdownRow[];
  success: SuccessBreakdown;
}

export interface LeaderboardRow extends JsonObject {
  rank: number;
  key_name: string | null;
  requests: number;
  tokens: number;
  input_tokens: number;
  output_tokens: number;
  cost: number;
  success_pct: number;
}

export interface LeaderboardState extends JsonObject {
  leaderboard: LeaderboardRow[];
}

export interface RequestLogRow extends JsonObject {
  id: number;
  timestamp: string;
  client_format: string | null;
  model: string | null;
  provider_id: string | null;
  account_id: string | null;
  status: number;
  duration_ms: number;
  streamed: number;
  error: string | null;
  client_key_id: number | null;
  client_key_label: string | null;
  client_key_name: string | null;
}

export interface RequestLogsState extends JsonObject {
  logs: RequestLogRow[];
  logging_enabled: boolean;
  retention_max: number;
}

export interface ComboRow extends JsonObject {
  id: number;
  name: string;
  models: string;
  models_list: string[];
  created_at: string;
  updated_at: string;
}

export interface WiredProvider extends JsonObject {
  prefix: string;
  models: string[];
  accounts: number;
}

export interface CombosState extends JsonObject {
  combos: ComboRow[];
  wired_providers: WiredProvider[];
}

export interface BudgetStatus extends JsonObject {
  daily_limit: number | null;
  today_spend: number;
  remaining: number | null;
  pct_used: number;
  status: string;
  warn_pct: number;
  reporting_timezone: string;
  retry_after: number;
  resets_at: string | null;
}

export interface BudgetRow extends JsonObject {
  id: number;
  key_id: number | null;
  daily_limit: number;
  warn_pct: number;
  is_active: number;
  created_at: string;
  status: BudgetStatus;
  key_name: string | null;
}

export interface BudgetsState extends JsonObject {
  budgets: BudgetRow[];
  keys: { id: number; name: string; prefix: string; is_active: number; can_login: boolean }[];
  reporting_timezone: string;
}

export interface DashboardKeyRow extends JsonObject {
  id: number;
  name: string;
  prefix: string;
  is_active: number;
  can_login: boolean;
  allowed_models: string[] | null;
  created_at: string;
  budget: BudgetStatus | null;
}

export interface KeysState extends JsonObject {
  keys: DashboardKeyRow[];
  status: string;
  counts: { active: number; revoked: number; all: number };
}

export interface SaversState extends JsonObject {
  settings: Record<string, string>;
  saver_stats: JsonObject;
}

export interface ToolsState extends JsonObject {
  base_url: string;
  require_api_key: boolean;
}

export interface SettingsState extends JsonObject {
  values: Record<string, string>;
  dashboard_access: { mode: string; keys_url: string; localhost_requires_auth: boolean };
  status: Record<string, string | number | boolean>;
  export: JsonObject;
}
