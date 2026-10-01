import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, waitFor } from '@testing-library/svelte';
import type { ValidatedMutationOptions } from '$lib/api';

const api = vi.hoisted(() => ({
  mutate: vi.fn(),
  getState: vi.fn(),
  dashboardFetch: vi.fn(),
  responseError: vi.fn(async () => 'error')
}));

vi.mock('$lib/api', () => api);

import ConnectPage from './ConnectPage.svelte';

const RAW_KEY = 'sk-proj-RAWSECRET0123456789abcdefXYZ';
const RAW_TOKEN = 'eyJhbGciOiJSUzI1NiJ9.RAWTOKENPAYLOAD.signature';

function previewPayload(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    processed_count: 5,
    new_count: 3,
    exists_count: 1,
    rejected_count: 1,
    by_provider: [
      { provider_id: 'codex', provider_display_name: 'Codex', count: 2 },
      { provider_id: 'groq', provider_display_name: 'Groq', count: 1 }
    ],
    results: [
      {
        key_masked: 'eyJh****ture',
        label: 'work@example.com',
        provider_id: 'codex',
        provider_display_name: 'Codex',
        format: 'codex_auth_json',
        status: 'new',
        error: null
      },
      {
        key_masked: 'eyJh****ure2',
        label: '',
        provider_id: 'codex',
        provider_display_name: 'Codex',
        format: 'codex_auth_json',
        status: 'new',
        error: null
      },
      {
        key_masked: 'gsk_****7890',
        label: '',
        provider_id: 'groq',
        provider_display_name: 'Groq',
        format: 'api_key',
        status: 'new',
        error: null
      },
      {
        key_masked: 'sk-p****fXYZ',
        label: '',
        provider_id: 'openai',
        provider_display_name: 'OpenAI',
        format: 'api_key',
        status: 'exists',
        error: null
      },
      {
        key_masked: '****',
        label: '',
        provider_id: null,
        provider_display_name: null,
        format: 'unknown',
        status: 'rejected',
        error: 'Unsupported credential format.'
      }
    ],
    ...overrides
  };
}

function setup() {
  const action = vi.fn(async (_url: string, options?: ValidatedMutationOptions) => {
    const payload = {
      ok: true,
      processed_count: 3,
      accepted_count: 3,
      rejected_count: 0,
      queued_count: 3,
      has_pending: true,
      results: [
        {
          id: '1',
          key_masked: 'gsk_****7890',
          provider_id: 'groq',
          provider_display_name: 'Groq',
          status: 'pending_validation',
          error: null
        }
      ]
    };
    return options?.validate ? options.validate(payload, new Response()) : payload;
  });
  const navigate = vi.fn();
  const view = render(ConnectPage, { props: { data: {}, action, navigate } });
  return { ...view, action, navigate };
}

async function pasteAndCheck(view: ReturnType<typeof setup>, value = RAW_KEY) {
  const textarea = view.getByLabelText('API keys or login file contents') as HTMLTextAreaElement;
  await fireEvent.input(textarea, { target: { value } });
  await fireEvent.click(view.getByRole('button', { name: /Check credentials/ }));
}

function assertNoSecrets(...secrets: string[]) {
  const html = document.body.innerHTML;
  const textContent = document.body.textContent ?? '';
  for (const secret of secrets) {
    expect(html).not.toContain(secret);
    expect(textContent).not.toContain(secret);
  }
}

beforeEach(() => {
  api.mutate.mockReset();
  api.getState.mockReset();
  api.getState.mockResolvedValue({
    section: 'inventory',
    alerts: [],
    data: { summary: { pending: 0 } },
    meta: {}
  });
  api.dashboardFetch.mockReset();
  api.dashboardFetch.mockResolvedValue(
    new Response(JSON.stringify({ providers: [{ id: 'groq', display_name: 'Groq' }] }), {
      headers: { 'Content-Type': 'application/json' }
    })
  );
});

afterEach(() => {
  vi.useRealTimers();
});

