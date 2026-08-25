export type IconName =
  | 'home'
  | 'pulse'
  | 'chart'
  | 'trophy'
  | 'logs'
  | 'vault'
  | 'plug'
  | 'route'
  | 'spark'
  | 'wallet'
  | 'key'
  | 'tool'
  | 'tag'
  | 'settings'
  | 'layers'
  | 'plus';

export interface NavItem {
  label: string;
  href: string;
  icon: IconName;
  section: string;
  keywords?: string;
}

export interface NavGroup {
  label: string;
  items: NavItem[];
}

export const navGroups: NavGroup[] = [
  {
    label: 'Observe',
    items: [
      { label: 'Overview', href: '/dashboard/ui', icon: 'home', section: 'overview' },
      {
        label: 'Usage',
        href: '/dashboard/ui/usage',
        icon: 'pulse',
        section: 'usage',
        keywords: 'live traffic tokens'
      },
      { label: 'Analytics', href: '/dashboard/ui/analytics', icon: 'chart', section: 'analytics' },
      {
        label: 'Leaderboard',
        href: '/dashboard/ui/leaderboard',
        icon: 'trophy',
        section: 'leaderboard'
      },
      {
        label: 'Request logs',
        href: '/dashboard/ui/request-logs',
        icon: 'logs',
        section: 'request-logs'
      }
    ]
  },
  {
    label: 'Route',
    items: [
      {
        label: 'Inventory',
        href: '/dashboard/ui/inventory',
        icon: 'vault',
        section: 'inventory',
        keywords: 'accounts credentials'
      },
      { label: 'Providers', href: '/dashboard/ui/providers', icon: 'plug', section: 'providers' },
      {
        label: 'Combos',
        href: '/dashboard/ui/combos',
        icon: 'layers',
        section: 'combos',
        keywords: 'fallback models'
      },
      { label: 'Routing', href: '/dashboard/ui/routing', icon: 'route', section: 'routing' },
      { label: 'Token savers', href: '/dashboard/ui/savers', icon: 'spark', section: 'savers' }
    ]
  },
  {
    label: 'Manage',
    items: [
      { label: 'Budgets', href: '/dashboard/ui/budgets', icon: 'wallet', section: 'budgets' },
      { label: 'API keys', href: '/dashboard/ui/keys', icon: 'key', section: 'keys' },
      { label: 'Tools', href: '/dashboard/ui/tools', icon: 'tool', section: 'tools' },
      { label: 'Pricing', href: '/dashboard/ui/pricing', icon: 'tag', section: 'pricing' },
      { label: 'Settings', href: '/dashboard/ui/settings', icon: 'settings', section: 'settings' }
    ]
  }
];

export const allNavItems = navGroups.flatMap((group) => group.items);
export const commandItems: NavItem[] = [
  ...allNavItems,
  {
    label: 'Inventory keys',
    href: '/dashboard/ui/inventory/keys',
    icon: 'key',
    section: 'inventory-keys',
    keywords: 'accounts credentials upstream'
  },
  {
    label: 'Add inventory keys',
    href: '/dashboard/ui/inventory/add',
    icon: 'plus',
    section: 'inventory',
    keywords: 'credential provider'
  },
  {
    label: 'Import inventory',
    href: '/dashboard/ui/inventory/import',
    icon: 'vault',
    section: 'inventory',
    keywords: 'json restore credentials'
  }
];

export function routeFor(pathname: string): NavItem {
  const normalized = pathname.length > 1 ? pathname.replace(/\/+$/, '') : pathname;
  if (normalized === '/dashboard/ui') return allNavItems[0];
  if (normalized === '/dashboard/ui/inventory/keys')
    return { label: 'Inventory keys', href: normalized, icon: 'key', section: 'inventory-keys' };
  if (normalized === '/dashboard/ui/inventory/add')
    return { label: 'Add inventory', href: normalized, icon: 'vault', section: 'inventory' };
  if (normalized === '/dashboard/ui/inventory/import')
    return { label: 'Import inventory', href: normalized, icon: 'vault', section: 'inventory' };
  return (
    allNavItems.find((item) => normalized === item.href) ?? {
      label: 'Page not found',
      href: normalized,
      icon: 'home',
      section: 'not-found'
    }
  );
}
