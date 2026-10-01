import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render } from '@testing-library/svelte';
import OverviewPage from './OverviewPage.svelte';
import type { JsonObject } from '$lib/types';

const baseData = {
  stats: {
    total_requests: 10,
    total_input_tokens: 100,
    total_output_tokens: 50,
    total_cost: 1.5
  },
  provider_count: 1,
  today_cost: 0.75,
  savings_today: {
    baseline: 'gpt-4o',
    baseline_priced: true,
    actual_cost: 0.75,
    baseline_cost: 4.5,
    savings: 3.75,
    savings_pct: 83.33,
    requests: 9,
    excluded: { subscription_requests: 2, unpriced_requests: 1 },
    window: { kind: 'today', reporting_timezone: 'UTC' }
  },
  reporting_timezone: 'UTC',
  live_inflight: 0,
  cooldown_count: 0,
  setup_checklist: { has_providers: true, has_keys: true, has_requests: true }
};

function setup(data: JsonObject = baseData) {
  return render(OverviewPage, {
    props: { data, navigate: vi.fn() }
  });
}

describe('OverviewPage savings tile', () => {
  it('renders the saved-today tile with pct vs baseline', () => {
    const view = setup();
    expect(view.getByText('Saved today')).toBeTruthy();
    expect(view.getByText('$3.75')).toBeTruthy();
    expect(view.container.textContent).toContain('83%');
    expect(view.container.textContent).toContain('vs gpt-4o');
  });

  it('omits the tile when savings_today is null', () => {
    const view = setup({ ...baseData, savings_today: null });
    expect(view.queryByText('Saved today')).toBeNull();
  });

  it('never interpolates a model id into HTML', () => {
    const hostile = '<img src=x onerror=alert(1)>';
    const view = setup({
      ...baseData,
      savings_today: { ...baseData.savings_today, baseline: hostile }
    });
    expect(view.container.textContent).toContain(hostile);
    expect(view.container.querySelector('img')).toBeNull();
  });
});
