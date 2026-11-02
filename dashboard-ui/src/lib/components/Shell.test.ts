import { beforeAll, describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/svelte';
import Shell from './Shell.svelte';
import { routeFor } from '#lib/nav.ts';

beforeAll(() => {
  vi.stubGlobal(
    'matchMedia',
    (query: string): MediaQueryList =>
      ({
        matches: false,
        media: query,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        addListener: () => undefined,
        removeListener: () => undefined,
        dispatchEvent: () => false,
        onchange: null
      }) as MediaQueryList
  );
});

describe('Shell', () => {
  it('gives the search trigger an accessible name when its label is hidden', () => {
    const view = render(Shell, { props: { active: routeFor('/dashboard/ui') } });
    expect(view.getByRole('button', { name: 'Search dashboard' })).toBeTruthy();
  });
});
