import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, waitFor, within } from '@testing-library/svelte';
import RequestLogsPage from './RequestLogsPage.svelte';
import { dashboardFetch } from '$lib/api';
import type { JsonObject, MutationOptions } from '$lib/types';

vi.mock('$lib/api', () => ({
  dashboardFetch: vi.fn(),
  responseError: vi.fn(async (response: Response) => response.statusText)
}));

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
  vi.mocked(dashboardFetch).mockReset();
});

const log = (overrides: JsonObject): JsonObject => ({
  id: 1,
  timestamp: '2026-09-29T12:00:00Z',
  client_format: 'openai',
  model: 'gpt-4o',
  provider_id: 'openai::account-1',
  status: 200,
  duration_ms: 120,
  ...overrides
});

const renderPage = (logs: JsonObject[]) =>
  render(RequestLogsPage, {
    props: {
      data: { logs, logging_enabled: true, offset: 0, limit: 100, total: logs.length },
      action: vi.fn<(url: string, options?: MutationOptions) => Promise<unknown>>(),
      navigateQuery: vi.fn()
    }
  });

describe('RequestLogsPage resolved model column', () => {
  it('shows the resolved model next to the requested one for auto requests', () => {
    const page = renderPage([log({ model: 'auto', resolved_model: 'deepseek-chat' })]);
    expect(page.getByRole('columnheader', { name: 'Resolved' })).toBeTruthy();
    expect(page.getByRole('cell', { name: 'deepseek-chat' })).toBeTruthy();
  });

  it('renders an em-dash when no resolved model is recorded', () => {
    const page = renderPage([log({ resolved_model: null })]);
    expect(page.getAllByRole('cell', { name: '—' }).length).toBeGreaterThanOrEqual(1);
  });
});

describe('RequestLogsPage API key column', () => {
  it('prefers the joined key name in the table', () => {
    const page = renderPage([log({ client_key_name: 'ci-key', client_key_label: 'sk-janus-old' })]);
    expect(page.getByRole('columnheader', { name: 'API key' })).toBeTruthy();
    expect(page.getByRole('cell', { name: 'ci-key' })).toBeTruthy();
  });

  it('falls back to the client key label when the key row is gone', () => {
    const page = renderPage([log({ client_key_name: null, client_key_label: 'static-yaml-key' })]);
    expect(page.getByRole('cell', { name: 'static-yaml-key' })).toBeTruthy();
  });

  it('renders an em-dash when neither name nor label exists', () => {
    const page = renderPage([
      log({ client_key_name: null, client_key_label: null, resolved_model: 'gpt-4o' })
    ]);
    expect(page.getByRole('cell', { name: '—' })).toBeTruthy();
  });

  it('shows the same key field in the inspect modal', async () => {
    vi.mocked(dashboardFetch).mockResolvedValue({
      ok: true,
      json: async () =>
        log({ id: 7, client_key_name: null, client_key_label: 'labelled-key', error: null })
    } as unknown as Response);
    const page = renderPage([
      log({ id: 7, client_key_name: null, client_key_label: 'labelled-key' })
    ]);
    await fireEvent.click(page.getByTitle('Inspect'));
    const dialog = await within(document.body).findByRole('dialog', { name: 'Request detail' });
    expect(within(dialog).getByText('API key')).toBeTruthy();
    expect(within(dialog).getByText('labelled-key')).toBeTruthy();
    expect(dashboardFetch).toHaveBeenCalledWith(
      '/dashboard/api/request-logs/7',
      expect.objectContaining({ headers: { Accept: 'application/json' } })
    );
  });
});
