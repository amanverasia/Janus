import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, waitFor } from '@testing-library/svelte';
import SettingsPage from './SettingsPage.svelte';
import type { JsonObject, MutationOptions } from '$lib/types';

describe('SettingsPage failed saves', () => {
  it('restores toggles, selects, and inputs to their saved values', async () => {
    const action = vi
      .fn<(url: string, options?: MutationOptions) => Promise<unknown>>()
      .mockRejectedValue(new Error('Save failed'));
    const page = render(SettingsPage, {
      props: {
        data: {
          values: {
            server_require_api_key: true,
            server_request_log_retention: 500,
            combo_strategy: 'fallback'
          }
        } as JsonObject,
        action,
        navigate: vi.fn()
      }
    });

    const apiKeyToggle = page.getByRole('checkbox', {
      name: /Require API key/
    }) as HTMLInputElement;
    await fireEvent.click(apiKeyToggle);
    await waitFor(() => expect(apiKeyToggle.checked).toBe(true));

    const comboStrategy = page.getByLabelText('Combo strategy') as HTMLSelectElement;
    await fireEvent.change(comboStrategy, { target: { value: 'round_robin' } });
    await waitFor(() => expect(comboStrategy.value).toBe('fallback'));

    const retention = page.getByLabelText('Request log retention') as HTMLInputElement;
    await fireEvent.change(retention, { target: { value: '750' } });
    await waitFor(() => expect(retention.value).toBe('500'));

    expect(action).toHaveBeenCalledTimes(3);
  });
});
