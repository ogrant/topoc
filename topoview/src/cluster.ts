// Loads the cluster the page embeds. `topoc view` writes it into the
// `topoview-cluster` script element; during development, which runs the bare
// template, the reference fixture stands in.

import { FORMAT_VERSION, type Cluster } from './model';

export type Loaded = { cluster: Cluster } | { error: string };

export async function loadCluster(): Promise<Loaded> {
  const element = document.getElementById('topoview-cluster');
  if (element === null) return { error: 'This page carries no cluster.' };
  let data: unknown;
  try {
    data = JSON.parse(element.textContent ?? '');
  } catch {
    return { error: 'The cluster embedded in this page is not valid JSON.' };
  }
  if (isPlaceholder(data)) {
    if (import.meta.env.DEV) {
      const fixture = await import('../fixtures/reference.cluster.json');
      return { cluster: fixture.default as Cluster };
    }
    return { error: 'This is the topoview template: generate a page with `topoc view`.' };
  }
  const version = (data as { formatVersion?: unknown }).formatVersion;
  if (version !== FORMAT_VERSION) {
    return {
      error: `This page's cluster has format version ${String(version)}; this viewer reads version ${FORMAT_VERSION}.`,
    };
  }
  return { cluster: data as Cluster };
}

function isPlaceholder(data: unknown): boolean {
  return typeof data === 'object' && data !== null && 'topoviewPlaceholder' in data;
}
