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

describe('SettingsPage dispatch limits', () => {
  it('shows effective limits and explains scope, recovery, and Request Logs', async () => {
    const navigate = vi.fn();
    const page = render(SettingsPage, {
      props: {
        data: { status: { dispatch_timeout_s: 90, dispatch_max_attempts: 30 } },
        action: vi.fn().mockResolvedValue({ ok: true }),
        navigate
      }
    });

    expect((page.getByLabelText('Dispatch timeout (seconds)') as HTMLInputElement).value).toBe(
      '90'
    );
    expect((page.getByLabelText('Maximum dispatch attempts') as HTMLInputElement).value).toBe('30');
    expect(
      page.getByText(/Includes preprocessing, credential refresh, image prefetch/).textContent
    ).toMatch(/panel\/judge dispatch until the first nonempty client-visible stream output/);
    expect(page.getByText(/The stream continues afterwards/).textContent).toMatch(
      /existing upstream transport timeouts/
    );
    expect(
      page.getByText(/Both limits apply even when account cooldowns are disabled/)
    ).toBeTruthy();
    expect(page.getByText(/After a failure, inspect Request Logs/).textContent).toMatch(
      /Fix\s+credentials and cooldowns before increasing these limits/
    );
    const logs = page.getByRole('link', { name: 'Inspect Request Logs' });
    expect(logs.getAttribute('href')).toBe('/dashboard/ui/request-logs');
    await fireEvent.click(logs);
    expect(navigate).toHaveBeenCalledWith('/dashboard/ui/request-logs');
  });

  it.each([
    ['Dispatch timeout (seconds)', 'server_dispatch_timeout_s', '1'],
    ['Dispatch timeout (seconds)', 'server_dispatch_timeout_s', '600'],
    ['Maximum dispatch attempts', 'server_dispatch_max_attempts', '1'],
    ['Maximum dispatch attempts', 'server_dispatch_max_attempts', '100']
  ])('saves %s as %s=%s through the settings mutation', async (label, key, value) => {
    const action = vi.fn().mockResolvedValue({ ok: true });
    const page = render(SettingsPage, { props: { data: {}, action, navigate: vi.fn() } });
    const field = page.getByLabelText(label) as HTMLInputElement;

    await fireEvent.change(field, { target: { value } });

    await waitFor(() => expect(action).toHaveBeenCalledOnce());
    const [url, options] = action.mock.calls[0];
    expect(url).toBe('/dashboard/api/settings');
    expect(options.success).toBe('Setting saved');
    expect(options.body.get('key')).toBe(key);
    expect(options.body.get('value')).toBe(value);
    expect(field.value).toBe(value);
  });

  it.each([
    ['Dispatch timeout (seconds)', ''],
    ['Dispatch timeout (seconds)', '0'],
    ['Dispatch timeout (seconds)', '-1'],
    ['Dispatch timeout (seconds)', '601'],
    ['Dispatch timeout (seconds)', '1.5'],
    ['Maximum dispatch attempts', ''],
    ['Maximum dispatch attempts', '0'],
    ['Maximum dispatch attempts', '-1'],
    ['Maximum dispatch attempts', '101'],
    ['Maximum dispatch attempts', '1.5']
  ])('blocks invalid %s=%s before sending a mutation', async (label, value) => {
    const action = vi.fn();
    const page = render(SettingsPage, { props: { data: {}, action, navigate: vi.fn() } });
    const field = page.getByLabelText(label) as HTMLInputElement;
    const reportValidity = vi.spyOn(field, 'reportValidity');

    await fireEvent.change(field, { target: { value } });

    expect(action).not.toHaveBeenCalled();
    expect(field.checkValidity()).toBe(false);
    expect(reportValidity).toHaveBeenCalledOnce();
  });

  it.each([
    ['Dispatch timeout (seconds)', 'server_dispatch_timeout_s', 'dispatch_timeout_s', '90', '120'],
    [
      'Maximum dispatch attempts',
      'server_dispatch_max_attempts',
      'dispatch_max_attempts',
      '30',
      '40'
    ]
  ])(
    'restores %s after a failed save and allows retry',
    async (label, key, statusKey, saved, next) => {
      const action = vi
        .fn()
        .mockRejectedValueOnce(new Error('Save failed'))
        .mockResolvedValue({ ok: true });
      const page = render(SettingsPage, {
        props: { data: { status: { [statusKey]: Number(saved) } }, action, navigate: vi.fn() }
      });
      const field = page.getByLabelText(label) as HTMLInputElement;

      await fireEvent.change(field, { target: { value: next } });
      await waitFor(() => expect(field.value).toBe(saved));
      await fireEvent.change(field, { target: { value: next } });
      await waitFor(() => expect(action).toHaveBeenCalledTimes(2));
      expect(action.mock.calls[1][1].body.get('key')).toBe(key);
      expect(action.mock.calls[1][1].body.get('value')).toBe(next);
      expect(field.value).toBe(next);
    }
  );
});
