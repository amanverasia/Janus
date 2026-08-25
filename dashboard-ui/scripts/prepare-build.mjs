import { readFile, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';

const indexPath = resolve('build/index.html');
const source = await readFile(indexPath, 'utf8');
const output = source
  .replaceAll('/dashboard/ui/_app/', '/dashboard/static/app/_app/')
  .replace('assets: "/dashboard/ui"', 'assets: "/dashboard/static/app"');

if (!output.includes('/dashboard/static/app/_app/')) {
  throw new Error('Dashboard asset paths were not emitted as expected');
}

await writeFile(indexPath, output);
