import {
  BaseEdge,
  EdgeLabelRenderer,
  getStraightPath,
  useInternalNode,
  type EdgeProps,
  type InternalNode,
} from '@xyflow/react';

import { floatingEnds, labelPoint, routePath, type Point } from '../floating';
import type { NodeState } from './nodes';
import styles from './LinkEdge.module.css';

export interface LinkData extends Record<string, unknown> {
  // Shown on highlighted links only, so the resting canvas stays readable.
  label: string | null;
  state: NodeState;
  // Every edge of the link replicates synchronously: drawn solid.
  sync: boolean;
  // Edges in both directions (a mesh pair): arrows at both ends.
  twoWay: boolean;
  // Selects the link (its label is clickable, like the line).
  onSelect: () => void;
  // The layout's route, kept while both ends stay where the layout put them
  // (`at`); once either is dragged, the link runs straight between borders.
  route: Point[] | null;
  at: { source: Point; target: Point } | null;
}

function stayed(node: InternalNode, at: Point): boolean {
  const { x, y } = node.internals.positionAbsolute;
  return Math.abs(x - at.x) < 0.5 && Math.abs(y - at.y) < 0.5;
}

// A replication link, drawn between its nodes' borders.
export function LinkEdge({ id, source, target, data, markerEnd, markerStart }: EdgeProps) {
  const from = useInternalNode(source);
  const to = useInternalNode(target);
  if (from === undefined || to === undefined) return null;

  const { label, state, sync, onSelect, route, at } = data as LinkData;
  let path: string;
  let labelX: number;
  let labelY: number;
  if (route !== null && at !== null && stayed(from, at.source) && stayed(to, at.target)) {
    path = routePath(route);
    ({ x: labelX, y: labelY } = labelPoint(route));
  } else {
    const [start, end] = floatingEnds(from, to);
    [path, labelX, labelY] = getStraightPath({ sourceX: start.x, sourceY: start.y, targetX: end.x, targetY: end.y });
  }
  const color = state === 'highlighted' ? 'var(--color-highlight)' : 'var(--color-edge)';
  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={markerEnd}
        markerStart={markerStart}
        interactionWidth={16}
        style={{
          stroke: color,
          strokeWidth: state === 'highlighted' ? 3 : 2,
          strokeDasharray: sync ? undefined : '6 4',
          opacity: state === 'dimmed' ? 0.2 : 1,
          transition: 'opacity 0.15s',
        }}
      />
      {label !== null && (
        <EdgeLabelRenderer>
          <div
            className={`${styles.label} ${styles.highlighted} nodrag nopan`}
            // Above nodes, which a label beside a node's border could slip under.
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`, zIndex: 1001 }}
            title={label}
            onClick={onSelect}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

export const edgeTypes = { link: LinkEdge };
