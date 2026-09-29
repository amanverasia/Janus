import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, waitFor, within } from '@testing-library/svelte';
import InventoryKeysPage from './InventoryKeysPage.svelte';
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

const makeAction = () =>
  vi.fn<(url: string, options?: MutationOptions) => Promise<unknown>>().mockResolvedValue({});

const keyA: JsonObject = {
  id: 'key-a',
  provider_id: 'alpha',
  key_masked: 'sk-alpha-***a',
  key_label: 'Credential A',
  status: 'active',
  priority: 1
};
const keyB: JsonObject = {
  ...keyA,
  id: 'key-b',
  provider_id: 'beta',
  key_masked: 'sk-beta-***b',
  key_label: 'Credential B',
  priority: 7
};

const detailResponse = (row: JsonObject) =>
  ({ ok: true, json: async () => ({ ...row, models: [], history: [] }) }) as unknown as Response;

const revealResponse = (keyValue: string) =>
  ({ ok: true, json: async () => ({ key_value: keyValue }) }) as unknown as Response;

const deferred = <T>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
};

const flushAsync = () => new Promise((resolve) => setTimeout(resolve, 0));

const renderPage = (keys: JsonObject[], action = makeAction()) => ({
  action,
  ...render(InventoryKeysPage, {
    props: { data: { keys }, action, navigate: vi.fn(), navigateQuery: vi.fn() }
  })
});

type RenderedPage = ReturnType<typeof renderPage>;

const dialogOf = (page: RenderedPage) =>
  within(page.getByRole('dialog', { name: 'Credential details' }));

const priorityInputOf = (page: RenderedPage) =>
  dialogOf(page).getByLabelText(/Routing priority/) as HTMLInputElement;

describe('InventoryKeysPage inspect modal', () => {
  it('shows the last-clicked credential when inspect responses resolve out of order', async () => {
    const pendingA = deferred<Response>();
    const pendingB = deferred<Response>();
    vi.mocked(dashboardFetch)
      .mockImplementationOnce(() => pendingA.promise)
      .mockImplementationOnce(() => pendingB.promise);
    const page = renderPage([keyA, keyB]);
    const [inspectA, inspectB] = page.getAllByTitle('Inspect');
    await fireEvent.click(inspectA);
    await fireEvent.click(inspectB);
    expect(page.getByText('Loading credential health…')).toBeTruthy();
    pendingB.resolve(detailResponse(keyB));
    await waitFor(() => expect(dialogOf(page).getByText('Credential B')).toBeTruthy());
    pendingA.resolve(detailResponse(keyA));
    await flushAsync();
    expect(dialogOf(page).getByText('Credential B')).toBeTruthy();
    expect(dialogOf(page).queryByText('Credential A')).toBeNull();
    expect(dialogOf(page).queryByText('sk-alpha-***a')).toBeNull();
    expect(vi.mocked(dashboardFetch).mock.calls.map((call) => call[0])).toEqual([
      '/dashboard/api/inventory/keys/key-a',
      '/dashboard/api/inventory/keys/key-b'
    ]);
  });

  it('does not attach a late reveal response to a different credential', async () => {
    const pendingReveal = deferred<Response>();
    vi.mocked(dashboardFetch)
      .mockResolvedValueOnce(detailResponse(keyA))
      .mockImplementationOnce(() => pendingReveal.promise)
      .mockResolvedValueOnce(detailResponse(keyB));
    const page = renderPage([keyA, keyB]);
    const [inspectA, inspectB] = page.getAllByTitle('Inspect');
    await fireEvent.click(inspectA);
    await waitFor(() => expect(dialogOf(page).getByText('Credential A')).toBeTruthy());
    await fireEvent.click(page.getByRole('button', { name: 'Reveal for 30s' }));
    await waitFor(() => expect(page.getByRole('button', { name: 'Revealing…' })).toBeTruthy());
    await fireEvent.click(inspectB);
    await waitFor(() => expect(dialogOf(page).getByText('Credential B')).toBeTruthy());
    pendingReveal.resolve(revealResponse('sk-alpha-plain-secret'));
    await flushAsync();
    expect(dialogOf(page).queryByText('sk-alpha-plain-secret')).toBeNull();
    expect(dialogOf(page).getByText('sk-beta-***b')).toBeTruthy();
  });

  it('does not overwrite the newly opened credential when a priority save finishes late', async () => {
    const pendingSave = deferred<unknown>();
    const action = vi.fn<(url: string, options?: MutationOptions) => Promise<unknown>>(
      () => pendingSave.promise
    );
    vi.mocked(dashboardFetch)
      .mockResolvedValueOnce(detailResponse(keyA))
      .mockResolvedValueOnce(detailResponse(keyB));
    const page = renderPage([keyA, keyB], action);
    const [inspectA, inspectB] = page.getAllByTitle('Inspect');
    await fireEvent.click(inspectA);
    await waitFor(() => expect(dialogOf(page).getByText('Credential A')).toBeTruthy());
    await fireEvent.input(priorityInputOf(page), {
      target: { value: '9' }
    });
    await fireEvent.click(dialogOf(page).getByRole('button', { name: 'Save priority' }));
    await waitFor(() => expect(action).toHaveBeenCalledOnce());
    expect(action.mock.calls[0][0]).toBe('/dashboard/api/inventory/keys/key-a/priority');
    await fireEvent.click(inspectB);
    await waitFor(() => expect(dialogOf(page).getByText('Credential B')).toBeTruthy());
    pendingSave.resolve({});
    await flushAsync();
    expect(dialogOf(page).getByText('Credential B')).toBeTruthy();
    expect(dialogOf(page).queryByText('Credential A')).toBeNull();
    expect(priorityInputOf(page).value).toBe('7');
  });
});

