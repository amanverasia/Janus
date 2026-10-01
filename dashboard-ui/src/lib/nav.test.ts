import { describe, expect, it } from 'vitest';
import { allNavItems, commandItems, hubFor, legacyRedirect, navHubs, routeFor } from './nav';

const UI = '/dashboard/ui';

const expected: [path: string, hub: string, tab: string, section: string][] = [
  [UI, 'Home', 'Overview', 'overview'],
  [`${UI}/connect`, 'Connect', 'Keys and logins', 'inventory'],
  [`${UI}/connect/restore`, 'Connect', 'Restore backup', 'inventory'],
  [`${UI}/inventory`, 'Inventory', 'Overview', 'inventory'],
  [`${UI}/inventory/keys`, 'Inventory', 'Keys', 'inventory-keys'],
  [`${UI}/providers`, 'Routing', 'Providers', 'providers'],
  [`${UI}/models`, 'Routing', 'Models', 'models'],
  [`${UI}/models/unreachable`, 'Routing', "Can't reach", 'models-unreachable'],
  [`${UI}/combos`, 'Routing', 'Combos', 'combos'],
  [`${UI}/routing`, 'Routing', 'Health', 'routing'],
  [`${UI}/savers`, 'Routing', 'Token savers', 'savers'],
  [`${UI}/usage`, 'Usage', 'Live', 'usage'],
  [`${UI}/analytics`, 'Usage', 'Analytics', 'analytics'],
  [`${UI}/leaderboard`, 'Usage', 'Leaderboard', 'leaderboard'],
  [`${UI}/request-logs`, 'Usage', 'Request logs', 'request-logs'],
  [`${UI}/settings`, 'Settings', 'General', 'settings'],
  [`${UI}/keys`, 'Settings', 'API keys', 'keys'],
  [`${UI}/budgets`, 'Settings', 'Budgets', 'budgets'],
  [`${UI}/pricing`, 'Settings', 'Pricing', 'pricing'],
  [`${UI}/tools`, 'Settings', 'Tools', 'tools']
];

describe('dashboard navigation hubs', () => {
  it('has six main entries', () => {
    expect(navHubs.map((hub) => hub.label)).toEqual([
      'Home',
      'Connect',
      'Inventory',
      'Routing',
      'Usage',
      'Settings'
    ]);
  });

  it.each(expected)('maps %s to %s / %s', (path, hub, tab, section) => {
    const route = routeFor(path);
    expect(route.label).toBe(tab);
    expect(route.section).toBe(section);
    expect(route.href).toBe(path);
    expect(hubFor(path)?.label).toBe(hub);
    expect(routeFor(`${path}/`).href).toBe(path);
  });

  it('covers every tab exactly once', () => {
    expect(allNavItems.map((item) => item.href).sort()).toEqual(
      expected.map(([path]) => path).sort()
    );
  });

  it('points each hub at its first tab', () => {
    for (const hub of navHubs) expect(hub.href).toBe(hub.tabs[0].href);
  });

  it('keeps every page reachable from the command palette', () => {
    expect(commandItems.map((item) => item.href).sort()).toEqual(
      allNavItems.map((item) => item.href).sort()
    );
    expect(commandItems.find((item) => item.href === `${UI}/routing`)?.label).toBe(
      'Routing health'
    );
  });

  it.each([
    [`${UI}/inventory/add`, `${UI}/connect`, 'Keys and logins'],
    [`${UI}/inventory/add/`, `${UI}/connect`, 'Keys and logins'],
    [`${UI}/inventory/import`, `${UI}/connect/restore`, 'Restore backup']
  ])('redirects legacy %s to %s', (legacy, target, tab) => {
    expect(legacyRedirect(legacy)).toBe(target);
    expect(routeFor(legacy).href).toBe(target);
    expect(routeFor(legacy).label).toBe(tab);
    expect(hubFor(legacy)?.label).toBe('Connect');
  });

  it('does not redirect current URLs', () => {
    for (const [path] of expected) expect(legacyRedirect(path)).toBeNull();
  });

  it('reports unknown URLs as not found with no hub', () => {
    expect(routeFor(`${UI}/nope`).section).toBe('not-found');
    expect(hubFor(`${UI}/nope`)).toBeUndefined();
  });
});
