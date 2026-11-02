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

describe('RequestLogsPage failure diagnostics', () => {
  it('shows failure reasons as text without interpreting upstream HTML', () => {
    const error = '<img src=x onerror="alert(1)"> All accounts are cooling down';
    const page = renderPage([log({ status: 503, error })]);
    expect(page.getByRole('columnheader', { name: 'Failure reason' })).toBeTruthy();
    expect(page.getByRole('cell', { name: error })).toBeTruthy();
    expect(page.container.querySelector('img')).toBeNull();
  });

  it('bounds the table reason and retains the full reason and stream metadata in detail', async () => {
    const error = `All providers exhausted: ${'upstream 402; '.repeat(30)}`;
    const entry = log({ status: 503, error, streamed: 1, resolved_model: 'selected-model' });
    vi.mocked(dashboardFetch).mockResolvedValue({
      ok: true,
      json: async () => entry
    } as unknown as Response);
    const page = renderPage([entry]);
    expect(page.getByRole('cell', { name: `${error.slice(0, 117)}…` })).toBeTruthy();
    await fireEvent.click(page.getByTitle('Inspect'));
    const dialog = await within(document.body).findByRole('dialog', { name: 'Request detail' });
    await waitFor(() => expect(within(dialog).getByText(error.trim())).toBeTruthy());
    const stream = within(dialog).getByText('Stream').parentElement;
    expect(stream?.textContent).toContain('1');
    expect(within(dialog).getByText('selected-model')).toBeTruthy();
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
    expect(page.getAllByRole('cell', { name: '—' }).length).toBeGreaterThanOrEqual(1);
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
      '/dashboard/api/request-logs/7?attempt_limit=25&attempt_offset=0',
      expect.objectContaining({ headers: { Accept: 'application/json' } })
    );
  });
});

const timelineDetail = (overrides: JsonObject = {}): JsonObject => ({
  ...log({
    id: 9,
    request_id: 'request-123',
    resolved_model: 'm1',
    status: 200,
    has_bodies: false,
    final_outcome: 'success'
  }),
  correlation: { available: true, request_id: 'request-123' },
  outcome: { status: 200, attempts: 3, kind: 'success' },
  attempt_pagination: { total: 3, limit: 25, offset: 0, page: 1, total_pages: 1 },
  attempts: [
    {
      id: 1,
      attempt_order: 1,
      model: 'm1',
      account: 'acc…-a',
      outcome: 'error',
      status: 401,
      duration_ms: 20,
      failure_class: 'auth_error',
      cooldown: { decision: 'applied', seconds: 300 },
      recovery: {
        label: 'Review credentials',
        href: '/dashboard/ui/inventory/keys?provider_id=openai'
      }
    },
    {
      id: 2,
      attempt_order: 2,
      model: 'm1',
      account: 'acc…-b',
      outcome: 'error',
      status: 402,
      duration_ms: 25,
      failure_class: 'payment_error',
      cooldown: { decision: 'applied', seconds: 300 },
      recovery: { label: 'Review billing and quota', href: '/dashboard/ui/inventory/keys' }
    },
    {
      id: 3,
      attempt_order: 3,
      model: 'm1',
      account: 'acc…-c',
      outcome: 'ok',
      status: 200,
      duration_ms: 80,
      failure_class: null,
      cooldown: { decision: 'none', seconds: null },
      recovery: null
    }
  ],
  ...overrides
});

