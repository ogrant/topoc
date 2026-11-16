import type { Cluster } from '../model';
import type { Theme } from '../theme';
import styles from './Toolbar.module.css';

interface Props {
  cluster: Cluster;
  topicCount: number;
  queueCount: number;
  theme: Theme;
  onToggleTheme: () => void;
  onShowWarnings: () => void;
}

export function Toolbar({ cluster, topicCount, queueCount, theme, onToggleTheme, onShowWarnings }: Props) {
  const warnings = cluster.warnings.length;
  return (
    <header className={styles.toolbar}>
      <span className={styles.product}>topoview</span>
      <span className={styles.cluster}>{cluster.name}</span>
      <span className={styles.counts}>
        {cluster.instances.length} instances · {topicCount} topics
        {queueCount > 0 && ` · ${queueCount} ${queueCount === 1 ? 'queue' : 'queues'}`} ·{' '}
        {cluster.edges.length} destinations
      </span>
      <span className={styles.spacer} />
      {warnings > 0 && (
        <button className={styles.warnings} onClick={onShowWarnings}>
          {warnings} warning{warnings === 1 ? '' : 's'}
        </button>
      )}
      <button
        className={styles.themeBtn}
        onClick={onToggleTheme}
        title={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
      >
        {theme === 'dark' ? '☀' : '☾'}
      </button>
    </header>
  );
}
