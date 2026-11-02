import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { test } from 'node:test';

const require = createRequire(import.meta.url);
const pluginRequire = createRequire(require.resolve('@sveltejs/vite-plugin-svelte'));
const merge = pluginRequire('deepmerge');

const attack = () => JSON.parse('{"__proto__":{"janusPolluted":true}}');

function assertClean(value) {
  assert.equal(Object.getPrototypeOf(value), Object.prototype);
  assert.equal(value.janusPolluted, undefined);
  assert.equal({}.janusPolluted, undefined);
}

test('blocks poisoning when the destination has a null prototype', () => {
  assertClean(merge(Object.create(null), attack()));
});

test('blocks poisoning from source and target own __proto__ keys', () => {
  assertClean(merge(attack(), {}));
  assertClean(merge(attack(), attack()));
  assertClean(merge({}, attack()));
});

test('blocks poisoning when nested defaults are null, undefined or primitive', () => {
  for (const value of [null, undefined, false, 0, 'value']) {
    const result = merge({ nested: value }, { nested: attack() });
    assertClean(result);
    assertClean(result.nested);
  }
});

test('filters constructor and prototype keys recursively on both sides', () => {
  const payload = JSON.parse(
    '{"constructor":{"prototype":{"janusPolluted":true}},"prototype":{"janusPolluted":true},"safe":42}'
  );
  for (const result of [merge({}, payload), merge(payload, {}), merge.all([payload])]) {
    assertClean(result);
    assert.equal(Object.hasOwn(result, 'constructor'), false);
    assert.equal(Object.hasOwn(result, 'prototype'), false);
    assert.equal(result.safe, 42);
  }
});

test('merges Svelte plugin configuration without mutating inputs', () => {
  const transform = () => {};
  const defaults = {
    compilerOptions: { dev: false },
    extensions: ['.svelte'],
    preprocess: transform
  };
  const override = { compilerOptions: { css: 'external' }, extensions: ['.custom.svelte'] };
  const result = merge(defaults, override, { arrayMerge: (_target, source) => source });
  assert.deepEqual(result.compilerOptions, { dev: false, css: 'external' });
  assert.deepEqual(result.extensions, ['.custom.svelte']);
  assert.equal(result.preprocess, transform);
  assert.deepEqual(defaults.compilerOptions, { dev: false });
  assert.deepEqual(defaults.extensions, ['.svelte']);
});

test('preserves symbols, special values, array cloning and custom merge support', () => {
  const key = Symbol('config');
  const date = new Date(0);
  const regex = /config/;
  const result = merge(
    { [key]: { a: 1 }, values: [{ x: 1 }] },
    { [key]: { b: 2 }, values: [2], date, regex }
  );
  assert.deepEqual(result[key], { a: 1, b: 2 });
  assert.deepEqual(result.values, [{ x: 1 }, 2]);
  assert.equal(result.date, date);
  assert.equal(result.regex, regex);
  assert.deepEqual(
    merge(
      { count: { n: 1 } },
      { count: { n: 2 } },
      {
        customMerge: (key) => (key === 'count' ? (a, b) => ({ n: a.n + b.n }) : undefined)
      }
    ),
    { count: { n: 3 } }
  );
});

test('only resolves the reviewed local patch for the plugin', () => {
  assert.match(pluginRequire.resolve('deepmerge'), /vendor[/\\\\]deepmerge[/\\\\]index\.cjs$/);
  assert.equal(pluginRequire('deepmerge/package.json').name, '@janus/deepmerge');
});
