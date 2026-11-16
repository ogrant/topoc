import type { HostedTopic, Queue } from '../model';
import styles from './pills.module.css';

// What a pill shows: a SOW, by durability, a queue, or a neutral fact.
export type PillKind = 'persistent' | 'transient' | 'queue' | 'neutral';

export function Pill({ kind, children }: { kind: PillKind; children: string }) {
  return <span className={`${styles.pill} ${kind === 'neutral' ? '' : styles[kind]}`}>{children}</span>;
}

export interface Holding {
  kind: PillKind;
  label: string;
}

// How an instance holds a topic, for its badge: its SOW (coloured by
// durability), else its journal; `null` if neither.
export function holding(topic: HostedTopic): Holding | null {
  if (topic.sow !== null) {
    const durability = topic.sow.durability;
    return {
      kind: durability === 'persistent' || durability === 'transient' ? durability : 'neutral',
      label: durability === null ? 'SOW' : `SOW · ${durability}`,
    };
  }
  return topic.journaled ? { kind: 'neutral', label: 'journal' } : null;
}

// How an instance holds a queue, for its badge: which kind of queue, and
// whether the instance owns a group-local queue's messages.
export function queueHolding(queue: Queue, instance: string): Holding {
  const label = { distributed: 'queue', group_local: 'group-local queue', local: 'local queue' }[queue.kind];
  return { kind: 'queue', label: queue.initialOwner === instance ? `${label} · owner` : label };
}
