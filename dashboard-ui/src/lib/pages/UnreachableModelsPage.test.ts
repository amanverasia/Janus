import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render } from '@testing-library/svelte';
import UnreachableModelsPage from './UnreachableModelsPage.svelte';
import type { JsonObject } from '$lib/types';

const anthropic = {
  prefix: 'anthropic',
  catalog_id: 'anthropic',
  name: 'Anthropic',
  count: 3,
  reasons: { provider_disabled: 3 },
  sample_models: ['claude-sonnet-4-5', 'claude-opus-4-1'],
  connect: { kind: 'connect', href: '/dashboard/ui/connect?provider=anthropic' }
};

const groq = {
  prefix: 'groq',
  catalog_id: 'groq',
  name: 'Groq',
  count: 1,
  reasons: { no_provider: 1 },
  sample_models: ['llama-3.3-70b'],
  connect: { kind: 'providers', href: '/dashboard/ui/providers' }
};

const row = (prefix: string, model: string, reason: string) => ({
  model,
  prefix,
  catalog_id: prefix,
  reason,
  source: 'catalog'
});

const baseData = (extra: JsonObject = {}): JsonObject => ({
  groups: [anthropic, groq],
  models: [
    row('anthropic', 'claude-sonnet-4-5', 'provider_disabled'),
    row('groq', 'llama-3.3-70b', 'no_provider')
  ],
  soon: [],
  unreachable_total: 4,
  reachable_total: 12,
  total: 4,
  limit: 50,
  offset: 0,
  provider: '',
  reason: '',
  search: '',
  ...extra
});

const renderPage = (data: JsonObject) => {
  const navigate = vi.fn();
  const navigateQuery = vi.fn();
  return {
    navigate,
    navigateQuery,
    ...render(UnreachableModelsPage, { props: { data, navigate, navigateQuery } })
  };
};

describe('UnreachableModelsPage (#238)', () => {
  it('renders group cards with counts, reason labels and connect actions', async () => {
    const page = renderPage(baseData());

    expect(page.getByRole('heading', { name: "Models you can't reach" })).toBeTruthy();
    expect(page.getByRole('heading', { name: 'Anthropic' })).toBeTruthy();
    expect(page.getByText('3 models')).toBeTruthy();
    expect(page.getAllByText('Provider disabled').length).toBeGreaterThan(0);
    expect(page.getAllByText('No provider').length).toBeGreaterThan(0);

    await fireEvent.click(page.getByRole('button', { name: 'Connect Anthropic' }));
    expect(page.navigate).toHaveBeenCalledWith('/dashboard/ui/connect?provider=anthropic');

    await fireEvent.click(page.getByRole('button', { name: 'Add provider' }));
    expect(page.navigate).toHaveBeenCalledWith('/dashboard/ui/providers');
  });

  it('renders untrusted model ids as literal text', () => {
    const hostile = '<img src=x onerror=alert(1)>';
    const page = renderPage(
      baseData({
        groups: [{ ...anthropic, sample_models: [hostile] }],
        models: [row('anthropic', hostile, 'provider_disabled')]
      })
    );

    expect(page.getAllByText(hostile).length).toBeGreaterThan(0);
    expect(page.container.querySelector('img')).toBeNull();
  });

  it('sends filter changes as server-side query params', async () => {
    const page = renderPage(baseData());

    await fireEvent.change(page.getByLabelText('Filter by reason'), {
      target: { value: 'no_provider' }
    });
    expect(page.navigateQuery).toHaveBeenLastCalledWith({
      provider: '',
      reason: 'no_provider',
      search: '',
      offset: ''
    });

    await fireEvent.change(page.getByLabelText('Filter by provider'), {
      target: { value: 'groq' }
    });
    expect(page.navigateQuery).toHaveBeenLastCalledWith(
      expect.objectContaining({ provider: 'groq' })
    );
  });

  it('shows the empty state when every model is reachable', () => {
    const page = renderPage(baseData({ groups: [], models: [], unreachable_total: 0, total: 0 }));

    expect(page.getByText('Every known model is reachable.')).toBeTruthy();
  });

  it('lists cooled-down models as reachable soon', () => {
    const page = renderPage(baseData({ soon: [{ model: 'gpt-4o', prefix: 'openai' }] }));

    expect(page.getByRole('heading', { name: 'Reachable soon' })).toBeTruthy();
    expect(page.getByText('openai/gpt-4o')).toBeTruthy();
  });
});
