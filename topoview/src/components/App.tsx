import { useCallback, useEffect, useMemo, useState } from 'react';

import { highlight, links, queueIndex, topicIndex, type Selection } from '../derive';
import { readSelection, writeSelection } from '../selectionHash';
import type { Cluster } from '../model';
import { useTheme } from '../theme';
import styles from './App.module.css';
import { Canvas } from './Canvas';
import { DetailsPanel } from './DetailsPanel';
import { holding, queueHolding, type Holding } from './Pill';
import { Sidebar } from './Sidebar';
import { Toolbar } from './Toolbar';

export function App({ cluster }: { cluster: Cluster }) {
  const [theme, toggleTheme] = useTheme();
  const [selection, setSelection] = useState<Selection>(() => readSelection(window.location.hash));
  const select = useCallback((next: Selection) => setSelection(next), []);

  // The URL fragment follows the selection, without adding history entries.
  useEffect(() => {
    const hash = writeSelection(selection);
    if (hash !== window.location.hash) {
      history.replaceState(null, '', hash === '' ? window.location.pathname + window.location.search : hash);
    }
  }, [selection]);

  const allLinks = useMemo(() => links(cluster), [cluster]);
  const topics = useMemo(() => topicIndex(cluster), [cluster]);
  const queues = useMemo(() => queueIndex(cluster), [cluster]);
  const lit = useMemo(() => highlight(cluster, allLinks, selection), [cluster, allLinks, selection]);

  // What each instance holds of the selected topic, or how it holds the
  // selected queue.
  const badges = useMemo(() => {
    const result = new Map<string, Holding>();
    if (selection?.kind === 'queue') {
      for (const host of queues.find((queue) => queue.name === selection.name)?.hosts ?? []) {
        result.set(host.instance, queueHolding(host.queue, host.instance));
      }
      return result;
    }
    if (selection?.kind !== 'topic') return result;
    for (const host of topics.find((topic) => topic.name === selection.name)?.hosts ?? []) {
      const held = holding(host.topic);
      if (held !== null) result.set(host.instance, held);
    }
    return result;
  }, [selection, topics, queues]);

  useEffect(() => {
    document.title = `${cluster.name} · topoview`;
  }, [cluster.name]);

  // Escape clears the selection.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSelection(null);
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <div className={styles.app}>
      <Toolbar
        cluster={cluster}
        topicCount={topics.length}
        queueCount={queues.length}
        theme={theme}
        onToggleTheme={toggleTheme}
        onShowWarnings={() => setSelection(null)}
      />
      <div className={styles.body}>
        <Sidebar cluster={cluster} topics={topics} queues={queues} selection={selection} onSelect={select} />
        <Canvas cluster={cluster} links={allLinks} highlight={lit} selection={selection} badges={badges} onSelect={select} />
        <DetailsPanel
          cluster={cluster}
          topics={topics}
          queues={queues}
          links={allLinks}
          selection={selection}
          onSelect={select}
        />
      </div>
    </div>
  );
}

export function Message({ text }: { text: string }) {
  useTheme();
  return <div className={styles.message}>{text}</div>;
}
