// The page's display rules, tested on the reference cluster (fixtures/,
// generated from testdata/expected/ by fixtures/generate.py): node ids, links
// merged per pair of nodes with mesh pairs two-way, the topic index, what each
// kind of selection highlights, and an instance's topics.

import { describe, expect, it } from 'vitest';

import reference from '../fixtures/reference.cluster.json';
import {
  externalGroups,
  framedGroups,
  highlight,
  instanceTopics,
  isSync,
  linkQueues,
  linkTopics,
  links,
  queueIndex,
  queueNames,
  queuesReading,
  topicIndex,
  topicPeers,
  topicSowKeys,
} from './derive';
import type { Cluster } from './model';

const cluster = reference as Cluster;
const allLinks = links(cluster);

describe('nodes', () => {
  it('frames only groups with several members', () => {
    expect(framedGroups(cluster).map((group) => group.name)).toEqual(['Infra']);
  });

  it('finds no external group in a closed cluster', () => {
    expect(externalGroups(cluster)).toEqual([]);
  });
});

describe('links', () => {
  it('merges a mesh pair into one two-way link and keeps one link per pair', () => {
    expect(allLinks.map((link) => [link.id, link.forward.length, link.backward.length])).toEqual([
      ['instance:Infra1|instance:Infra2', 1, 1],
      ['instance:Infra1|instance:Quoting.LN', 1, 0],
      ['instance:Infra1|instance:Quoting.NY', 1, 0],
      ['instance:Infra2|instance:Quoting.LN', 1, 0],
      ['instance:Infra2|instance:Quoting.NY', 1, 0],
      ['instance:Quoting.LN|group:Infra', 1, 0],
      ['instance:Quoting.NY|group:Infra', 1, 0],
    ]);
  });

  it('tells sync links from async ones and lists the topics they carry', () => {
    const [mesh, toLondon] = allLinks;
    expect(isSync(mesh!)).toBe(true);
    expect(isSync(toLondon!)).toBe(false);
    expect(linkTopics(toLondon!, new Set())).toEqual(['microprices', 'refdata', 'tvgrids']);
  });
});

describe('topic index', () => {
  it('sorts hosts by instance and replications by source then target, numbers by value', () => {
    const edge = (source: string, target: string) => ({
      source,
      targetKind: 'instance' as const,
      target,
      members: [target],
      destination: { name: null, group: target, syncType: 'async', addresses: [], topics: [{ name: 't', messageType: null, filter: null, filterNames: null, excludedValidation: [] }] },
    });
    const hosted = { name: 't', messageType: null, sow: null, journaled: true };
    const unordered: Cluster = {
      formatVersion: 3,
      name: 'c',
      instances: ['Q.LN10', 'Pricing1', 'Q.LN2'].map((name) => ({ name, group: name, source: `${name}.xml`, topics: [hosted], queues: [] })),
      groups: [],
      edges: [edge('Pricing1', 'Q.LN10'), edge('Pricing1', 'Q.LN2'), edge('Pricing0', 'Q.LN2')],
      warnings: [],
      fieldNames: false,
    };
    const [topic] = topicIndex(unordered);
    expect(topic!.hosts.map((host) => host.instance)).toEqual(['Pricing1', 'Q.LN2', 'Q.LN10']);
    expect(topic!.replications.map(({ edge }) => `${edge.source} -> ${edge.target}`)).toEqual([
      'Pricing0 -> Q.LN2',
      'Pricing1 -> Q.LN2',
      'Pricing1 -> Q.LN10',
    ]);
  });

  it('lists every topic with where it lives and how it replicates', () => {
    const index = topicIndex(cluster);
    expect(index.map((topic) => topic.name)).toEqual([
      'microprices',
      'orders/LN',
      'orders/NY',
      'ownership',
      'quotes',
      'refdata',
      'signals/arbitrage',
      'signals/momentum',
      'tvgrids',
    ]);
    const tvgrids = index.find((topic) => topic.name === 'tvgrids')!;
    expect(tvgrids.replications.map(({ edge }) => `${edge.source} -> ${edge.target}`)).toEqual([
      'Infra1 -> Infra2',
      'Infra1 -> Quoting.LN',
      'Infra1 -> Quoting.NY',
      'Infra2 -> Infra1',
      'Infra2 -> Quoting.LN',
      'Infra2 -> Quoting.NY',
    ]);
    const quotes = index.find((topic) => topic.name === 'quotes')!;
    expect(quotes.hosts.map((host) => host.instance)).toEqual(['Quoting.LN', 'Quoting.NY']);
    expect(quotes.replications).toEqual([]);
  });
});

