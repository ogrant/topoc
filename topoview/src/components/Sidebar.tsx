import { useState } from 'react';

import type { QueueInfo, Selection, TopicInfo } from '../derive';
import type { Cluster } from '../model';
import styles from './Sidebar.module.css';

interface Props {
  cluster: Cluster;
  topics: TopicInfo[];
  queues: QueueInfo[];
  selection: Selection;
  onSelect: (selection: Selection) => void;
}

export function Sidebar({ cluster, topics, queues, selection, onSelect }: Props) {
  // The selected topic's, queue's or instance's name, if one is selected.
  const selected = (kind: 'topic' | 'queue' | 'instance'): string | null =>
    selection !== null && selection.kind === kind ? selection.name : null;
  // Picking the selected item again clears the selection.
  const pick = (kind: 'topic' | 'queue' | 'instance', name: string) =>
    onSelect(selected(kind) === name ? null : { kind, name });
  return (
    <aside className={styles.sidebar}>
      <Section
        title="Instances"
        items={cluster.instances.map((instance) => ({
          name: instance.name,
          meta: instance.group === instance.name ? '' : instance.group,
          selected: selected('instance') === instance.name,
        }))}
        onPick={(name) => pick('instance', name)}
      />
      <Section
        title="Topics"
        items={topics.map((topic) => ({
          name: topic.name,
          meta: `${topic.hosts.length} ${topic.hosts.length === 1 ? 'instance' : 'instances'}`,
          selected: selected('topic') === topic.name,
        }))}
        onPick={(name) => pick('topic', name)}
      />
      {queues.length > 0 && (
        <Section
          title="Queues"
          items={queues.map((queue) => ({
            name: queue.name,
            meta: `${queue.hosts.length} ${queue.hosts.length === 1 ? 'instance' : 'instances'}`,
            selected: selected('queue') === queue.name,
          }))}
          onPick={(name) => pick('queue', name)}
        />
      )}
    </aside>
  );
}

interface Item {
  name: string;
  meta: string;
  selected: boolean;
}

function Section({ title, items, onPick }: { title: string; items: Item[]; onPick: (name: string) => void }) {
  const [query, setQuery] = useState('');
  const needle = query.trim().toLowerCase();
  const shown = needle === '' ? items : items.filter((item) => item.name.toLowerCase().includes(needle));
  return (
    <section className={styles.section}>
      <h2 className={styles.heading}>
        {title} ({items.length})
      </h2>
      <input
        className={styles.search}
        type="search"
        placeholder={`Search ${title.toLowerCase()}`}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      <ul className={styles.list}>
        {shown.map((item) => (
          <li key={item.name}>
            <button
              className={`${styles.item} ${item.selected ? styles.selected : ''}`}
              onClick={() => onPick(item.name)}
              title={item.name}
            >
              <span className={styles.name}>{item.name}</span>
              <span className={styles.meta}>{item.meta}</span>
            </button>
          </li>
        ))}
        {shown.length === 0 && <li className={styles.empty}>No match</li>}
      </ul>
    </section>
  );
}
