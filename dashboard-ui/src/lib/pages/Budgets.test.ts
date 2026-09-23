import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, waitFor, within } from '@testing-library/svelte';
import BudgetsPage from './BudgetsPage.svelte';
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

const budget = {
  daily_limit: 5,
  absolute_limit: 25,
  today_spend: 1,
  total_spend: 20,
  status: 'warning',
  warn_pct: 80
};
const key: JsonObject = {
  id: 1,
  name: 'Trial key',
  prefix: 'sk-janus-example',
  can_login: false,
  is_active: 1,
  allowed_models: null,
  budget
};

const makeAction = () =>
  vi.fn<(url: string, options?: MutationOptions) => Promise<unknown>>().mockResolvedValue({});

const bodyOf = (action: ReturnType<typeof makeAction>) => {
  const body = action.mock.calls[0][1]?.body;
  expect(body).toBeInstanceOf(FormData);
  return body as FormData;
};

describe('API key budgets', () => {
  it('prefills both limits from the nested budget state and submits them unchanged', async () => {
    const action = makeAction();
    const page = render(KeysPage, {
      props: { data: { keys: [key] }, action, navigateQuery: vi.fn() }
    });
    await fireEvent.click(page.getByTitle('Edit'));
    const dialog = within(page.getByRole('dialog', { name: 'Edit API key' }));
    expect((dialog.getByLabelText('Daily budget (USD)') as HTMLInputElement).value).toBe('5');
    expect((dialog.getByLabelText('Absolute budget (USD)') as HTMLInputElement).value).toBe('25');
    await fireEvent.click(dialog.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(action).toHaveBeenCalledOnce());
    const body = bodyOf(action);
    expect(action.mock.calls[0][0]).toBe('/dashboard/api/keys/1');
    expect(body.get('daily_budget')).toBe('5');
    expect(body.get('absolute_budget')).toBe('25');
    expect(body.get('budget_fields')).toBe('1');
  });

  it('creates an absolute-only key', async () => {
    const action = makeAction();
    const page = render(KeysPage, {
      props: { data: { keys: [] }, action, navigateQuery: vi.fn() }
    });
    await fireEvent.click(page.getByRole('button', { name: 'Create key' }));
    const dialog = within(page.getByRole('dialog', { name: 'Create API key' }));
    await fireEvent.input(dialog.getByLabelText('Name'), { target: { value: 'Trial key' } });
    await fireEvent.input(dialog.getByLabelText('Absolute budget (USD)'), {
      target: { value: '25' }
    });
    expect(dialog.getByText(/including past usage/)).toBeTruthy();
    await fireEvent.click(dialog.getByRole('button', { name: 'Create key' }));
    await waitFor(() => expect(action).toHaveBeenCalledOnce());
    const body = bodyOf(action);
    expect(action.mock.calls[0][0]).toBe('/dashboard/api/v2/keys');
    expect(body.get('daily_budget')).toBe('');
    expect(body.get('absolute_budget')).toBe('25');
  });

  it('explicitly clears the absolute limit without clearing the daily limit', async () => {
    const action = makeAction();
    const page = render(KeysPage, {
      props: { data: { keys: [key] }, action, navigateQuery: vi.fn() }
    });
    await fireEvent.click(page.getByTitle('Edit'));
    const dialog = within(page.getByRole('dialog', { name: 'Edit API key' }));
    await fireEvent.input(dialog.getByLabelText('Absolute budget (USD)'), {
      target: { value: '' }
    });
    await fireEvent.click(dialog.getByRole('button', { name: 'Save changes' }));
    await waitFor(() => expect(action).toHaveBeenCalledOnce());
    expect(bodyOf(action).get('daily_budget')).toBe('5');
    expect(bodyOf(action).get('absolute_budget')).toBe('');
    expect(bodyOf(action).get('budget_fields')).toBe('1');
  });
});