describe('ConnectPage', () => {
  it('renders grouped preview chips and enables Import for new credentials', async () => {
    api.mutate.mockResolvedValue(previewPayload());
    const view = setup();
    const importButton = () => view.getByRole('button', { name: /^Import/ }) as HTMLButtonElement;
    expect(importButton().disabled).toBe(true);

    await pasteAndCheck(view);

    const chips = await view.findByRole('list', { name: 'What Janus found' });
    const labels = Array.from(chips.querySelectorAll('li')).map((chip) =>
      chip.textContent?.replace(/\s+/g, ' ').trim()
    );
    expect(labels).toEqual(['Codex ×2', 'Groq ×1', '1 already stored', '1 rejected']);
    expect(api.mutate).toHaveBeenCalledWith(
      '/dashboard/api/inventory/preview',
      expect.objectContaining({ body: expect.any(FormData) })
    );
    const body = api.mutate.mock.calls[0][1].body as FormData;
    expect(body.get('provider_id')).toBe('auto');
    expect(body.has('provision_routing')).toBe(false);
    expect(view.getByText('Unsupported credential format.')).toBeTruthy();
    expect(importButton().disabled).toBe(false);
    expect(importButton().textContent?.trim()).toBe('Import 3 credentials');
  });

  it('keeps Import disabled when nothing in the preview is new', async () => {
    api.mutate.mockResolvedValue(
      previewPayload({
        new_count: 0,
        exists_count: 1,
        rejected_count: 1,
        by_provider: [],
        results: previewPayload().results.slice(3)
      })
    );
    const view = setup();
    await pasteAndCheck(view);
    await view.findByText('1 already stored');
    const importButton = view.getByRole('button', { name: /^Import/ }) as HTMLButtonElement;
    expect(importButton.disabled).toBe(true);
  });

  it('disables Import again once the input changes after a preview', async () => {
    api.mutate.mockResolvedValue(previewPayload());
    const view = setup();
    await pasteAndCheck(view);
    await view.findByRole('list', { name: 'What Janus found' });
    const textarea = view.getByLabelText('API keys or login file contents');
    await fireEvent.input(textarea, { target: { value: `${RAW_KEY}\ngsk_another` } });
    const importButton = view.getByRole('button', { name: /^Import/ }) as HTMLButtonElement;
    expect(importButton.disabled).toBe(true);
    view.unmount();
  });

  it('never renders raw secrets from pasted text or dropped files', async () => {
    api.mutate.mockResolvedValue(previewPayload());
    const view = setup();
    const input = view.container.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File([JSON.stringify({ tokens: { access_token: RAW_TOKEN } })], 'auth.json', {
      type: 'application/json'
    });
    Object.defineProperty(input, 'files', { value: [file], configurable: true });
    await fireEvent.change(input);
    await view.findByText('auth.json');

    await pasteAndCheck(view);
    await view.findByRole('list', { name: 'What Janus found' });
    const sent = api.mutate.mock.calls.map((call) => (call[1].body as FormData).get('keys_text'));
    expect(sent).toContain(RAW_KEY);
    expect(sent.some((value) => String(value).includes(RAW_TOKEN))).toBe(true);
    assertNoSecrets(RAW_KEY, RAW_TOKEN);

    await fireEvent.click(view.getByRole('button', { name: /^Import/ }));
    await view.findByText(/credentials? imported/);
    expect(view.action).toHaveBeenCalledWith(
      '/dashboard/api/inventory/submit',
      expect.objectContaining({ refresh: false })
    );
    const submitted = view.action.mock.calls[0][1]?.body as FormData;
    expect(submitted.get('provision_routing')).toBe('true');
    expect(view.getByRole('button', { name: /View in Inventory/ })).toBeTruthy();
    assertNoSecrets(RAW_KEY, RAW_TOKEN);
    view.unmount();
  });
});

describe('ConnectPage provider preselect', () => {
  afterEach(() => {
    window.history.replaceState({}, '', '/');
  });

  it('preselects a known provider from ?provider=', async () => {
    window.history.replaceState({}, '', '/dashboard/ui/connect?provider=anthropic');
    api.dashboardFetch.mockResolvedValue(
      new Response(
        JSON.stringify({
          providers: [
            { id: 'anthropic', display_name: 'Anthropic' },
            { id: 'groq', display_name: 'Groq' }
          ]
        }),
        { headers: { 'Content-Type': 'application/json' } }
      )
    );
    const view = setup();
    await view.findByRole('option', { name: 'Anthropic' });
    const select = view.getByRole('combobox') as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe('anthropic'));
    expect(api.dashboardFetch).toHaveBeenCalledWith('/dashboard/api/inventory/providers', {
      headers: { Accept: 'application/json' }
    });
  });

  it('waits for the catalog provider list before preselecting', async () => {
    window.history.replaceState({}, '', '/dashboard/ui/connect?provider=deepseek');
    api.dashboardFetch.mockResolvedValue(
      new Response(
        JSON.stringify({
          providers: [
            { id: 'anthropic', display_name: 'Anthropic' },
            { id: 'deepseek', display_name: 'DeepSeek' }
          ]
        }),
        { headers: { 'Content-Type': 'application/json' } }
      )
    );
    const action = vi.fn(async () => ({}));
    const view = render(ConnectPage, {
      props: {
        data: { provider_cards: [{ id: 'anthropic', display_name: 'Anthropic' }] },
        action,
        navigate: vi.fn()
      }
    });
    await view.findByRole('option', { name: 'DeepSeek' });
    const select = view.getByRole('combobox') as HTMLSelectElement;
    await waitFor(() => expect(select.value).toBe('deepseek'));
  });

  it('keeps automatic detection for unknown providers', async () => {
    window.history.replaceState({}, '', '/dashboard/ui/connect?provider=nope');
    const view = setup();
    await view.findByRole('option', { name: 'Groq' });
    const select = view.getByRole('combobox') as HTMLSelectElement;
    expect(select.value).toBe('auto');
  });
});