describe('RequestLogsPage routing timeline', () => {
  it('shows ordered attempts, failure classes, cooldowns, and recovery actions', async () => {
    vi.mocked(dashboardFetch).mockResolvedValue({
      ok: true,
      json: async () => timelineDetail()
    } as unknown as Response);
    const page = renderPage([log({ id: 9 })]);
    await fireEvent.click(page.getByTitle('Inspect'));
    const dialog = await within(document.body).findByRole('dialog', { name: 'Request detail' });
    expect(within(dialog).getByText('Routing timeline')).toBeTruthy();
    expect(within(dialog).getByText('Attempt 1')).toBeTruthy();
    expect(within(dialog).getByText('Attempt 3')).toBeTruthy();
    expect(within(dialog).getByText('auth error')).toBeTruthy();
    expect(within(dialog).getAllByText(/Cooldown applied/)).toHaveLength(2);
    expect(within(dialog).getByText('acc…-a')).toBeTruthy();
    const recovery = within(dialog).getByRole('link', { name: 'Review credentials' });
    expect(recovery.getAttribute('href')).toBe('/dashboard/ui/inventory/keys?provider_id=openai');
  });

  it('loads bounded attempt pages without losing the request detail', async () => {
    const allAttempts = timelineDetail().attempts as JsonObject[];
    const first = timelineDetail({
      attempts: allAttempts.slice(0, 2),
      attempt_pagination: { total: 3, limit: 2, offset: 0, page: 1, total_pages: 2 }
    });
    const second = timelineDetail({
      attempts: allAttempts.slice(2),
      attempt_pagination: { total: 3, limit: 2, offset: 2, page: 2, total_pages: 2 }
    });
    vi.mocked(dashboardFetch)
      .mockResolvedValueOnce({ ok: true, json: async () => first } as unknown as Response)
      .mockResolvedValueOnce({ ok: true, json: async () => second } as unknown as Response);
    const page = renderPage([log({ id: 9 })]);
    await fireEvent.click(page.getByTitle('Inspect'));
    const dialog = await within(document.body).findByRole('dialog', { name: 'Request detail' });
    await fireEvent.click(within(dialog).getByRole('button', { name: 'Next attempts' }));
    await waitFor(() => expect(within(dialog).getByText('Attempt 3')).toBeTruthy());
    expect(dashboardFetch).toHaveBeenLastCalledWith(
      '/dashboard/api/request-logs/9?attempt_limit=2&attempt_offset=2',
      expect.objectContaining({ headers: { Accept: 'application/json' } })
    );
  });

  it('keeps legacy rows readable without inventing a timeline', async () => {
    vi.mocked(dashboardFetch).mockResolvedValue({
      ok: true,
      json: async () =>
        timelineDetail({
          correlation: { available: false, request_id: null },
          request_id: null,
          outcome: null,
          attempts: []
        })
    } as unknown as Response);
    const page = renderPage([log({ id: 9 })]);
    await fireEvent.click(page.getByTitle('Inspect'));
    const dialog = await within(document.body).findByRole('dialog', { name: 'Request detail' });
    expect(within(dialog).getByText(/Attempt correlation is unavailable/)).toBeTruthy();
    expect(within(dialog).queryByText('Attempt 1')).toBeNull();
  });

  it('fetches bodies only after an explicit reveal action', async () => {
    const initial = timelineDetail({ has_bodies: true });
    vi.mocked(dashboardFetch)
      .mockResolvedValueOnce({ ok: true, json: async () => initial } as unknown as Response)
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          ...initial,
          request_body: '{"private":true}',
          response_body: '{"answer":"OK"}'
        })
      } as unknown as Response);
    const page = renderPage([log({ id: 9 })]);
    await fireEvent.click(page.getByTitle('Inspect'));
    const dialog = await within(document.body).findByRole('dialog', { name: 'Request detail' });
    expect(dashboardFetch).toHaveBeenCalledTimes(1);
    expect(within(dialog).queryByText(/private/)).toBeNull();
    await fireEvent.click(within(dialog).getByRole('button', { name: 'Show bodies' }));
    await waitFor(() => expect(within(dialog).getByText(/private/)).toBeTruthy());
    expect(dashboardFetch).toHaveBeenLastCalledWith(
      '/dashboard/api/request-logs/9?attempt_limit=25&attempt_offset=0&include_bodies=true',
      expect.objectContaining({ headers: { Accept: 'application/json' } })
    );
  });
});
