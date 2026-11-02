import { describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/svelte';
import InventoryOverviewPage from './InventoryOverviewPage.svelte';
import type { JsonObject } from '#lib/types.ts';

const baseData: JsonObject = {
  summary: {
    total: 1047,
    active: 900,
    usable: 880,
    pending: 4,
    needs_attention: 12,
    providers: 80,
    providers_with_keys: 14,
    archived: 380
  },
  provider_cards: [],
  credit_summary: [
    {
      id: 'aggregator',
      display_name: 'Aggregator',
      billing_model: 'prepaid',
      key_count: 3,
      total_remaining: 51,
      total_cap: 50,
      worst_usage_percent: null
    },
    {
      id: 'normal',
      display_name: 'Normal Pool',
      billing_model: 'prepaid',
      key_count: 2,
      total_remaining: 25,
      total_cap: 100,
      worst_usage_percent: null
    },
    {
      id: 'later',
      display_name: 'Pay Later',
      billing_model: 'postpaid',
      key_count: 2,
      total_remaining: 0,
      total_cap: 0,
      worst_usage_percent: null
    }
  ],
  best_keys: [],
  recent_activity: [],
  encryption: { encrypted: 0, plaintext: 1427, total: 1427, archived_plaintext: 380 },
  provider_encryption: { encrypted: 0, plaintext: 31, total: 31 },
  encryption_enabled: false
};

function setup(data: JsonObject = baseData) {
  return render(InventoryOverviewPage, {
    props: { data, action: vi.fn(), navigate: vi.fn() }
  });
}

describe('InventoryOverviewPage counts', () => {
  it('shows exact stored-key counts and names the population', () => {
    const view = setup();
    expect(view.getByText('1,047')).toBeTruthy();
    expect(view.container.textContent).toContain('In 14 inventory providers');
    expect(view.container.textContent).toContain('380 archived not counted');
  });

  it('explains what the encryption count includes', () => {
    const view = setup();
    const text = view.container.textContent ?? '';
    expect(text).toContain('1,458 stored secrets still need encryption');
    expect(text).toContain('1,427 inventory credentials (380 archived)');
    expect(text).toContain('31 routing provider API keys');
  });
});

describe('InventoryOverviewPage credit pools', () => {
  it('never reports more than the cap as a percentage', () => {
    const view = setup();
    expect(view.container.textContent).toContain('Above original cap');
    expect(view.container.textContent).not.toContain('102% remaining');
    expect(view.container.textContent).toContain('25% remaining');
  });

  it('shows postpaid providers as billed after use instead of a balance', () => {
    const view = setup();
    expect(view.getByText('Postpaid')).toBeTruthy();
    expect(view.getByText('Billed after use')).toBeTruthy();
    expect(view.container.textContent).not.toContain('$0.00 available');
  });

  it('leaves postpaid pools out of the available credits total', () => {
    const view = setup();
    expect(view.getByText('$76.00')).toBeTruthy();
  });
});