describe('InventoryKeysPage row actions', () => {
  it('tracks row actions in flight per credential id', async () => {
    const pendingA = deferred<unknown>();
    const pendingB = deferred<unknown>();
    const action = vi
      .fn<(url: string, options?: MutationOptions) => Promise<unknown>>()
      .mockImplementationOnce(() => pendingA.promise)
      .mockImplementationOnce(() => pendingB.promise);
    const page = renderPage([keyA, keyB], action);
    const [recheckA, recheckB] = page.getAllByTitle('Recheck') as HTMLButtonElement[];

    await fireEvent.click(recheckA);
    await waitFor(() => expect(action).toHaveBeenCalledOnce());
    expect(action.mock.calls[0][0]).toBe('/dashboard/api/inventory/keys/key-a/recheck');
    await waitFor(() => expect(recheckA.disabled).toBe(true));

    await fireEvent.click(recheckA);
    expect(action).toHaveBeenCalledOnce();

    await fireEvent.click(recheckB);
    await waitFor(() => expect(action).toHaveBeenCalledTimes(2));
    expect(action.mock.calls[1][0]).toBe('/dashboard/api/inventory/keys/key-b/recheck');
    await waitFor(() => expect(recheckB.disabled).toBe(true));

    pendingA.resolve({});
    await waitFor(() => expect(recheckA.disabled).toBe(false));
    expect(recheckB.disabled).toBe(true);
    expect(action).toHaveBeenCalledTimes(2);

    pendingB.resolve({});
    await waitFor(() => expect(recheckB.disabled).toBe(false));
    expect(action).toHaveBeenCalledTimes(2);
  });

  it('does not fire a second test for a credential already being tested', async () => {
    const pending = deferred<unknown>();
    const action = vi.fn<(url: string, options?: MutationOptions) => Promise<unknown>>(
      () => pending.promise
    );
    const page = renderPage([keyA], action);
    const test = page.getByTitle('Test') as HTMLButtonElement;
    await fireEvent.click(test);
    await waitFor(() => expect(action).toHaveBeenCalledOnce());
    expect(action.mock.calls[0][0]).toBe('/dashboard/api/inventory/keys/key-a/test');
    await waitFor(() => expect(test.disabled).toBe(true));
    await fireEvent.click(test);
    expect(action).toHaveBeenCalledOnce();
    pending.resolve({});
    await waitFor(() => expect(test.disabled).toBe(false));
  });
});
