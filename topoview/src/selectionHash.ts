// The selection, kept in the page's URL fragment (`#topic=tvgrids`,
// `#queue=work`, `#instance=Infra1`), so a link can point at it.

import type { Selection } from './derive';

export function readSelection(hash: string): Selection {
  const params = new URLSearchParams(hash.replace(/^#/, ''));
  const topic = params.get('topic');
  if (topic !== null) return { kind: 'topic', name: topic };
  const queue = params.get('queue');
  if (queue !== null) return { kind: 'queue', name: queue };
  const instance = params.get('instance');
  if (instance !== null) return { kind: 'instance', name: instance };
  const link = params.get('link');
  if (link !== null) return { kind: 'link', id: link };
  return null;
}

export function writeSelection(selection: Selection): string {
  if (selection === null) return '';
  const params = new URLSearchParams();
  if (selection.kind === 'link') params.set('link', selection.id);
  else params.set(selection.kind, selection.name);
  return `#${params.toString()}`;
}
