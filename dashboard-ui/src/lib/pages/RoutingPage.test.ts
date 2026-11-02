import { describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/svelte';
import RoutingPage from './RoutingPage.svelte';
import type { JsonObject } from '$lib/types';

const renderPage = (data: JsonObject) =>
  render(RoutingPage, {
    props: { data, action: vi.fn(async () => undefined), navigateQuery: vi.fn() }
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
