import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/svelte';
import MiniChart from './MiniChart.svelte';

describe('MiniChart', () => {
  it('renders a chart for finite series', () => {
    const { container } = render(MiniChart, {
      props: { values: [1, 2, 3, 2], label: 'requests' }
    });
    const polyline = container.querySelector('polyline');
    expect(polyline).toBeTruthy();
    expect(polyline?.getAttribute('points') ?? '').not.toMatch(/NaN/);
  });

  it('never emits NaN points when only one value survives filtering', () => {
    const { container } = render(MiniChart, {
      props: { values: [5, Number.NaN], label: 'requests' }
    });
    const polyline = container.querySelector('polyline');
    expect(polyline).toBeNull();
    expect(container.textContent).toContain('No data for this period yet.');
  });

  it('treats an all-zero series as empty', () => {
    const { container } = render(MiniChart, {
      props: { values: [0, 0, 0], label: 'requests' }
    });
    expect(container.querySelector('polyline')).toBeNull();
  });
});
