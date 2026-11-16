// What the page shows, derived from the cluster: the canvas's nodes and links,
// the topic index, and what a selection highlights. Pure functions, so the
// rules are tested without rendering anything.

import type { Cluster, Edge, HostedTopic, Instance, Queue, ReplicatedTopic } from './model';

// The order names are listed in: alphabetical, with numbers by value
// (`Quoting.LN2` before `Quoting.LN10`), whatever the configuration order.
export function compareNames(a: string, b: string): number {
  return a.localeCompare(b, undefined, { numeric: true });
}

// ---- nodes -----------------------------------------------------------------

export type NodeId = string;

export const instanceNodeId = (name: string): NodeId => `instance:${name}`;
export const groupNodeId = (name: string): NodeId => `group:${name}`;
export const externalNodeId = (name: string): NodeId => `external:${name}`;

// Groups drawn as a frame around their members: those with several members.
export function framedGroups(cluster: Cluster): Cluster['groups'] {
  return cluster.groups.filter((group) => group.members.length > 1);
}

// Groups replicated to that no configuration declares, by name.
export function externalGroups(cluster: Cluster): string[] {
  const names = new Set(
    cluster.edges.filter((edge) => edge.targetKind === 'external').map((edge) => edge.target),
  );
  return [...names].sort();
}

// The node an edge lands on.
export function targetNodeId(edge: Edge): NodeId {
  switch (edge.targetKind) {
    case 'instance':
      return instanceNodeId(edge.target);
    case 'group':
      return groupNodeId(edge.target);
    case 'external':
      return externalNodeId(edge.target);
  }
}

// ---- links -----------------------------------------------------------------

// What the canvas draws between two nodes: every edge from `from` to `to`
// (forward), and every edge back from `to` to `from` (backward), so a mesh
// pair is one two-way link rather than two crossing ones.
export interface Link {
  id: string;
  from: NodeId;
  to: NodeId;
  forward: Edge[];
  backward: Edge[];
}

export function links(cluster: Cluster): Link[] {
  const byPair = new Map<string, Link>();
  const result: Link[] = [];
  for (const edge of cluster.edges) {
    const from = instanceNodeId(edge.source);
    const to = targetNodeId(edge);
    const back = byPair.get(`${to}|${from}`);
    if (back !== undefined) {
      back.backward.push(edge);
      continue;
    }
    const id = `${from}|${to}`;
    let link = byPair.get(id);
    if (link === undefined) {
      link = { id, from, to, forward: [], backward: [] };
      byPair.set(id, link);
      result.push(link);
    }
    link.forward.push(edge);
  }
  return result;
}

export function linkEdges(link: Link): Edge[] {
  return [...link.forward, ...link.backward];
}

// The queues of the cluster, by name. A queue's own topic is replicated like
// any other, so destinations list queue names among their topics.
export function queueNames(cluster: Cluster): Set<string> {
  return new Set(cluster.instances.flatMap((instance) => instance.queues.map((queue) => queue.name)));
}

// The distinct topics a link carries, by name, leaving out `queues` (which
// `linkQueues` lists).
export function linkTopics(link: Link, queues: Set<string>): string[] {
  const names = new Set(linkEdges(link).flatMap((edge) => edge.destination.topics.map((t) => t.name)));
  return [...names].filter((name) => !queues.has(name)).sort();
}

// The distinct queues a link carries, by name.
export function linkQueues(link: Link, queues: Set<string>): string[] {
  const names = new Set(linkEdges(link).flatMap((edge) => edge.destination.topics.map((t) => t.name)));
  return [...names].filter((name) => queues.has(name)).sort();
}

// Whether every edge of a link replicates synchronously.
export function isSync(link: Link): boolean {
  return linkEdges(link).every((edge) => edge.destination.syncType === 'sync');
}

// ---- topics ----------------------------------------------------------------

export interface TopicHost {
  instance: string;
  topic: HostedTopic;
}

export interface TopicReplication {
  edge: Edge;
  topic: ReplicatedTopic;
}

export interface TopicInfo {
  name: string;
  hosts: TopicHost[];
  replications: TopicReplication[];
}