describe('highlight', () => {
  it('highlights nothing and dims nothing without a selection', () => {
    expect(highlight(cluster, allLinks, null).active).toBe(false);
  });

  it('highlights a topic where it lives and on the links carrying it, labelled by filter', () => {
    const result = highlight(cluster, allLinks, { kind: 'topic', name: 'tvgrids' });
    expect([...result.nodes].sort()).toEqual([
      'instance:Infra1',
      'instance:Infra2',
      'instance:Quoting.LN',
      'instance:Quoting.NY',
    ]);
    expect([...result.links]).toEqual([
      'instance:Infra1|instance:Infra2',
      'instance:Infra1|instance:Quoting.LN',
      'instance:Infra1|instance:Quoting.NY',
      'instance:Infra2|instance:Quoting.LN',
      'instance:Infra2|instance:Quoting.NY',
    ]);
    expect(result.linkLabels.get('instance:Infra1|instance:Quoting.LN')).toBe("/owner LIKE 'LN/.*'");
    expect(result.linkLabels.has('instance:Infra1|instance:Infra2')).toBe(false);
  });

  it('highlights the group frame a topic replicates to', () => {
    const result = highlight(cluster, allLinks, { kind: 'topic', name: 'orders/LN' });
    expect(result.nodes.has('group:Infra')).toBe(true);
    expect(result.links.has('instance:Quoting.LN|group:Infra')).toBe(true);
  });

  it('highlights an instance and its peers, including replication to its group', () => {
    const result = highlight(cluster, allLinks, { kind: 'instance', name: 'Infra2' });
    expect(result.links.has('instance:Quoting.LN|group:Infra')).toBe(true);
    expect(result.nodes.has('instance:Quoting.LN')).toBe(true);
    expect(result.nodes.has('instance:Admin')).toBe(false);
  });

  it('highlights a link and its two ends', () => {
    const result = highlight(cluster, allLinks, { kind: 'link', id: 'instance:Quoting.LN|group:Infra' });
    expect([...result.nodes]).toEqual(['instance:Quoting.LN', 'group:Infra']);
  });
});

describe('topic SOW keys', () => {
  const index = topicIndex(cluster);
  const keysOf = (name: string) => topicSowKeys(index.find((topic) => topic.name === name)!);

  it('gives the key shared by every instance holding a SOW of the topic', () => {
    expect(keysOf('tvgrids')).toEqual([{ paths: ['/isin'], names: ['/isin'], instances: ['Infra1', 'Infra2'] }]);
    expect(keysOf('quotes')).toEqual([{ paths: ['/isin'], names: ['/isin'], instances: ['Quoting.LN', 'Quoting.NY'] }]);
  });

  it('gives the key in field names beside its wire paths', () => {
    expect(keysOf('refdata')).toEqual([{ paths: ['/s'], names: ['/symbol'], instances: ['Infra1', 'Infra2'] }]);
  });

  it('gives no key for a topic without a SOW', () => {
    expect(keysOf('signals/momentum')).toEqual([]);
  });

  it('lists each key when instances disagree', () => {
    const sow = (keys: string[]) => ({ keys, keyNames: null, durability: 'transient', file: null, recoveryPoint: 'now' });
    const host = (instance: string, keys: string[]) => ({
      instance,
      topic: { name: 't', messageType: null, sow: sow(keys), journaled: false },
    });
    expect(
      topicSowKeys({ name: 't', hosts: [host('A', ['/a', '/b']), host('B', ['/a']), host('C', ['/a', '/b'])], replications: [] }),
    ).toEqual([
      { paths: ['/a', '/b'], names: null, instances: ['A', 'C'] },
      { paths: ['/a'], names: null, instances: ['B'] },
    ]);
  });
});

