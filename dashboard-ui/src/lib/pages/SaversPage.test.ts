import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, waitFor } from '@testing-library/svelte';
import SaversPage from './SaversPage.svelte';
import type { JsonObject, MutationOptions } from '$lib/types';

const deferred = <T>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
};

const makeAction = () =>
  vi.fn<(url: string, options?: MutationOptions) => Promise<unknown>>().mockResolvedValue({});

const bodyOf = (action: ReturnType<typeof makeAction>, call: number) => {
  const body = action.mock.calls[call][1]?.body;
  expect(body).toBeInstanceOf(FormData);
  return body as FormData;
};

const renderPage = (action = makeAction(), settings: JsonObject = {}) => ({
  action,
  ...render(SaversPage, {
    props: {
      data: {
        settings: { saver_caveman_enabled: true, saver_caveman_level: 'lite', ...settings },
        saver_stats: {}
      },
      action
    }
  })
});

describe('SaversPage in-flight guards', () => {
  it('ignores repeated toggles while the save is in flight', async () => {
    const pending = deferred<unknown>();
    const action = vi.fn<(url: string, options?: MutationOptions) => Promise<unknown>>(
      () => pending.promise
    );
    const page = renderPage(action);
    const toggle = page.getByRole('switch', { name: 'Disable Caveman' }) as HTMLButtonElement;

    await fireEvent.click(toggle);
    await waitFor(() => expect(action).toHaveBeenCalledOnce());
    expect(action.mock.calls[0][0]).toBe('/dashboard/api/settings');
    expect(bodyOf(action, 0).get('key')).toBe('saver_caveman_enabled');
    expect(bodyOf(action, 0).get('value')).toBe('false');
    await waitFor(() => expect(toggle.disabled).toBe(true));

    await fireEvent.click(toggle);
    expect(action).toHaveBeenCalledOnce();

    pending.resolve({});
    await waitFor(() => expect(toggle.disabled).toBe(false));
    await fireEvent.click(toggle);
    await waitFor(() => expect(action).toHaveBeenCalledTimes(2));
    expect(bodyOf(action, 1).get('key')).toBe('saver_caveman_enabled');
    expect(bodyOf(action, 1).get('value')).toBe('false');
  });

  it('prevents out-of-order compression-level changes while a level save is pending', async () => {
    const pending = deferred<unknown>();
    const action = vi.fn<(url: string, options?: MutationOptions) => Promise<unknown>>(
      () => pending.promise
    );
    const page = renderPage(action);
    const ultra = page.getAllByRole('button', { name: 'ultra' })[0] as HTMLButtonElement;
    const full = page.getAllByRole('button', { name: 'full' })[0] as HTMLButtonElement;

    await fireEvent.click(ultra);
    await waitFor(() => expect(action).toHaveBeenCalledOnce());
    expect(bodyOf(action, 0).get('key')).toBe('saver_caveman_level');
    expect(bodyOf(action, 0).get('value')).toBe('ultra');
    await waitFor(() => expect(full.disabled).toBe(true));
    expect(ultra.disabled).toBe(true);

    await fireEvent.click(full);
    expect(action).toHaveBeenCalledOnce();

    pending.resolve({});
    await waitFor(() => expect(full.disabled).toBe(false));
    await fireEvent.click(full);
    await waitFor(() => expect(action).toHaveBeenCalledTimes(2));
    expect(bodyOf(action, 1).get('key')).toBe('saver_caveman_level');
    expect(bodyOf(action, 1).get('value')).toBe('full');
  });
});
