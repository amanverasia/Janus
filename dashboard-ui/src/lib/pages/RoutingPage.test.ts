import { describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/svelte';
import RoutingPage from './RoutingPage.svelte';
import type { JsonObject } from '#lib/types.ts';

const renderPage = (data: JsonObject) =>
  render(RoutingPage, {
    props: {
      data,
      action: vi.fn(async () => undefined),
      navigateQuery: vi.fn(),
      navigate: vi.fn()
    }
  });

it('shows the ranked string model chain returned by the routing API', () => {
  const page = renderPage({
    auto: { top: 'alpha/gpt-4o', models: ['alpha/gpt-4o', 'beta/gpt-4o-mini'] }
  });
  expect(page.getByText('1. alpha/gpt-4o')).toBeTruthy();
  expect(page.getByText('2. beta/gpt-4o-mini')).toBeTruthy();
  expect(page.queryByText('No priced, routable models for auto yet.')).toBeNull();
});

describe('RoutingPage cooldown protection', () => {
  it('explains disabled cooldowns and links to the setting', () => {
    const page = renderPage({ settings: { cooldowns_enabled: false } });
    expect(page.getByRole('alert').textContent).toContain('Failing accounts stay in rotation');
    expect(
      page.getByRole('link', { name: 'Enable account cooldowns in Settings' }).getAttribute('href')
    ).toBe('/dashboard/ui/settings');
  });

  it('uses the live handler state when settings are absent', () => {
    const page = renderPage({ live: { cooldowns_enabled: false } });
    expect(page.getByRole('alert')).toBeTruthy();
  });

  it.each([{}, { settings: { cooldowns_enabled: true } }])(
    'does not report disabled cooldowns without evidence',
    (data) => {
      const page = renderPage(data);
      expect(page.queryByRole('alert')).toBeNull();
    }
  );
});

describe('RoutingPage auto preview', () => {
  const preview = [
    {
      model: 'deepseek/Deepseek-Flash',
      score: 0.9,
      blended_cost: 0.2,
      error_rate: 0.1,
      tps_p50: 40,
      ttft_p50_ms: 300,
      samples: 12,
      overridden: false,
      signal_scope: 'provider'
    },
    {
      model: 'harbor/Deepseek-Flash',
      score: 0.5,
      blended_cost: 0.2,
      error_rate: null,
      tps_p50: null,
      ttft_p50_ms: null,
      samples: 30,
      overridden: false,
      signal_scope: 'model'
    }
  ];
  const data = {
    auto: {
      top: 'deepseek/Deepseek-Flash',
      top_n: 5,
      chain_length: 2,
      models: ['deepseek/Deepseek-Flash', 'harbor/Deepseek-Flash'],
      preview,
      overrides: []
    }
  };

  it('describes the chain length and flags model-wide signal fallback', () => {
    const page = renderPage(data);
    expect(page.getByText(/Top 5 model families/).textContent).toContain('2 routes in the chain');
    expect(page.getAllByText('model-wide')).toHaveLength(1);
  });

  it('keeps model ID casing intact', () => {
    const page = renderPage(data);
    const pill = page.getByText('1. deepseek/Deepseek-Flash');
    expect(getComputedStyle(pill).textTransform).not.toBe('capitalize');
    expect(page.getAllByText('deepseek/Deepseek-Flash').length).toBeGreaterThan(0);
  });

  it('styles the override form with shared field controls', () => {
    const page = renderPage(data);
    expect(
      page.getByLabelText('Override model').closest('label')?.classList.contains('field')
    ).toBe(true);
    expect(page.getByLabelText('Auto routing strategy').closest('.field')).toBeTruthy();
  });
});

describe('RoutingPage credential health', () => {
  const account = (overrides: JsonObject = {}): JsonObject => ({
    order: 1,
    account_id: 'uk-1',
    key_label: 'dead key',
    prefix: 'openai',
    source: 'inventory',
    cooldown_active: false,
    quota_deprioritized: false,
    routing_unhealthy: false,
    ...overrides
  });
  const failing = account({
    routing_unhealthy: true,
    routing_health_reason: 'payment_error',
    routing_health_status_code: 402,
    routing_health_at: '2026-11-02 10:00:00'
  });

  it('never labels a failing credential Ready', () => {
    const page = renderPage({
      overview: { total_accounts: 2, ready_accounts: 1, unhealthy_accounts: 1 },
      accounts: [failing, account({ order: 2, account_id: 'uk-2', key_label: 'good' })],
      unhealthy: [failing]
    });
    expect(page.getAllByText('Failing').length).toBeGreaterThanOrEqual(1);
    expect(page.getAllByText('Ready')).toHaveLength(1);
    expect(page.container.textContent).toContain('1 failing');
    expect(page.container.textContent).toContain('Payment required (HTTP 402)');
    expect(page.getByText('Review in Inventory')).toBeTruthy();
  });

  it('gives the cooling and strategy cards a subtitle', () => {
    const page = renderPage({
      overview: { total_accounts: 1, ready_accounts: 1 },
      settings: { cooldowns_enabled: true, account_strategy: 'fill_first' },
      accounts: [account()]
    });
    expect(page.container.textContent).toContain('None right now');
    expect(page.container.textContent).toContain('Uses accounts in order');
  });

  it('explains disabled cooldowns in the cooling card', () => {
    const page = renderPage({ settings: { cooldowns_enabled: false } });
    expect(page.container.textContent).toContain('Cooldowns are disabled');
    expect(page.container.textContent).toContain('Cooldowns are disabled, so no account');
  });
});
