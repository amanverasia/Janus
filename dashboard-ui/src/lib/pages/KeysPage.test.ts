import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, waitFor, within } from '@testing-library/svelte';
import KeysPage from './KeysPage.svelte';
import type { JsonObject, MutationOptions } from '$lib/types';

beforeEach(() => {
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute('open', '');
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute('open');
  };
});

const deferred = <T>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
};

describe('KeysPage create key', () => {
  it('ignores a second submit while the create request is in flight', async () => {
    const pending = deferred<{ api_key: string }>();
    const action = vi.fn<(url: string, options?: MutationOptions) => Promise<unknown>>(
      () => pending.promise
    );
    const existing: JsonObject = { id: 'key-a', name: 'Primary', key_prefix: 'sk-janus-1' };
    const page = render(KeysPage, {
      props: { data: { keys: [existing] }, action, navigateQuery: vi.fn() }
    });

    await fireEvent.click(page.getByRole('button', { name: 'Create key' }));
    const dialog = within(page.getByRole('dialog', { name: 'Create API key' }));
    await fireEvent.input(dialog.getByLabelText('Name'), { target: { value: 'CI key' } });

    const submitButton = dialog.getByRole('button', { name: 'Create key' }) as HTMLButtonElement;
    await fireEvent.click(submitButton);
    expect(action).toHaveBeenCalledOnce();
    expect(action.mock.calls[0][0]).toBe('/dashboard/api/v2/keys');
    await waitFor(() => expect(submitButton.disabled).toBe(true));

    await fireEvent.click(submitButton);
    pending.resolve({ api_key: 'sk-janus-reveal-once' });
    await waitFor(() => expect(page.getByText('sk-janus-reveal-once')).toBeTruthy());
    expect(action).toHaveBeenCalledOnce();
    expect(submitButton.disabled).toBe(false);
  });

  it('ignores a second revoke click while the delete request is in flight', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const pending = deferred<unknown>();
    const action = vi.fn<(url: string, options?: MutationOptions) => Promise<unknown>>(
      () => pending.promise
    );
    const existing: JsonObject = {
      id: 'key-a',
      name: 'Primary',
      key_prefix: 'sk-janus-1',
      is_active: true
    };
    const page = render(KeysPage, {
      props: { data: { keys: [existing] }, action, navigateQuery: vi.fn() }
    });
    const revoke = page.getByTitle('Revoke') as HTMLButtonElement;

    await fireEvent.click(revoke);
    await waitFor(() => expect(action).toHaveBeenCalledOnce());
    expect(action.mock.calls[0][0]).toBe('/dashboard/api/keys/key-a');
    await waitFor(() => expect(revoke.disabled).toBe(true));

    await fireEvent.click(revoke);
    expect(confirmSpy).toHaveBeenCalledTimes(1);
    expect(action).toHaveBeenCalledOnce();

    pending.resolve({});
    await waitFor(() => expect(revoke.disabled).toBe(false));
  });
});