// Every topic in the cluster, by name: where it lives, by instance, and how it
// replicates, by source then target.
export function topicIndex(cluster: Cluster): TopicInfo[] {
  const queues = queueNames(cluster);
  const byName = new Map<string, TopicInfo>();
  const info = (name: string): TopicInfo => {
    let found = byName.get(name);
    if (found === undefined) {
      found = { name, hosts: [], replications: [] };
      byName.set(name, found);
    }
    return found;
  };
  for (const instance of cluster.instances) {
    for (const topic of instance.topics) info(topic.name).hosts.push({ instance: instance.name, topic });
  }
  for (const edge of cluster.edges) {
    for (const topic of edge.destination.topics) {
      if (!queues.has(topic.name)) info(topic.name).replications.push({ edge, topic });
    }
  }
  for (const topic of byName.values()) {
    topic.hosts.sort((a, b) => compareNames(a.instance, b.instance));
    topic.replications.sort(
      (a, b) => compareNames(a.edge.source, b.edge.source) || compareNames(a.edge.target, b.edge.target),
    );
  }
  return [...byName.values()].sort((a, b) => compareNames(a.name, b.name));
}

// ---- queues ----------------------------------------------------------------

export interface QueueHost {
  instance: string;
  queue: Queue;
}

export interface QueueInfo {
  name: string;
  // By instance.
  hosts: QueueHost[];
  // The edges replicating the queue's own topic, by source then target.
  replications: TopicReplication[];
}

// Every queue in the cluster, by name: where it is held and how it replicates.
export function queueIndex(cluster: Cluster): QueueInfo[] {
  const byName = new Map<string, QueueInfo>();
  for (const instance of cluster.instances) {
    for (const queue of instance.queues) {
      let info = byName.get(queue.name);
      if (info === undefined) {
        info = { name: queue.name, hosts: [], replications: [] };
        byName.set(queue.name, info);
      }
      info.hosts.push({ instance: instance.name, queue });
    }
  }
  for (const edge of cluster.edges) {
    for (const topic of edge.destination.topics) byName.get(topic.name)?.replications.push({ edge, topic });
  }
  for (const queue of byName.values()) {
    queue.hosts.sort((a, b) => compareNames(a.instance, b.instance));
    queue.replications.sort(
      (a, b) => compareNames(a.edge.source, b.edge.source) || compareNames(a.edge.target, b.edge.target),
    );
  }
  return [...byName.values()].sort((a, b) => compareNames(a.name, b.name));
}

// The queues reading `topic`, by name.
export function queuesReading(cluster: Cluster, topic: string): string[] {
  const names = new Set(
    cluster.instances.flatMap((instance) =>
      instance.queues.filter((queue) => queue.name !== topic && queue.sources.includes(topic)).map((q) => q.name),
    ),
  );
  return [...names].sort(compareNames);
}

// Whether an edge lands on `instance`: directly, or through its group's frame.
export function edgeReaches(edge: Edge, instance: Instance): boolean {
  return edge.targetKind === 'external' ? false : edge.members.includes(instance.name);
}

// ---- selection -------------------------------------------------------------

export type Selection =
  | { kind: 'topic'; name: string }
  | { kind: 'queue'; name: string }
  | { kind: 'instance'; name: string }
  | { kind: 'link'; id: string }
  | null;

export interface Highlight {
  // Nothing selected: nothing is highlighted, and nothing dimmed.
  active: boolean;
  nodes: Set<NodeId>;
  links: Set<string>;
  // A label replacing a highlighted link's default one (a topic's filter).
  linkLabels: Map<string, string>;
}

export function highlight(cluster: Cluster, allLinks: Link[], selection: Selection): Highlight {
  const result: Highlight = { active: selection !== null, nodes: new Set(), links: new Set(), linkLabels: new Map() };
  if (selection === null) return result;

  switch (selection.kind) {
    case 'topic': {
      for (const instance of cluster.instances) {
        if (instance.topics.some((topic) => topic.name === selection.name)) {
          result.nodes.add(instanceNodeId(instance.name));
        }
      }
      for (const link of allLinks) {
        const carried = linkEdges(link).flatMap((edge) =>
          edge.destination.topics.filter((topic) => topic.name === selection.name),
        );
        if (carried.length === 0) continue;
        result.links.add(link.id);
        result.nodes.add(link.to);
        const filters = [
          ...new Set(carried.map((topic) => topic.filterNames ?? topic.filter).filter((f): f is string => f !== null)),
        ];
        if (filters.length > 0) result.linkLabels.set(link.id, filters.join(' | '));
      }
      return result;
    }
    case 'queue': {
      for (const instance of cluster.instances) {
        if (instance.queues.some((queue) => queue.name === selection.name)) {
          result.nodes.add(instanceNodeId(instance.name));
        }
      }
      for (const link of allLinks) {
        if (!linkEdges(link).some((edge) => edge.destination.topics.some((t) => t.name === selection.name))) continue;
        result.links.add(link.id);
        result.nodes.add(link.to);
      }
      return result;
    }
    case 'instance': {
      const self = instanceNodeId(selection.name);
      const instance = cluster.instances.find((candidate) => candidate.name === selection.name);
      result.nodes.add(self);
      for (const link of allLinks) {
        const touches =
          link.from === self ||
          link.to === self ||
          (instance !== undefined && linkEdges(link).some((edge) => edgeReaches(edge, instance)));
        if (!touches) continue;
        result.links.add(link.id);
        result.nodes.add(link.from);
        result.nodes.add(link.to);
      }
      return result;
    }
    case 'link': {
      const link = allLinks.find((candidate) => candidate.id === selection.id);
      if (link !== undefined) {
        result.links.add(link.id);
        result.nodes.add(link.from);
        result.nodes.add(link.to);
      }
      return result;
    }
  }
}

