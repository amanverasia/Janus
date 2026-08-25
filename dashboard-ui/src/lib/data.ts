import type { JsonObject, JsonValue } from './types';

export const object = (value: unknown): JsonObject =>
  value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as JsonObject) : {};

export const list = (value: unknown): JsonObject[] =>
  Array.isArray(value) ? value.map(object).filter((item) => Object.keys(item).length > 0) : [];

export const firstList = (source: JsonObject, ...keys: string[]): JsonObject[] => {
  for (const key of keys) {
    const result = list(source[key]);
    if (result.length || Array.isArray(source[key])) return result;
  }
  return [];
};

export const text = (value: unknown, fallback = '—'): string => {
  if (typeof value === 'string' && value.trim()) return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return fallback;
};

export const number = (value: unknown, fallback = 0): number => {
  const result = typeof value === 'number' ? value : Number(value);
  return Number.isFinite(result) ? result : fallback;
};

export const bool = (value: unknown, fallback = false): boolean => {
  if (typeof value === 'boolean') return value;
  if (typeof value === 'number') return value !== 0;
  if (typeof value === 'string')
    return ['true', '1', 'yes', 'on', 'active', 'enabled'].includes(value.toLowerCase());
  return fallback;
};

export const money = (value: unknown): string =>
  new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 4
  }).format(number(value));

export const compact = (value: unknown): string =>
  new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(
    number(value)
  );

export const percent = (value: unknown): string =>
  `${number(value).toFixed(number(value) < 10 ? 1 : 0)}%`;

export const dateTime = (value: unknown): string => {
  const raw = text(value, '');
  if (!raw) return 'Never';
  const date = new Date(raw);
  return Number.isNaN(date.getTime())
    ? raw
    : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
};

export const formData = (form: HTMLFormElement): FormData => new FormData(form);

export const idOf = (item: JsonObject): string =>
  text(item.id ?? item.row_id ?? item.key_id ?? item.model, '');

export const entries = (value: unknown): [string, JsonValue | undefined][] =>
  Object.entries(object(value));

export const csv = (value: unknown): string =>
  Array.isArray(value)
    ? value
        .map((entry) => text(entry, ''))
        .filter(Boolean)
        .join(', ')
    : text(value, '');
