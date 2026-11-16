import { Handle, Position, type NodeProps } from '@xyflow/react';

import styles from './nodes.module.css';
import { Pill, type Holding } from './Pill';

// How a node shows under the current selection.
export type NodeState = 'normal' | 'highlighted' | 'dimmed';

export interface DisplayData extends Record<string, unknown> {
  label: string;
  state: NodeState;
  // The selected instance itself.
  focused: boolean;
  // What the node holds of the selected topic (`SOW · persistent`, `journal`).
  badge: Holding | null;
}

// Floating edges attach to node borders; the handles only anchor them.
function Anchors() {
  return (
    <>
      <Handle type="target" position={Position.Left} className={styles.handle} isConnectable={false} />
      <Handle type="source" position={Position.Right} className={styles.handle} isConnectable={false} />
    </>
  );
}

function stateClass(state: NodeState): string {
  return state === 'normal' ? '' : styles[state] ?? '';
}

export function InstanceNode({ data }: NodeProps) {
  const { label, state, focused, badge } = data as DisplayData;
  return (
    <div className={`${styles.instance} ${stateClass(state)} ${focused ? styles.focused : ''}`} title={label}>
      <Anchors />
      <div className={styles.name}>{label}</div>
      {badge !== null && (
        <span className={styles.badge}>
          <Pill kind={badge.kind}>{badge.label}</Pill>
        </span>
      )}
    </div>
  );
}

export function FrameNode({ data }: NodeProps) {
  const { label, state } = data as DisplayData;
  return (
    <div className={`${styles.frame} ${stateClass(state)}`}>
      <Anchors />
      <span className={styles.frameLabel}>{label}</span>
    </div>
  );
}

export function ExternalNode({ data }: NodeProps) {
  const { label, state } = data as DisplayData;
  return (
    <div
      className={`${styles.instance} ${styles.external} ${stateClass(state)}`}
      title={`${label}: a group none of the configurations declares`}
    >
      <Anchors />
      <div className={styles.name}>{label}</div>
      <div className={styles.caption}>external group</div>
    </div>
  );
}

export const nodeTypes = { instance: InstanceNode, frame: FrameNode, external: ExternalNode };
