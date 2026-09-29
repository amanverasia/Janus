import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, waitFor } from '@testing-library/svelte';
import ModelsPage from './ModelsPage.svelte';
import type { JsonObject, MutationOptions } from '$lib/types';

const provider = (prefix: string, name: string, counts: Partial<Record<string, number>> = {}) => ({
  id: `prov-${prefix}`,
  catalog_id: prefix,
  name,
  prefix,
  is_enabled: true,
  model_count: 190,
  visible_model_count: 150,
  toggleable_model_count: 190,
  toggleable_visible_count: 150,
  ...counts
});

const model = (prefix: string, id: string, disabled = false) => ({
  prefix,
  id,
  namespaced: `${prefix}/${id}`,
  provider: prefix,
  provider_id: `prov-${prefix}`,
  provider_enabled: true,
  custom_enabled: true,
  disabled,
  source: 'configured'
});

const renderPage = (data: JsonObject) => {
  const action = vi
    .fn<(url: string, options?: MutationOptions) => Promise<unknown>>()
    .mockResolvedValue({});
  const navigate = vi.fn();
  const navigateQuery = vi.fn();
  return {
    action,
    navigate,
    navigateQuery,
    ...render(ModelsPage, { props: { data, action, navigate, navigateQuery } })
  };
};

describe('ModelsPage without pagination (#245)', () => {
  it('lists every provider with server-side counts and no pager', () => {
    const page = renderPage({
      models: [],
      providers: [
        provider('openai', 'OpenAI'),
        provider('anthropic', 'Anthropic', { model_count: 0, visible_model_count: 0 })
      ],
      model_total: 190,
      visible_total: 150
    });

    expect(page.getAllByText('150/190 visible').length).toBeGreaterThan(0);
    expect(page.getAllByText('No models yet').length).toBeGreaterThan(0);
    expect(page.getByRole('button', { name: 'Browse 190 models' })).toBeTruthy();
    expect(page.queryByRole('button', { name: 'Next' })).toBeNull();
    expect(page.queryByRole('button', { name: 'Collapse all' })).toBeNull();
  });

  it('opens a provider from the overview instead of expanding a partial slice', async () => {
    const page = renderPage({
      models: [],
      providers: [provider('openai', 'OpenAI')],
      model_total: 190,
      visible_total: 150
    });

    await fireEvent.click(page.getByRole('button', { name: 'Browse 190 models' }));
    expect(page.navigate).toHaveBeenCalledWith('/dashboard/ui/models?provider=openai');
  });

  it('shows the whole selected provider expanded', () => {
    const page = renderPage({
      models: [model('openai', 'gpt-a'), model('openai', 'gpt-b', true)],
      providers: [provider('openai', 'OpenAI', { model_count: 2, visible_model_count: 1 })],
      provider: 'openai',
      model_total: 2,
      visible_total: 1
    });

    expect(page.getByText('openai/gpt-a')).toBeTruthy();
    expect(page.getByText('openai/gpt-b')).toBeTruthy();
  });

  it('derives All on/off from provider-wide counts, not loaded rows', async () => {
    const page = renderPage({
      models: [model('openai', 'gpt-a')],
      providers: [provider('openai', 'OpenAI')],
      search: 'gpt-a',
      match_total: 1,
      model_total: 190,
      visible_total: 150
    });

    const toggle = page.getByRole('button', { name: 'All on' });
    await fireEvent.click(toggle);
    await waitFor(() => expect(page.action).toHaveBeenCalledOnce());
    expect(page.action.mock.calls[0][1]?.body).toMatchObject({
      scope: 'provider',
      provider: 'openai',
      targets: [],
      enabled: true
    });
  });

  it('says when search results are truncated', () => {
    const page = renderPage({
      models: [model('openai', 'gpt-a')],
      providers: [provider('openai', 'OpenAI')],
      search: 'gpt',
      match_total: 900,
      truncated: true,
      model_total: 190,
      visible_total: 150
    });

    expect(page.getByRole('status').textContent).toContain('first 1 of 900 matches');
  });
});
