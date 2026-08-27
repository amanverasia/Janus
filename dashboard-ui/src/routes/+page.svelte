<script lang="ts">
  import { onMount } from 'svelte';
  import '../app.css';
  import AlertStrip from '$lib/components/AlertStrip.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import PageSkeleton from '$lib/components/PageSkeleton.svelte';
  import Shell from '$lib/components/Shell.svelte';
  import Toasts from '$lib/components/Toasts.svelte';
  import AnalyticsPage from '$lib/pages/AnalyticsPage.svelte';
  import BudgetsPage from '$lib/pages/BudgetsPage.svelte';
  import CombosPage from '$lib/pages/CombosPage.svelte';
  import InventoryAddPage from '$lib/pages/InventoryAddPage.svelte';
  import InventoryImportPage from '$lib/pages/InventoryImportPage.svelte';
  import InventoryKeysPage from '$lib/pages/InventoryKeysPage.svelte';
  import InventoryOverviewPage from '$lib/pages/InventoryOverviewPage.svelte';
  import KeysPage from '$lib/pages/KeysPage.svelte';
  import LeaderboardPage from '$lib/pages/LeaderboardPage.svelte';
  import ModelsPage from '$lib/pages/ModelsPage.svelte';
  import OverviewPage from '$lib/pages/OverviewPage.svelte';
  import PricingPage from '$lib/pages/PricingPage.svelte';
  import ProvidersPage from '$lib/pages/ProvidersPage.svelte';
  import RequestLogsPage from '$lib/pages/RequestLogsPage.svelte';
  import RoutingPage from '$lib/pages/RoutingPage.svelte';
  import SaversPage from '$lib/pages/SaversPage.svelte';
  import SettingsPage from '$lib/pages/SettingsPage.svelte';
  import ToolsPage from '$lib/pages/ToolsPage.svelte';
  import UsagePage from '$lib/pages/UsagePage.svelte';
  import { getState, mutate } from '$lib/api';
  import { object } from '$lib/data';
  import { routeFor, type NavItem } from '$lib/nav';
  import type { AlertItem, JsonObject, MutationOptions, ToastItem } from '$lib/types';

  type CachedView = { data: JsonObject; alerts: AlertItem[] };

  const VIEW_CACHE_LIMIT = 12;

  let active: NavItem = routeFor('/dashboard/ui');
  let data: JsonObject = {};
  let alerts: AlertItem[] = [];
  let loading = true;
  let mutationCount = 0;
  let error = '';
  let request: AbortController | undefined;
  let toasts: ToastItem[] = [];
  let toastId = 0;
  let pathname = '/dashboard/ui';
  let hasView = false;
  let busy = true;
  const viewCache = new Map<string, CachedView>();
  const latestPathCache = new Map<string, CachedView>();

  $: busy = loading || mutationCount > 0;

  function mergedData(source: JsonObject, meta: JsonObject): JsonObject {
    const query = object(meta.query);
    const pagination = object(meta.pagination);
    const result: JsonObject = { ...source, ...query, ...pagination };
    if (source.values && typeof source.values === 'object' && !Array.isArray(source.values)) {
      result.settings = source.values;
    }
    if (source.status && typeof source.status === 'object' && !Array.isArray(source.status)) {
      Object.assign(result, source.status);
    }
    const live = object(source.live);
    if (live.inflight !== undefined) result.live_inflight = live.inflight;
    return result;
  }

  function notify(message: string, kind: ToastItem['kind'] = 'success') {
    const id = ++toastId;
    toasts = [...toasts, { id, kind, message }];
    window.setTimeout(() => {
      toasts = toasts.filter((toast) => toast.id !== id);
    }, 3600);
  }

  function getCachedView(viewKey: string, nextPathname: string): CachedView | undefined {
    const exact = viewCache.get(viewKey);
    if (exact) {
      viewCache.delete(viewKey);
      viewCache.set(viewKey, exact);
      return exact;
    }
    return latestPathCache.get(nextPathname);
  }

  function cacheView(viewKey: string, nextPathname: string, value: CachedView) {
    viewCache.delete(viewKey);
    viewCache.set(viewKey, value);
    while (viewCache.size > VIEW_CACHE_LIMIT) {
      const oldest = viewCache.keys().next().value;
      if (oldest === undefined) break;
      viewCache.delete(oldest);
    }
    latestPathCache.set(nextPathname, value);
  }

  async function load() {
    request?.abort();
    const controller = new AbortController();
    request = controller;
    loading = true;
    error = '';
    const nextPathname = window.location.pathname.replace(/\/+$/, '') || '/dashboard/ui';
    const nextRoute = routeFor(window.location.pathname);
    const viewKey = `${nextPathname}${window.location.search}`;
    const cached = getCachedView(viewKey, nextPathname);
    pathname = nextPathname;
    active = nextRoute;
    hasView = cached !== undefined;
    if (cached) {
      data = cached.data;
      alerts = cached.alerts;
    } else {
      data = {};
      alerts = [];
    }
    try {
      const result = await getState(active.section, controller.signal);
      if (controller.signal.aborted || request !== controller) return;
      const nextData = mergedData(result.data, result.meta);
      const nextAlerts = result.alerts;
      cacheView(viewKey, nextPathname, { data: nextData, alerts: nextAlerts });
      data = nextData;
      alerts = nextAlerts;
      hasView = true;
    } catch (caught) {
      if (controller.signal.aborted || request !== controller) return;
      const message =
        caught instanceof Error ? caught.message : 'The dashboard could not be loaded.';
      if (cached) notify(`Couldn’t refresh ${active.label.toLowerCase()}: ${message}`, 'error');
      else error = message;
    } finally {
      if (request === controller) {
        request = undefined;
        loading = false;
      }
    }
  }

  function navigate(href: string) {
    const target = new URL(href, window.location.origin);
    if (
      `${window.location.pathname}${window.location.search}` !==
      `${target.pathname}${target.search}`
    ) {
      window.history.pushState({}, '', `${target.pathname}${target.search}`);
    }
    void load();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  function navigateQuery(params: Record<string, string>) {
    const target = new URL(window.location.href);
    for (const [key, value] of Object.entries(params)) {
      if (value) target.searchParams.set(key, value);
      else target.searchParams.delete(key);
    }
    if (!Object.hasOwn(params, 'offset')) target.searchParams.delete('offset');
    navigate(`${target.pathname}${target.search}`);
  }

  async function action(url: string, options: MutationOptions = {}): Promise<unknown> {
    mutationCount += 1;
    try {
      const result = await mutate(url, options);
      if (
        result !== null &&
        typeof result === 'object' &&
        !Array.isArray(result) &&
        (result as Record<string, unknown>).ok === false
      ) {
        const failure = result as Record<string, unknown>;
        const detail =
          typeof failure.error === 'string' && failure.error.trim()
            ? failure.error
            : typeof failure.status === 'number'
              ? `Upstream returned status ${failure.status}`
              : 'The action could not be completed.';
        throw new Error(detail);
      }
      notify(options.success ?? 'Changes saved');
      if (options.refresh !== false) await load();
      return result;
    } catch (caught) {
      notify(
        caught instanceof Error ? caught.message : 'The action could not be completed.',
        'error'
      );
      // Revert one-way-bound controls to server values on a failed mutation.
      if (options.refresh !== false) await load().catch(() => undefined);
      throw caught;
    } finally {
      mutationCount = Math.max(0, mutationCount - 1);
    }
  }

  async function logout() {
    await mutate('/dashboard/logout', { method: 'POST' }).catch(() => undefined);
    window.location.assign('/dashboard/login');
  }

  onMount(() => {
    document.getElementById('dashboard-initial-shell')?.remove();
    const popstate = () => void load();
    window.addEventListener('popstate', popstate);
    void load();
    return () => {
      request?.abort();
      window.removeEventListener('popstate', popstate);
    };
  });
</script>

<Shell
  {active}
  loading={busy}
  on:navigate={(event) => navigate(event.detail)}
  on:refresh={load}
  on:logout={logout}
>
  {#if loading && !hasView}
    <PageSkeleton label={active.label.toLowerCase()} section={active.section} {pathname} />
  {:else if error && !hasView}
    <section class="error-state" role="alert">
      <Icon name="warning" size={26} />
      <h2>Couldn’t load this view</h2>
      <p>{error}</p>
      <button class="button primary" on:click={load}>Try again</button>
    </section>
  {:else}
    <AlertStrip {alerts} />
    {#if active.section === 'overview'}
      <OverviewPage {data} {navigate} />
    {:else if active.section === 'usage'}
      <UsagePage {data} />
    {:else if active.section === 'analytics'}
      <AnalyticsPage {data} {navigateQuery} />
    {:else if active.section === 'leaderboard'}
      <LeaderboardPage {data} {navigateQuery} />
    {:else if active.section === 'request-logs'}
      <RequestLogsPage {data} {action} {navigateQuery} />
    {:else if active.section === 'inventory-keys'}
      <InventoryKeysPage {data} {action} {navigate} {navigateQuery} />
    {:else if active.section === 'inventory' && pathname.endsWith('/add')}
      <InventoryAddPage {data} {action} {navigate} />
    {:else if active.section === 'inventory' && pathname.endsWith('/import')}
      <InventoryImportPage {data} {action} {navigate} />
    {:else if active.section === 'inventory'}
      <InventoryOverviewPage {data} {action} {navigate} />
    {:else if active.section === 'providers'}
      <ProvidersPage {data} {action} />
    {:else if active.section === 'models'}
      <ModelsPage {data} {action} />
    {:else if active.section === 'combos'}
      <CombosPage {data} {action} />
    {:else if active.section === 'routing'}
      <RoutingPage {data} {action} />
    {:else if active.section === 'savers'}
      <SaversPage {data} {action} />
    {:else if active.section === 'budgets'}
      <BudgetsPage {data} {action} />
    {:else if active.section === 'keys'}
      <KeysPage {data} {action} {navigateQuery} />
    {:else if active.section === 'tools'}
      <ToolsPage {data} />
    {:else if active.section === 'pricing'}
      <PricingPage {data} {action} />
    {:else if active.section === 'settings'}
      <SettingsPage {data} {action} {navigate} />
    {:else}
      <section class="error-state">
        <Icon name="warning" size={26} />
        <h2>Page not found</h2>
        <p>This dashboard route is not available.</p>
        <button class="button primary" on:click={() => navigate('/dashboard/ui')}>
          Return to overview
        </button>
      </section>
    {/if}
  {/if}
</Shell>
<Toasts {toasts} />
