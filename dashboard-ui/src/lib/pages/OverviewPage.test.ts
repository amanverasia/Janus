import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render } from '@testing-library/svelte';
import OverviewPage from './OverviewPage.svelte';
import type { JsonObject } from '#lib/types.ts';

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

describe('OverviewPage stat grid', () => {
  it('states the actual window behind Total requests', () => {
    const view = setup({ ...baseData, stats: { ...baseData.stats, period_days: 30 } });
    expect(view.getByText('Last 30 days')).toBeTruthy();
    expect(view.container.textContent).not.toContain('selected period');
  });

  it('defaults the window to 30 days and names a 24-hour window', () => {
    expect(setup().getByText('Last 30 days')).toBeTruthy();
    const day = setup({ ...baseData, stats: { ...baseData.stats, period_days: 1 } });
    expect(day.getByText('Last 24 hours')).toBeTruthy();
  });

  it('marks the five-card layout only when the savings tile is present', () => {
    const five = setup();
    expect(five.container.querySelector('.stats-grid.five')).not.toBeNull();
    const four = setup({ ...baseData, savings_today: null });
    expect(four.container.querySelector('.stats-grid.five')).toBeNull();
  });
});

describe('OverviewPage provider health', () => {
  const healthy = {
    credentials_total: 40,
    credentials_needing_attention: 0,
    credentials_routing_unhealthy: 0,
    attempts: {
      window_hours: 24,
      total: 100,
      ok: 98,
      failed: 2,
      aborted: 0,
      success_rate: 98,
      failed_by_class: {}
    }
  };

  it('reports Operational only when credentials and attempts are healthy', () => {
    const view = setup({ ...baseData, provider_health: healthy });
    expect(view.getByText('Operational')).toBeTruthy();
    expect(view.container.textContent).toContain('98% succeeded');
  });

  it('is not Operational when credentials fail at routing, and links to them', async () => {
    const navigate = vi.fn();
    const view = render(OverviewPage, {
      props: {
        data: {
          ...baseData,
          provider_health: {
            ...healthy,
            credentials_needing_attention: 30,
            credentials_routing_unhealthy: 30
          }
        },
        navigate
      }
    });
    expect(view.queryByText('Operational')).toBeNull();
    expect(view.getByText('Failing')).toBeTruthy();
    expect(view.container.textContent).toContain('30 failing');
    await fireEvent.click(view.getByText('Review failing credentials'));
    expect(navigate).toHaveBeenCalledWith('/dashboard/ui/inventory/keys?status=routing_unhealthy');
  });

  it('reflects a high recent upstream attempt failure rate', () => {
    const view = setup({
      ...baseData,
      provider_health: {
        ...healthy,
        attempts: { ...healthy.attempts, total: 100, ok: 10, failed: 90, success_rate: 10 }
      }
    });
    expect(view.queryByText('Operational')).toBeNull();
    expect(view.getByText('Failing')).toBeTruthy();
    expect(view.container.textContent).toContain('90 of 100 attempts failed');
  });

  it('ignores a failure rate from too few attempts', () => {
    const view = setup({
      ...baseData,
      provider_health: {
        ...healthy,
        attempts: { ...healthy.attempts, total: 3, ok: 1, failed: 2, success_rate: 33 }
      }
    });
    expect(view.getByText('Operational')).toBeTruthy();
  });
});