describe('topic peers', () => {
  const topicsOf = (name: string) =>
    instanceTopics(cluster, cluster.instances.find((instance) => instance.name === name)!, 'topic');

  it('lists peers replicated to, then peers received from, with a mesh partner once', () => {
    const microprices = topicsOf('Infra1').find((topic) => topic.name === 'microprices')!;
    expect(topicPeers(microprices)).toEqual([
      { name: 'Infra2', direction: 'both' },
      { name: 'Quoting.LN', direction: 'to' },
      { name: 'Quoting.NY', direction: 'to' },
    ]);
    const tvgrids = topicsOf('Quoting.LN').find((topic) => topic.name === 'tvgrids')!;
    expect(topicPeers(tvgrids)).toEqual([
      { name: 'Infra1', direction: 'from' },
      { name: 'Infra2', direction: 'from' },
    ]);
  });

  it('sorts each direction by name, whatever the configuration order', () => {
    const topic = { name: 't', hosted: null, to: ['Quoting.NY1', 'Quoting.LN10', 'Quoting.LN2'], from: ['Z', 'A'] };
    expect(topicPeers(topic)).toEqual([
      { name: 'Quoting.LN2', direction: 'to' },
      { name: 'Quoting.LN10', direction: 'to' },
      { name: 'Quoting.NY1', direction: 'to' },
      { name: 'A', direction: 'from' },
      { name: 'Z', direction: 'from' },
    ]);
  });

  it('names a failover destination by its group', () => {
    const orders = topicsOf('Quoting.LN').find((topic) => topic.name === 'orders/LN')!;
    expect(topicPeers(orders)).toEqual([{ name: 'Infra', direction: 'to' }]);
  });
});

describe('instance topics', () => {
  it('lists what an instance hosts, sends and receives', () => {
    const london = cluster.instances.find((instance) => instance.name === 'Quoting.LN')!;
    const topics = instanceTopics(cluster, london, 'topic');
    const orders = topics.find((topic) => topic.name === 'orders/LN')!;
    expect(orders.to).toEqual(['Infra']);
    const tvgrids = topics.find((topic) => topic.name === 'tvgrids')!;
    expect(tvgrids.from).toEqual(['Infra1', 'Infra2']);
    expect(topics.find((topic) => topic.name === 'quotes')!.hosted?.sow?.durability).toBe('transient');
  });
});

describe('queues', () => {
  const queues = queueNames(cluster);

  it('indexes each queue with where it is held and how it replicates', () => {
    const index = queueIndex(cluster);
    expect(index.map((queue) => queue.name)).toEqual(['work/orders', 'work/quotes']);
    const [orders, quotes] = index;
    expect(orders!.hosts.map((host) => host.instance)).toEqual(['Infra1', 'Infra2']);
    expect(orders!.hosts[0]!.queue.sources).toEqual(['orders/LN', 'orders/NY']);
    expect(orders!.replications.map(({ edge }) => `${edge.source} -> ${edge.target}`)).toEqual([
      'Infra1 -> Infra2',
      'Infra2 -> Infra1',
    ]);
    expect(quotes!.hosts.map((host) => host.instance)).toEqual(['Quoting.LN', 'Quoting.NY']);
    expect(quotes!.replications).toEqual([]);
  });

  it('keeps queues out of the topic index and the topics a link carries', () => {
    expect(topicIndex(cluster).map((topic) => topic.name)).not.toContain('work/orders');
    const mesh = allLinks.find((link) => link.id === 'instance:Infra1|instance:Infra2')!;
    expect(linkTopics(mesh, queues)).not.toContain('work/orders');
    expect(linkQueues(mesh, queues)).toEqual(['work/orders']);
  });

  it('names the queues reading a topic', () => {
    expect(queuesReading(cluster, 'orders/LN')).toEqual(['work/orders']);
    expect(queuesReading(cluster, 'quotes')).toEqual(['work/quotes']);
    expect(queuesReading(cluster, 'refdata')).toEqual([]);
  });

  it('highlights the instances holding a queue and the links carrying it', () => {
    const result = highlight(cluster, allLinks, { kind: 'queue', name: 'work/orders' });
    expect([...result.nodes].sort()).toEqual(['instance:Infra1', 'instance:Infra2']);
    expect([...result.links]).toEqual(['instance:Infra1|instance:Infra2']);
  });

  it("lists an instance's queues with their peers", () => {
    const infra1 = cluster.instances.find((instance) => instance.name === 'Infra1')!;
    const [orders] = instanceTopics(cluster, infra1, 'queue');
    expect(orders!.name).toBe('work/orders');
    expect(topicPeers(orders!)).toEqual([{ name: 'Infra2', direction: 'both' }]);
    expect(instanceTopics(cluster, infra1, 'topic').map((t) => t.name)).not.toContain('work/orders');
  });
});
