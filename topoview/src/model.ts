// The cluster a page embeds: the JSON form `topoc` writes
// (src/topoc/view/model_json.py), which these types mirror.

export const FORMAT_VERSION = 3;

export interface Sow {
  // The key's wire paths, as the configuration writes them.
  keys: string[];
  // The same paths in field names, when `topoc view` was given the catalog.
  keyNames: string[] | null;
  // `persistent` or `transient`, as written; null if the configuration does not say.
  durability: string | null;
  // The SOW file, for a persistent SOW.
  file: string | null;
  // Where a transient SOW recovers from (`now`, `epoch`).
  recoveryPoint: string | null;
}

// A topic as an instance's configuration reveals it.
export interface HostedTopic {
  name: string;
  messageType: string | null;
  sow: Sow | null;
  journaled: boolean;
}

// Which AMPS queue element a queue is: `Queue`, `GroupLocalQueue`, `LocalQueue`.
export type QueueKind = 'distributed' | 'group_local' | 'local';

// A queue as an instance's configuration declares it.
export interface Queue {
  name: string;
  kind: QueueKind;
  messageType: string | null;
  // `UnderlyingTopic` as written (a name or a pattern); null when the queue
  // reads the topic of its own name.
  underlyingTopic: string | null;
  // The topics on the instance it reads: the underlying topic, those the
  // pattern matches, or the queue's own name.
  sources: string[];
  defaultPublishTarget: string | null;
  initialOwner: string | null;
  domain: string | null;
  // `at-least-once` or `at-most-once`; null if left to AMPS's default.
  semantics: string | null;
  // As written, with wire paths; `*Names` the same in field names when
  // `topoc view` was given the catalog.
  filter: string | null;
  filterNames: string | null;
  priority: string | null;
  priorityNames: string | null;
  barrier: string | null;
  barrierNames: string | null;
  // Every other setting, as written, in configuration order.
  settings: { name: string; value: string }[];
}

// A topic a destination replicates, with its replication settings.
export interface ReplicatedTopic {
  name: string;
  messageType: string | null;
  // The filter as the configuration writes it, with wire paths.
  filter: string | null;
  // The same filter in field names, when `topoc view` was given the catalog.
  filterNames: string | null;
  excludedValidation: string[];
}

export interface Destination {
  name: string | null;
  group: string;
  // `sync` or `async`; null if the configuration leaves AMPS's default.
  syncType: string | null;
  addresses: string[];
  topics: ReplicatedTopic[];
}

// What a replication edge lands on: the one other member of the destination's
// group, the group itself (several members, a failover list), or a group no
// configuration declares.
export type TargetKind = 'instance' | 'group' | 'external';

export interface Edge {
  source: string;
  targetKind: TargetKind;
  target: string;
  members: string[];
  destination: Destination;
}

export interface Instance {
  name: string;
  group: string;
  // The configuration file's name.
  source: string;
  // A queue's own topic is not among them: it is the queue.
  topics: HostedTopic[];
  queues: Queue[];
}

export interface Group {
  name: string;
  members: string[];
}

export interface Cluster {
  formatVersion: number;
  name: string;
  instances: Instance[];
  groups: Group[];
  edges: Edge[];
  warnings: string[];
  // Whether SOW keys and filters have field names (`topoc view --catalog`).
  fieldNames: boolean;
}
