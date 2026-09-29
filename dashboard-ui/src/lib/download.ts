import { dashboardFetch } from '$lib/api';

export async function downloadAttachment(url: string, init: RequestInit, fallbackName: string) {
  const response = await dashboardFetch(url, {
    credentials: 'same-origin',
    cache: 'no-store',
    ...init
  });
  if (!response.ok) throw new Error(`Download failed (${response.status})`);
  const disposition = response.headers.get('content-disposition') ?? '';
  const filename = /filename="([^"]+)"/.exec(disposition)?.[1] ?? fallbackName;
  const link = document.createElement('a');
  link.href = URL.createObjectURL(await response.blob());
  link.download = filename;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(link.href));
}

export function downloadInventoryExport() {
  return downloadAttachment(
    '/dashboard/api/inventory/export',
    { method: 'POST', headers: { Accept: 'application/json' } },
    'janus-inventory-export.json'
  );
}
