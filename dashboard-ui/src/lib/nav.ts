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
  | 'plus'
  | 'upload';

export interface NavItem {
  label: string;
  href: string;
  icon: IconName;
  section: string;
  title?: string;
  keywords?: string;
}

export interface NavHub {
  label: string;
  icon: IconName;
  href: string;
  tabs: NavItem[];
}

const UI = '/dashboard/ui';

export const navHubs: NavHub[] = [
  {
    label: 'Home',
    icon: 'home',
    href: UI,
    tabs: [{ label: 'Overview', href: UI, icon: 'home', section: 'overview', title: 'Overview' }]
  },
  {
    label: 'Connect',
    icon: 'plug',
    href: `${UI}/connect`,
    tabs: [
      {
        label: 'Keys and logins',
        href: `${UI}/connect`,
        icon: 'plus',
        section: 'inventory',
        title: 'Connect',
        keywords: 'add credentials api keys auth.json codex claude cline antigravity paste drop'
      },
      {
        label: 'Restore backup',
        href: `${UI}/connect/restore`,
        icon: 'upload',
        section: 'inventory',
        title: 'Restore backup',
        keywords: 'import json export restore inventory'
      }
    ]
  },
  {
    label: 'Inventory',
    icon: 'vault',
    href: `${UI}/inventory`,
    tabs: [
      {
        label: 'Overview',
        href: `${UI}/inventory`,
        icon: 'vault',
        section: 'inventory',
        title: 'Inventory',
        keywords: 'accounts credentials capacity'
      },
      {
        label: 'Keys',
        href: `${UI}/inventory/keys`,
        icon: 'key',
        section: 'inventory-keys',
        title: 'Inventory keys',
        keywords: 'accounts credentials upstream'
      }
    ]
  },
  {
    label: 'Routing',
    icon: 'route',
    href: `${UI}/providers`,
    tabs: [
      { label: 'Providers', href: `${UI}/providers`, icon: 'plug', section: 'providers' },
      {
        label: 'Models',
        href: `${UI}/models`,
        icon: 'layers',
        section: 'models',
        keywords: 'catalog visibility discovery custom'
      },
      {
        label: 'Combos',
        href: `${UI}/combos`,
        icon: 'layers',
        section: 'combos',
        keywords: 'fallback models'
      },
      {
        label: 'Health',
        href: `${UI}/routing`,
        icon: 'route',
        section: 'routing',
        title: 'Routing health',
        keywords: 'cooldowns attempts routing'
      },
      { label: 'Token savers', href: `${UI}/savers`, icon: 'spark', section: 'savers' }
    ]
  },
  {
    label: 'Usage',
    icon: 'pulse',
    href: `${UI}/usage`,
    tabs: [
      {
        label: 'Live',
        href: `${UI}/usage`,
        icon: 'pulse',
        section: 'usage',
        title: 'Live usage',
        keywords: 'live traffic tokens'
      },
      { label: 'Analytics', href: `${UI}/analytics`, icon: 'chart', section: 'analytics' },
      { label: 'Leaderboard', href: `${UI}/leaderboard`, icon: 'trophy', section: 'leaderboard' },
      { label: 'Request logs', href: `${UI}/request-logs`, icon: 'logs', section: 'request-logs' }
    ]
  },
  {
    label: 'Settings',
    icon: 'settings',
    href: `${UI}/settings`,
    tabs: [
      {
        label: 'General',
        href: `${UI}/settings`,
        icon: 'settings',
        section: 'settings',
        title: 'Settings'
      },
      {
        label: 'API keys',
        href: `${UI}/keys`,
        icon: 'key',
        section: 'keys',
        keywords: 'client keys janus'
      },
      { label: 'Budgets', href: `${UI}/budgets`, icon: 'wallet', section: 'budgets' },
      { label: 'Pricing', href: `${UI}/pricing`, icon: 'tag', section: 'pricing' },
      { label: 'Tools', href: `${UI}/tools`, icon: 'tool', section: 'tools', keywords: 'sdk curl' }
    ]
  }
];

export const allNavItems: NavItem[] = navHubs.flatMap((hub) => hub.tabs);

export const commandItems: NavItem[] = navHubs.flatMap((hub) =>
  hub.tabs.map((tab) => ({
    ...tab,
    label: tab.title ?? tab.label,
    keywords: [hub.label, tab.label, tab.keywords].filter(Boolean).join(' ')
  }))
);

const LEGACY_REDIRECTS: Record<string, string> = {
  [`${UI}/inventory/add`]: `${UI}/connect`,
  [`${UI}/inventory/import`]: `${UI}/connect/restore`
};

function normalize(pathname: string): string {
  return pathname.length > 1 ? pathname.replace(/\/+$/, '') || '/' : pathname;
}

export function legacyRedirect(pathname: string): string | null {
  return LEGACY_REDIRECTS[normalize(pathname)] ?? null;
}

export function routeFor(pathname: string): NavItem {
  const normalized = legacyRedirect(pathname) ?? normalize(pathname);
  return (
    allNavItems.find((item) => normalized === item.href) ?? {
      label: 'Page not found',
      href: normalized,
      icon: 'home',
      section: 'not-found'
    }
  );
}

export function hubFor(pathname: string): NavHub | undefined {
  const route = routeFor(pathname);
  return navHubs.find((hub) => hub.tabs.some((tab) => tab.href === route.href));
}

export function pageTitle(item: NavItem): string {
  return item.title ?? item.label;
}
