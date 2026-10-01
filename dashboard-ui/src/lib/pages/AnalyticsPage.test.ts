import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render } from '@testing-library/svelte';
import AnalyticsPage from './AnalyticsPage.svelte';
import type { JsonObject } from '$lib/types';

const baseData = {
  summary: {
    total_cost: 1.2,
    total_requests: 20,
    total_input_tokens: 1000,
    total_output_tokens: 500,
    daily: []
  },
  breakdown: [],
  success: { success_2xx: 19, client_4xx: 1, server_5xx: 0, total: 20 },
  days: '30',
  dimension: 'model',
  baseline: '',
  savings: {
    baseline: 'gpt-4o',
    baseline_priced: true,
    actual_cost: 1.2,
    baseline_cost: 9.6,
    savings: 8.4,
    savings_pct: 87.5,
    requests: 18,
    excluded: { subscription_requests: 2, unpriced_requests: 0 },
    window: { kind: 'days', days: 30 },
    by_model: [
      {
        model: 'deepseek-chat',
        requests: 18,
        actual_cost: 1.2,
        baseline_cost: 9.6,
        savings: 8.4
      }
    ]
  }
};

function setup(data: JsonObject = baseData) {
  const navigateQuery = vi.fn();
  const view = render(AnalyticsPage, { props: { data, navigateQuery } });
  return { ...view, navigateQuery };
}

describe('AnalyticsPage savings panel', () => {
  it('renders headline numbers, excluded count and the per-model row', () => {
    const view = setup();
    expect(view.getByText('Savings vs baseline')).toBeTruthy();
    expect(view.getAllByText('$8.40').length).toBeGreaterThanOrEqual(1);
    expect(view.getByText('deepseek-chat')).toBeTruthy();
    expect(view.getByText('Excluded')).toBeTruthy();
    expect(view.getByText('2')).toBeTruthy();
  });

  it('changing the baseline select updates the query', async () => {
    const view = setup();
    const select = view.getByLabelText('Baseline model') as HTMLSelectElement;
    await fireEvent.change(select, { target: { value: 'gpt-4o-mini' } });
    expect(view.navigateQuery).toHaveBeenCalledWith({ baseline: 'gpt-4o-mini' });
  });

  it('keeps an unknown stored baseline visible as an option', () => {
    const view = setup({
      ...baseData,
      savings: { ...baseData.savings, baseline: 'kimi-k2', by_model: [] }
    });
    const select = view.getByLabelText('Baseline model') as HTMLSelectElement;
    expect(select.value).toBe('kimi-k2');
    expect(view.getByRole('option', { name: 'kimi-k2' })).toBeTruthy();
  });

  it('shows the empty note when the baseline resolves no priced traffic', () => {
    const view = setup({
      ...baseData,
      savings: {
        ...baseData.savings,
        baseline_priced: false,
        baseline_cost: 0,
        savings: 0,
        savings_pct: 0,
        by_model: []
      }
    });
    expect(view.getByText(/never\s+counted as savings/)).toBeTruthy();
    expect(view.queryByText('deepseek-chat')).toBeNull();
  });
});