// ---- an instance's topics --------------------------------------------------

export interface InstanceTopic {
  name: string;
  hosted: HostedTopic | null;
  // Where the instance replicates the topic to (edge targets).
  to: string[];
  // Which instances replicate the topic to it.
  from: string[];
}

// The topics on an instance, with where each replicates to and from. With
// `kind` 'queue', the same for the instance's queues.
export function instanceTopics(cluster: Cluster, instance: Instance, kind: 'topic' | 'queue'): InstanceTopic[] {
  const queues = queueNames(cluster);
  const wanted = (name: string) => queues.has(name) === (kind === 'queue');
  const byName = new Map<string, InstanceTopic>();
  const entry = (name: string): InstanceTopic => {
    let found = byName.get(name);
    if (found === undefined) {
      found = { name, hosted: null, to: [], from: [] };
      byName.set(name, found);
    }
    return found;
  };
  if (kind === 'topic') for (const topic of instance.topics) entry(topic.name).hosted = topic;
  else for (const queue of instance.queues) entry(queue.name);
  for (const edge of cluster.edges) {
    for (const topic of edge.destination.topics) {
      if (!wanted(topic.name)) continue;
      if (edge.source === instance.name) entry(topic.name).to.push(edge.target);
      else if (edgeReaches(edge, instance)) entry(topic.name).from.push(edge.source);
    }
  }
  return [...byName.values()].sort((a, b) => a.name.localeCompare(b.name));
}

// A peer an instance exchanges a topic with, and in which direction: it
// replicates the topic to the peer (`to`), receives it from the peer (`from`),
// or both (a mesh partner).
export interface TopicPeer {
  name: string;
  direction: 'to' | 'from' | 'both';
}

// An instance topic's peers: those it replicates to first, then those it only
// receives from, each by name (not configuration order, which is arbitrary). A
// peer in both directions appears once, with those replicated to.
export function topicPeers(topic: InstanceTopic): TopicPeer[] {
  const byName = (a: TopicPeer, b: TopicPeer) => compareNames(a.name, b.name);
  const to = [...new Set(topic.to)].map(
    (name): TopicPeer => ({ name, direction: topic.from.includes(name) ? 'both' : 'to' }),
  );
  const from = [...new Set(topic.from)]
    .filter((name) => !topic.to.includes(name))
    .map((name): TopicPeer => ({ name, direction: 'from' }));
  return [...to.sort(byName), ...from.sort(byName)];
}

// A SOW key a topic is materialised with, and the instances using it.
export interface SowKey {
  // The key's wire paths, in order (several for a composite key).
  paths: string[];
  // The same paths in field names, if known.
  names: string[] | null;
  // By name.
  instances: string[];
}

// The SOW keys a topic is materialised with across the cluster: normally one,
// shared by every instance holding a SOW of it; several if instances disagree.
export function topicSowKeys(topic: TopicInfo): SowKey[] {
  const keys: SowKey[] = [];
  for (const host of topic.hosts) {
    const paths = host.topic.sow?.keys ?? [];
    if (paths.length === 0) continue;
    const same = keys.find((key) => key.paths.join('\0') === paths.join('\0'));
    if (same === undefined) keys.push({ paths, names: host.topic.sow?.keyNames ?? null, instances: [host.instance] });
    else same.instances.push(host.instance);
  }
  return keys;
}