describe('Budgets page', () => {
  const data: JsonObject = {
    reporting_timezone: 'UTC',
    keys: [key],
    budgets: [{ id: 1, key_id: 1, key_name: 'Trial key', ...budget, status: budget }]
  };

  it('distinguishes today and lifetime amounts and loads both limits for editing', async () => {
    const action = makeAction();
    const page = render(BudgetsPage, { props: { data, action } });
    expect(page.getByRole('columnheader', { name: 'Spent today' })).toBeTruthy();
    expect(page.getByRole('columnheader', { name: 'Spent total' })).toBeTruthy();
    expect(page.getByRole('cell', { name: '$1.00' })).toBeTruthy();
    expect(page.getByRole('cell', { name: '$20.00' })).toBeTruthy();
    await fireEvent.click(page.getByRole('button', { name: 'Edit budget for Trial key' }));
    const dialog = within(page.getByRole('dialog', { name: 'Set budget' }));
    expect((dialog.getByLabelText('Scope') as HTMLSelectElement).value).toBe('1');
    expect((dialog.getByLabelText('Daily limit (USD)') as HTMLInputElement).value).toBe('5');
    expect((dialog.getByLabelText('Absolute limit (USD)') as HTMLInputElement).value).toBe('25');
    await fireEvent.input(dialog.getByLabelText('Daily limit (USD)'), { target: { value: '' } });
    await fireEvent.click(dialog.getByRole('button', { name: 'Save budget' }));
    await waitFor(() => expect(action).toHaveBeenCalledOnce());
    expect(bodyOf(action).get('daily_limit')).toBe('');
    expect(bodyOf(action).get('absolute_limit')).toBe('25');
  });

  it('shows the absolute option immediately and enables it after selecting an API key', async () => {
    const action = makeAction();
    const page = render(BudgetsPage, { props: { data: { keys: [key], budgets: [] }, action } });
    await fireEvent.click(page.getByRole('button', { name: 'Set budget' }));
    const dialog = within(page.getByRole('dialog', { name: 'Set budget' }));
    const absolute = dialog.getByRole('spinbutton', {
      name: 'Absolute limit (USD)'
    }) as HTMLInputElement;
    expect(absolute.disabled).toBe(true);
    expect(absolute.required).toBe(false);
    expect(dialog.getByText(/select an API key in Scope above/)).toBeTruthy();
    expect(dialog.getByRole('option', { name: 'Global gateway (daily only)' })).toBeTruthy();
    await fireEvent.change(dialog.getByLabelText('Scope'), { target: { value: '1' } });
    expect(absolute.disabled).toBe(false);
    expect(absolute.required).toBe(true);
    await fireEvent.input(absolute, { target: { value: '10' } });
    expect((dialog.getByLabelText('Daily limit (USD)') as HTMLInputElement).required).toBe(false);
    await fireEvent.click(dialog.getByRole('button', { name: 'Save budget' }));
    await waitFor(() => expect(action).toHaveBeenCalledOnce());
    expect(bodyOf(action).get('key_select')).toBe('1');
    expect(bodyOf(action).get('daily_limit')).toBe('');
    expect(bodyOf(action).get('absolute_limit')).toBe('10');
  });

  it('clears and disables the absolute input when switching back to a global budget', async () => {
    const action = makeAction();
    const page = render(BudgetsPage, { props: { data, action } });
    await fireEvent.click(page.getByRole('button', { name: 'Set budget' }));
    const dialog = within(page.getByRole('dialog', { name: 'Set budget' }));
    await fireEvent.change(dialog.getByLabelText('Scope'), { target: { value: '1' } });
    expect((dialog.getByLabelText('Absolute limit (USD)') as HTMLInputElement).value).toBe('25');
    await fireEvent.change(dialog.getByLabelText('Scope'), { target: { value: 'global' } });
    const absolute = dialog.getByRole('spinbutton', {
      name: 'Absolute limit (USD)'
    }) as HTMLInputElement;
    expect(absolute.disabled).toBe(true);
    expect(absolute.value).toBe('');
    expect(absolute.required).toBe(false);
    expect((dialog.getByLabelText('Daily limit (USD)') as HTMLInputElement).required).toBe(true);
    await fireEvent.input(dialog.getByLabelText('Daily limit (USD)'), { target: { value: '5' } });
    await fireEvent.click(dialog.getByRole('button', { name: 'Save budget' }));
    await waitFor(() => expect(action).toHaveBeenCalledOnce());
    expect(bodyOf(action).get('key_select')).toBe('global');
    expect(bodyOf(action).get('daily_limit')).toBe('5');
    expect(bodyOf(action).has('absolute_limit')).toBe(false);
  });

  it('explains how to enable absolute budgets when no API keys exist', async () => {
    const action = makeAction();
    const page = render(BudgetsPage, { props: { data: { keys: [], budgets: [] }, action } });
    await fireEvent.click(page.getByRole('button', { name: 'Set budget' }));
    const dialog = within(page.getByRole('dialog', { name: 'Set budget' }));
    const absolute = dialog.getByRole('spinbutton', {
      name: 'Absolute limit (USD)'
    }) as HTMLInputElement;
    expect(absolute.disabled).toBe(true);
    expect(dialog.getByText(/Absolute budgets require a specific API key/)).toBeTruthy();
    expect(dialog.getByRole('link', { name: 'Create an API key' }).getAttribute('href')).toBe(
      '/dashboard/ui/keys'
    );
  });

  it('keeps the budget form open when saving fails', async () => {
    const action = makeAction().mockRejectedValue(new Error('Save failed'));
    const page = render(BudgetsPage, { props: { data, action } });
    await fireEvent.click(page.getByRole('button', { name: 'Edit budget for Trial key' }));
    const dialog = page.getByRole('dialog', { name: 'Set budget' });
    await fireEvent.click(within(dialog).getByRole('button', { name: 'Save budget' }));
    await waitFor(() => expect(action).toHaveBeenCalledOnce());
    expect((dialog as HTMLDialogElement).open).toBe(true);
  });
});
