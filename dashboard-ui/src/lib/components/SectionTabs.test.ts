import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render } from '@testing-library/svelte';
import SectionTabs from './SectionTabs.svelte';
import { hubFor, routeFor } from '$lib/nav';

function renderFor(path: string, navigate = vi.fn()) {
  const view = render(SectionTabs, {
    props: { hub: hubFor(path), active: routeFor(path), navigate }
  });
  return { ...view, navigate };
}

describe('SectionTabs', () => {
  it('renders every tab of the active hub as a link and marks the current one', () => {
    const { getByRole, getAllByRole } = renderFor('/dashboard/ui/routing');
    expect(getByRole('navigation', { name: 'Routing sections' })).toBeTruthy();
    const links = getAllByRole('link');
    expect(links.map((link) => link.textContent?.trim())).toEqual([
      'Providers',
      'Models',
      'Combos',
      'Health',
      'Token savers'
    ]);
    const current = links.filter((link) => link.getAttribute('aria-current') === 'page');
    expect(current).toHaveLength(1);
    expect(current[0].textContent?.trim()).toBe('Health');
    expect(current[0].getAttribute('href')).toBe('/dashboard/ui/routing');
  });

  it('navigates in-app on a plain click', async () => {
    const { getByRole, navigate } = renderFor('/dashboard/ui/usage');
    await fireEvent.click(getByRole('link', { name: 'Request logs' }));
    expect(navigate).toHaveBeenCalledWith('/dashboard/ui/request-logs');
  });

  it('leaves modified clicks to the browser so tabs open in a new window', async () => {
    const { getByRole, navigate } = renderFor('/dashboard/ui/usage');
    await fireEvent.click(getByRole('link', { name: 'Analytics' }), { ctrlKey: true });
    await fireEvent.click(getByRole('link', { name: 'Analytics' }), { metaKey: true });
    expect(navigate).not.toHaveBeenCalled();
  });

  it('renders nothing for a single-tab hub or an unknown route', () => {
    expect(renderFor('/dashboard/ui').container.querySelector('nav')).toBeNull();
    expect(renderFor('/dashboard/ui/missing').container.querySelector('nav')).toBeNull();
  });

  it('highlights the Connect tab for the legacy add URL', () => {
    const { getByRole } = renderFor('/dashboard/ui/inventory/add');
    expect(getByRole('link', { name: 'Keys and logins' }).getAttribute('aria-current')).toBe(
      'page'
    );
  });
});
