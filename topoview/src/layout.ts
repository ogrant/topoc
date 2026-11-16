// Places the canvas's nodes with ELK — instances, the frames of groups with
// several members (their members inside), and external groups — and routes the
// links between them, left to right from source to target, around the nodes.

import type { Node } from '@xyflow/react';
import ELK, { type ElkExtendedEdge, type ElkNode } from 'elkjs/lib/elk.bundled.js';

import type { Point } from './floating';
import {
  externalGroups,
  externalNodeId,
  framedGroups,
  groupNodeId,
  instanceNodeId,
  type Link,
} from './derive';
import type { Cluster } from './model';

export const INSTANCE_WIDTH = 170;
export const INSTANCE_HEIGHT = 58;
// Room for a frame's label above its members.
const FRAME_PADDING = '[top=40,left=20,bottom=20,right=20]';

export interface NodeData extends Record<string, unknown> {
  label: string;
}

export interface Placement {
  nodes: Node<NodeData>[];
  // Where the layout put each node, in canvas coordinates: a link keeps its
  // route only while both its ends stay there.
  positions: Map<string, Point>;
  // Each routed link's path, start to end, in canvas coordinates. Links
  // between members of one frame are not routed: they join stacked neighbours.
  routes: Map<string, Point[]>;
}

const elk = new ELK();

export async function layout(cluster: Cluster, allLinks: Link[]): Promise<Placement> {
  const framed = framedGroups(cluster);
  const framedMembers = new Set(framed.flatMap((group) => group.members));
  const box = (id: string): ElkNode => ({ id, width: INSTANCE_WIDTH, height: INSTANCE_HEIGHT });

  const children: ElkNode[] = [
    ...framed.map((group) => ({
      id: groupNodeId(group.name),
      // Members far enough apart for the arrows of a mesh between them.
      layoutOptions: { 'elk.padding': FRAME_PADDING, 'elk.spacing.nodeNode': '70' },
      children: group.members.map((member) => box(instanceNodeId(member))),
    })),
    ...cluster.instances
      .filter((instance) => !framedMembers.has(instance.name))
      .map((instance) => box(instanceNodeId(instance.name))),
    ...externalGroups(cluster).map((name) => box(externalNodeId(name))),
  ];

  // Links between members of one frame (a mesh) are left out, so the members
  // stack inside the frame rather than line up across it.
  const frameOf = new Map(
    framed.flatMap((group) => group.members.map((member) => [instanceNodeId(member), group.name] as const)),
  );
  const routed = allLinks.filter(
    (link) => frameOf.get(link.from) === undefined || frameOf.get(link.from) !== frameOf.get(link.to),
  );
  const edges: ElkExtendedEdge[] = routed.map((link) => ({ id: link.id, sources: [link.from], targets: [link.to] }));

  const graph = await elk.layout({
    id: 'root',
    layoutOptions: {
      'elk.algorithm': 'layered',
      'elk.direction': 'RIGHT',
      'elk.hierarchyHandling': 'INCLUDE_CHILDREN',
      'elk.edgeRouting': 'ORTHOGONAL',
      // Edge routes in the whole graph's coordinates, whatever their container.
      'elk.json.edgeCoords': 'ROOT',
      'elk.spacing.edgeNode': '24',
      'elk.spacing.edgeEdge': '20',
      'elk.layered.spacing.edgeNodeBetweenLayers': '24',
      'elk.spacing.nodeNode': '40',
      'elk.layered.spacing.nodeNodeBetweenLayers': '140',
      'elk.padding': '[top=20,left=20,bottom=20,right=20]',
    },
    children,
    edges,
  });

  // Parents come before their children, as xyflow requires; ELK gives child
  // positions relative to their parent, which is what xyflow expects too.
  const nodes: Node<NodeData>[] = [];
  const positions = new Map<string, Point>();
  for (const child of graph.children ?? []) {
    const [kind, ...rest] = child.id.split(':');
    const label = rest.join(':');
    const position = { x: child.x ?? 0, y: child.y ?? 0 };
    positions.set(child.id, position);
    if (kind === 'group') {
      nodes.push({
        id: child.id,
        type: 'frame',
        position,
        data: { label },
        style: { width: child.width, height: child.height },
        selectable: false,
      });
      for (const member of child.children ?? []) {
        positions.set(member.id, { x: position.x + (member.x ?? 0), y: position.y + (member.y ?? 0) });
        nodes.push({
          id: member.id,
          type: 'instance',
          parentId: child.id,
          position: { x: member.x ?? 0, y: member.y ?? 0 },
          data: { label: member.id.slice('instance:'.length) },
        });
      }
    } else {
      nodes.push({ id: child.id, type: kind === 'external' ? 'external' : 'instance', position, data: { label } });
    }
  }

  const routes = new Map<string, Point[]>();
  for (const edge of graph.edges ?? []) {
    const section = edge.sections?.[0];
    if (section !== undefined) {
      routes.set(edge.id, [section.startPoint, ...(section.bendPoints ?? []), section.endPoint]);
    }
  }
  return { nodes, positions, routes };
}
