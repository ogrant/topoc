// The hash of the front-end sources a template was built from. `topoc`'s test
// suite recomputes it (tests/test_view_template.py) to fail when the committed
// template is stale, so the two implementations must agree exactly:
//
// - the files: every file under `topoview/`, except the `node_modules` and
//   `dist` directories and files or directories whose name starts with `.`;
// - in order of their `/`-separated path relative to `topoview/`;
// - SHA-256 over, for each file: its path, a NUL byte, its bytes, a NUL byte;
// - written as lowercase hex.

import { createHash } from 'node:crypto';
import { readFileSync, readdirSync } from 'node:fs';
import { join, relative, sep } from 'node:path';

const EXCLUDED_DIRECTORIES = new Set(['node_modules', 'dist']);

function sourceFiles(root: string, directory: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    if (entry.name.startsWith('.')) continue;
    const path = join(directory, entry.name);
    if (entry.isDirectory()) {
      if (!EXCLUDED_DIRECTORIES.has(entry.name)) files.push(...sourceFiles(root, path));
    } else if (entry.isFile()) {
      files.push(relative(root, path).split(sep).join('/'));
    }
  }
  return files;
}

export function sourceHash(root: string): string {
  const hash = createHash('sha256');
  // Plain code-unit order, which matches Python's `sorted` on these ASCII paths.
  for (const file of sourceFiles(root, root).sort()) {
    hash.update(file);
    hash.update('\0');
    hash.update(readFileSync(join(root, file)));
    hash.update('\0');
  }
  return hash.digest('hex');
}
