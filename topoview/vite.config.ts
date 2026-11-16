import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import react from '@vitejs/plugin-react';
import { defineConfig, type Plugin } from 'vite';
import { viteSingleFile } from 'vite-plugin-singlefile';

import { sourceHash } from './build/sourceHash';

const ROOT = dirname(fileURLToPath(import.meta.url));

// Where `topoc view` reads the page template from.
const TEMPLATE = resolve(ROOT, '../src/topoc/view/template.html');

// Writes the single-file page into the Python package as its template, stamped
// with the hash of the sources it was built from.
function topocTemplate(): Plugin {
  return {
    name: 'topoc-template',
    apply: 'build',
    closeBundle() {
      const page = readFileSync(resolve(ROOT, 'dist/index.html'), 'utf-8');
      const stamp = `<meta name="topoview-source-hash" content="${sourceHash(ROOT)}">`;
      if (!page.includes('<head>')) throw new Error('topoc-template: no <head> in the built page');
      writeFileSync(TEMPLATE, page.replace('<head>', `<head>\n    ${stamp}`));
    },
  };
}

export default defineConfig({
  plugins: [react(), viteSingleFile(), topocTemplate()],
  build: { outDir: 'dist', emptyOutDir: true },
  test: { environment: 'node', include: ['src/**/*.test.ts'] },
});
