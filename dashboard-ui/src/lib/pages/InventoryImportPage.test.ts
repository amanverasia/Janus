import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, waitFor } from '@testing-library/svelte';
import InventoryImportPage from './InventoryImportPage.svelte';
import { dashboardFetch } from '$lib/api';

vi.mock('$lib/api', () => ({
  dashboardFetch: vi.fn()
}));

beforeEach(() => {
  vi.mocked(dashboardFetch).mockReset();
  URL.createObjectURL = vi.fn(() => 'blob:export');
  URL.revokeObjectURL = vi.fn();
});

function renderPage() {
  return render(InventoryImportPage, {
    props: { data: {}, action: vi.fn().mockResolvedValue({}), navigate: vi.fn() }
  });
}

describe('InventoryImportPage export', () => {
  it('downloads the inventory export with POST instead of a GET link', async () => {
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    vi.mocked(dashboardFetch).mockResolvedValue(
      new Response('{"keys":[]}', {
        headers: { 'content-disposition': 'attachment; filename="inventory.json"' }
      })
    );
    const view = renderPage();

    expect(view.container.querySelector('a[href="/dashboard/api/inventory/export"]')).toBeNull();
    await fireEvent.click(view.getByRole('button', { name: 'Export current inventory' }));

    await waitFor(() => expect(click).toHaveBeenCalled());
    const [url, init] = vi.mocked(dashboardFetch).mock.calls[0];
    expect(url).toBe('/dashboard/api/inventory/export');
    expect(init?.method).toBe('POST');
    click.mockRestore();
  });

  it('shows an error when the export request fails', async () => {
    vi.mocked(dashboardFetch).mockResolvedValue(new Response('', { status: 405 }));
    const view = renderPage();

    await fireEvent.click(view.getByRole('button', { name: 'Export current inventory' }));

    expect((await view.findByRole('alert')).textContent).toContain('Download failed (405)');
  });
});
