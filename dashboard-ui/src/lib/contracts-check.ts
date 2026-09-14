/**
 * Compile-time contract validation for the dashboard state API.
 *
 * Every fixture under `contract-fixtures/` is generated from the live
 * backend by `tests/integration/test_dashboard_state_contracts.py`. The
 * `satisfies` checks below make svelte-check (run by
 * `scripts/build_dashboard_ui.py --check` in CI) fail when a fixture
 * changes shape without the matching update to `$lib/contracts`.
 *
 * Only the byte-pinned stable sections have fixtures; volatile sections
 * (models, routing, pricing, providers, inventory, inventory-keys) are
 * guarded by backend-side `.shape.json` key-path signatures instead.
 *
 * This module is intentionally imported by no route — it exists only for
 * type-checking and is never bundled.
 */
import type {
  AnalyticsState,
  BudgetsState,
  CombosState,
  KeysState,
  LeaderboardState,
  OverviewState,
  RequestLogsState,
  SaversState,
  SectionEnvelope,
  SettingsState,
  ToolsState,
  UsageState
} from '$lib/contracts';
import type { JsonObject } from '$lib/types';
import analyticsFixture from '$lib/contract-fixtures/analytics.json';
import budgetsFixture from '$lib/contract-fixtures/budgets.json';
import combosFixture from '$lib/contract-fixtures/combos.json';
import keysFixture from '$lib/contract-fixtures/keys.json';
import leaderboardFixture from '$lib/contract-fixtures/leaderboard.json';
import overviewFixture from '$lib/contract-fixtures/overview.json';
import requestLogsFixture from '$lib/contract-fixtures/request-logs.json';
import saversFixture from '$lib/contract-fixtures/savers.json';
import settingsFixture from '$lib/contract-fixtures/settings.json';
import toolsFixture from '$lib/contract-fixtures/tools.json';
import usageFixture from '$lib/contract-fixtures/usage.json';

const analytics = analyticsFixture satisfies SectionEnvelope<AnalyticsState>;
const budgets = budgetsFixture satisfies SectionEnvelope<BudgetsState>;
const combos = combosFixture satisfies SectionEnvelope<CombosState>;
const keys = keysFixture satisfies SectionEnvelope<KeysState>;
const leaderboard = leaderboardFixture satisfies SectionEnvelope<LeaderboardState>;
const overview = overviewFixture satisfies SectionEnvelope<OverviewState>;
const requestLogs = requestLogsFixture satisfies SectionEnvelope<RequestLogsState>;
const savers = saversFixture satisfies SectionEnvelope<SaversState>;
const settings = settingsFixture satisfies SectionEnvelope<SettingsState>;
const tools = toolsFixture satisfies SectionEnvelope<ToolsState>;
const usage = usageFixture satisfies SectionEnvelope<UsageState>;

export const contractChecks: Record<string, SectionEnvelope<JsonObject>> = {
  analytics,
  budgets,
  combos,
  keys,
  leaderboard,
  overview,
  'request-logs': requestLogs,
  savers,
  settings,
  tools,
  usage
};
