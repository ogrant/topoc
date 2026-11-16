// Floating edges: a link runs between the borders of its two nodes, along the
// line joining their centres, so it stays right whichever side a node sits on
// and however nodes are dragged.

import type { InternalNode } from '@xyflow/react';

export interface Point {
  x: number;
  y: number;
}

interface Box {
  x: number;
  y: number;
  width: number;
  height: number;
}

function box(node: InternalNode): Box {
  const { x, y } = node.internals.positionAbsolute;
  return { x, y, width: node.measured.width ?? 0, height: node.measured.height ?? 0 };
}

// Where the line from `from`'s centre towards `to`'s centre leaves `from`.
function exit(from: Box, to: Box): Point {
  const cx = from.x + from.width / 2;
  const cy = from.y + from.height / 2;
  const dx = to.x + to.width / 2 - cx;
  const dy = to.y + to.height / 2 - cy;
  if (dx === 0 && dy === 0) return { x: cx, y: cy };
  const scale = Math.min(
    dx === 0 ? Infinity : from.width / 2 / Math.abs(dx),
    dy === 0 ? Infinity : from.height / 2 / Math.abs(dy),
  );
  return { x: cx + dx * scale, y: cy + dy * scale };
}

// The end points of a link between two nodes.
export function floatingEnds(source: InternalNode, target: InternalNode): [Point, Point] {
  const a = box(source);
  const b = box(target);
  return [exit(a, b), exit(b, a)];
}

// An orthogonal route as an SVG path, its corners rounded.
export function routePath(points: Point[], radius = 8): string {
  const [first, ...rest] = points;
  if (first === undefined) return '';
  let path = `M ${first.x} ${first.y}`;
  for (let ix = 0; ix < rest.length; ix += 1) {
    const corner = rest[ix]!;
    const next = rest[ix + 1];
    if (next === undefined) {
      path += ` L ${corner.x} ${corner.y}`;
      break;
    }
    const previous = ix === 0 ? first : rest[ix - 1]!;
    const r = Math.min(radius, distance(previous, corner) / 2, distance(corner, next) / 2);
    const before = towards(corner, previous, r);
    const after = towards(corner, next, r);
    path += ` L ${before.x} ${before.y} Q ${corner.x} ${corner.y} ${after.x} ${after.y}`;
  }
  return path;
}

// Where a route's label goes: halfway along its first segment when that is
// long enough, which keeps labels near their source, where parallel routes
// into one target are still apart; otherwise halfway along the route.
export function labelPoint(points: Point[]): Point {
  const [start, next] = points;
  if (start !== undefined && next !== undefined && distance(start, next) >= 100) {
    return towards(start, next, distance(start, next) / 2);
  }
  return midpoint(points);
}

// The point halfway along a route.
export function midpoint(points: Point[]): Point {
  const lengths = points.slice(1).map((point, ix) => distance(points[ix]!, point));
  let remaining = lengths.reduce((sum, length) => sum + length, 0) / 2;
  for (let ix = 0; ix < lengths.length; ix += 1) {
    const length = lengths[ix]!;
    if (remaining <= length && length > 0) return towards(points[ix]!, points[ix + 1]!, remaining);
    remaining -= length;
  }
  return points[0] ?? { x: 0, y: 0 };
}

function distance(a: Point, b: Point): number {
  return Math.hypot(b.x - a.x, b.y - a.y);
}

// The point `length` along the way from `from` to `to`.
function towards(from: Point, to: Point, length: number): Point {
  const total = distance(from, to);
  if (total === 0) return from;
  return { x: from.x + ((to.x - from.x) * length) / total, y: from.y + ((to.y - from.y) * length) / total };
}
