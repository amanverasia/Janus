import { describe, expect, it } from 'vitest';
import { compact, duration } from './data';

describe('duration', () => {
  it('renders sub-second values in milliseconds', () => {
    expect(duration(675)).toBe('675 ms');
    expect(duration(0)).toBe('0 ms');
    expect(duration(999.4)).toBe('999 ms');
  });

  it('renders seconds with one decimal from 1000 ms', () => {
    expect(duration(1000)).toBe('1.0 s');
    expect(duration(1100)).toBe('1.1 s');
    expect(duration(8600)).toBe('8.6 s');
  });

  it('renders minutes beyond 60 seconds', () => {
    expect(duration(60_000)).toBe('1m');
    expect(duration(125_000)).toBe('2m 5s');
  });

  it('falls back for missing values', () => {
    expect(duration(null)).toBe('—');
    expect(duration(undefined)).toBe('—');
    expect(duration('abc')).toBe('—');
    expect(duration(null, 'n/a')).toBe('n/a');
  });
});

describe('compact', () => {
  it('keeps integers below 10,000 exact with grouping', () => {
    expect(compact(0)).toBe('0');
    expect(compact(675)).toBe('675');
    expect(compact(1047)).toBe('1,047');
    expect(compact(1400)).toBe('1,400');
    expect(compact(9999)).toBe('9,999');
  });

  it('compacts from 10,000 upward', () => {
    expect(compact(10_000)).toBe('10K');
    expect(compact(12_500)).toBe('12.5K');
    expect(compact(2_300_000)).toBe('2.3M');
  });

  it('treats missing values as zero', () => {
    expect(compact(null)).toBe('0');
    expect(compact(undefined)).toBe('0');
  });
});
