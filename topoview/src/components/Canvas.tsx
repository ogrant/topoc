import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
  ReactFlowProvider,
  useNodesInitialized,
  useNodesState,
  useReactFlow,
  type Edge,
  type Node,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { useEffect, useMemo, useRef, useState } from 'react';

import {
  groupNodeId,
  isSync,
  linkQueues,
  linkTopics,
  queueNames,
  type Highlight,
  type Link,
  type Selection,
} from '../derive';
import type { Point } from '../floating';
import { layout, type NodeData } from '../layout';
import type { Cluster } from '../model';
import styles from './Canvas.module.css';
import { edgeTypes, type LinkData } from './LinkEdge';
import { nodeTypes, type DisplayData, type NodeState } from './nodes';
import type { Holding } from './Pill';

interface Props {
  cluster: Cluster;
  links: Link[];
  highlight: Highlight;
  selection: Selection;
  // What each instance holds of the selected topic, by instance name.
  badges: Map<string, Holding>;
  onSelect: (selection: Selection) => void;
}

export function Canvas(props: Props) {
  return (
    <div className={styles.canvas}>
      <ReactFlowProvider>
        <Flow {...props} />
      </ReactFlowProvider>
    </div>
  );
}

function Flow({ cluster, links, highlight, selection, badges, onSelect }: Props) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node<NodeData>>([]);
  const [routes, setRoutes] = useState(new Map<string, Point[]>());
  const [positions, setPositions] = useState(new Map<string, Point>());

  useEffect(() => {
    let current = true;
    void layout(cluster, links).then((placement) => {
      if (!current) return;
      setNodes(placement.nodes);
      setRoutes(placement.routes);
      setPositions(placement.positions);
    });
    return () => {
      current = false;
    };
  }, [cluster, links, setNodes]);

  // Fit the cluster to the view once, when its nodes first have their size.
  const { fitView } = useReactFlow();
  const initialised = useNodesInitialized();
  const fitted = useRef(false);
  useEffect(() => {
    if (initialised && !fitted.current) {
      fitted.current = true;
      void fitView({ padding: 0.1 });
    }
  }, [initialised, fitView]);

  const displayed = useMemo(() => {
    const focused = selection?.kind === 'instance' ? `instance:${selection.name}` : null;
    return nodes.map((node): Node<DisplayData> => {
      let state: NodeState = !highlight.active ? 'normal' : highlight.nodes.has(node.id) ? 'highlighted' : 'dimmed';
      // A frame stays lit while any of its members is.
      if (state === 'dimmed' && node.type === 'frame') {
        const members = cluster.groups.find((group) => groupNodeId(group.name) === node.id)?.members ?? [];
        if (members.some((member) => highlight.nodes.has(`instance:${member}`))) state = 'normal';
      }
      const instance = node.id.startsWith('instance:') ? node.id.slice('instance:'.length) : null;
      return {
        ...node,
        data: {
          label: node.data.label,
          state,
          focused: node.id === focused,
          badge: instance === null ? null : (badges.get(instance) ?? null),
        },
      };
    });
  }, [nodes, highlight, selection, badges, cluster]);

  const atOf = (source: string, target: string) => {
    const from = positions.get(source);
    const to = positions.get(target);
    return from === undefined || to === undefined ? null : { source: from, target: to };
  };

  const queues = useMemo(() => queueNames(cluster), [cluster]);

  const edges = useMemo(
    () =>
      links.map((link): Edge<LinkData> => {
        const state: NodeState = !highlight.active ? 'normal' : highlight.links.has(link.id) ? 'highlighted' : 'dimmed';
        const color = state === 'highlighted' ? 'var(--color-highlight)' : 'var(--color-edge)';
        const arrow = { type: MarkerType.ArrowClosed, color, width: 16, height: 16 };
        const topics = linkTopics(link, queues).length;
        const carriedQueues = linkQueues(link, queues).length;
        const twoWay = link.backward.length > 0;
        // With a topic or queue selected, arrowheads show only the directions
        // carrying it: a two-way link may carry the topic one way only.
        const carries = (edges: typeof link.forward) =>
          (selection?.kind !== 'topic' && selection?.kind !== 'queue') || state !== 'highlighted'
            ? edges.length > 0
            : edges.some((edge) => edge.destination.topics.some((topic) => topic.name === selection.name));
        // Labels on highlighted links only: a selected topic's filter, or how
        // many topics (and queues) a link around a selected instance or link
        // carries.
        const counted = [
          ...(topics > 0 ? [`${topics} ${topics === 1 ? 'topic' : 'topics'}`] : []),
          ...(carriedQueues > 0 ? [`${carriedQueues} ${carriedQueues === 1 ? 'queue' : 'queues'}`] : []),
        ].join(' · ');
        const label =
          state !== 'highlighted'
            ? null
            : selection?.kind === 'topic'
              ? (highlight.linkLabels.get(link.id) ?? null)
              : selection?.kind === 'queue'
                ? null
                : counted;
        return {
          id: link.id,
          source: link.from,
          target: link.to,
          type: 'link',
          markerEnd: carries(link.forward) ? arrow : undefined,
          markerStart: carries(link.backward) ? arrow : undefined,
          data: {
            label,
            state,
            sync: isSync(link),
            twoWay,
            onSelect: () => onSelect({ kind: 'link', id: link.id }),
            route: routes.get(link.id) ?? null,
            at: atOf(link.from, link.to),
          },
        };
      }),
    [links, queues, highlight, selection, onSelect, routes, positions],
  );

  return (
    <ReactFlow
      nodes={displayed}
      edges={edges}
      nodeTypes={nodeTypes}
      edgeTypes={edgeTypes}
      onNodesChange={onNodesChange}
      onNodeClick={(_, node) => {
        if (node.type === 'instance') onSelect({ kind: 'instance', name: (node.data as DisplayData).label });
      }}
      onEdgeClick={(_, edge) => onSelect({ kind: 'link', id: edge.id })}
      onPaneClick={() => onSelect(null)}
      nodesConnectable={false}
      edgesFocusable={false}
      minZoom={0.1}
      proOptions={{ hideAttribution: true }}
    >
      <Background gap={20} color="var(--color-border)" />
      <Controls showInteractive={false} />
    </ReactFlow>
  );
}
