// The selection round-trips through the URL fragment, names with `/` included.

import { describe, expect, it } from 'vitest';

import { readSelection, writeSelection } from './selectionHash';

describe('selection hash', () => {
  it('round-trips topics, queues, instances and links', () => {
    for (const selection of [
      { kind: 'topic', name: 'orders/LN' },
      { kind: 'queue', name: 'work/orders' },
      { kind: 'instance', name: 'Quoting.LN' },
      { kind: 'link', id: 'instance:Quoting.LN|group:Infra' },
    ] as const) {
      expect(readSelection(writeSelection(selection))).toEqual(selection);
    }
  });

  it('reads an empty or unknown fragment as no selection', () => {
    expect(readSelection('')).toBeNull();
    expect(readSelection('#zoom=2')).toBeNull();
    expect(writeSelection(null)).toBe('');
  });
});
