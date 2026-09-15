import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/svelte';

// @testing-library/svelte auto-cleans up after each test only when the test
// runner exposes a global `afterEach`; vitest does not unless globals are on,
// so register the cleanup explicitly to avoid renders accumulating in the DOM.
afterEach(() => {
  cleanup();
});
