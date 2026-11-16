import type { ReactNode } from 'react';

import {
  instanceTopics,
  queuesReading,
  topicPeers,
  topicSowKeys,
  type InstanceTopic,
  type Link,
  type QueueInfo,
  type Selection,
  type TopicInfo,
} from '../derive';
import type { Cluster, Edge, HostedTopic, Queue, ReplicatedTopic, Sow } from '../model';
import styles from './DetailsPanel.module.css';
import { holding, Pill } from './Pill';

interface Props {
  cluster: Cluster;
  topics: TopicInfo[];
  queues: QueueInfo[];
  links: Link[];
  selection: Selection;
  onSelect: (selection: Selection) => void;
}

export function DetailsPanel(props: Props) {
  return <aside className={styles.panel}>{content(props)}</aside>;
}

function content({ cluster, topics, queues, links, selection, onSelect }: Props): ReactNode {
  const pickTopic = (name: string) => onSelect({ kind: 'topic', name });
  const pickQueue = (name: string) => onSelect({ kind: 'queue', name });
  const pickInstance = (name: string) => onSelect({ kind: 'instance', name });
  const pick: Pickers = { topic: pickTopic, queue: pickQueue, instance: pickInstance };
  switch (selection?.kind) {
    case undefined:
      return <Overview cluster={cluster} />;
    case 'topic': {
      const topic = topics.find((candidate) => candidate.name === selection.name);
      return topic === undefined ? <Overview cluster={cluster} /> : <TopicDetails cluster={cluster} topic={topic} pick={pick} />;
    }
    case 'queue': {
      const queue = queues.find((candidate) => candidate.name === selection.name);
      return queue === undefined ? (
        <Overview cluster={cluster} />
      ) : (
        <QueueDetails queue={queue} topics={topics} pick={pick} />
      );
    }
    case 'instance': {
      const instance = cluster.instances.find((candidate) => candidate.name === selection.name);
      if (instance === undefined) return <Overview cluster={cluster} />;
      const group = cluster.groups.find((candidate) => candidate.name === instance.group);
      return (
        <>
          <Heading kind="Instance" title={instance.name} />
          <Field label="Group">
            {instance.group}
            {group !== undefined && group.members.length > 1 && (
              <span className={styles.text}> · with {group.members.filter((m) => m !== instance.name).join(', ')}</span>
            )}
          </Field>
          <Field label="Configuration">
            <code>{instance.source}</code>
          </Field>
          <Field label={`Topics (${instanceTopics(cluster, instance, 'topic').length})`}>
            <ul className={styles.list}>
              {instanceTopics(cluster, instance, 'topic').map((topic) => (
                <li key={topic.name} className={styles.card}>
                  <button className={styles.link} onClick={() => pickTopic(topic.name)}>
                    {topic.name}
                  </button>
                  {topic.hosted !== null && <HostedTags topic={topic.hosted} />}
                  {topic.hosted?.sow != null && <SowRows sow={topic.hosted.sow} showKey={true} />}
                  <Peers cluster={cluster} topic={topic} onPickInstance={pickInstance} />
                </li>
              ))}
            </ul>
            {instance.topics.length === 0 && <NoTopicsNote />}
          </Field>
          {instance.queues.length > 0 && (
            <Field label={`Queues (${instance.queues.length})`}>
              <ul className={styles.list}>
                {instanceTopics(cluster, instance, 'queue').map((entry) => {
                  const queue = instance.queues.find((candidate) => candidate.name === entry.name)!;
                  return (
                    <li key={entry.name} className={styles.card}>
                      <button className={styles.link} onClick={() => pickQueue(entry.name)}>
                        {entry.name}
                      </button>
                      <QueueTags queue={queue} />
                      <Peers cluster={cluster} topic={entry} onPickInstance={pickInstance} />
                    </li>
                  );
                })}
              </ul>
            </Field>
          )}
        </>
      );
    }
    case 'link': {
      const link = links.find((candidate) => candidate.id === selection.id);
      if (link === undefined) return <Overview cluster={cluster} />;
      return (
        <>
          <Heading kind="Replication" title={`${nodeName(link.from)} ${link.backward.length > 0 ? '⇄' : '→'} ${nodeName(link.to)}`} />
          {[...link.forward, ...link.backward].map((edge, ix) => (
            <EdgeCard key={ix} edge={edge} onPickTopic={pickTopic} />
          ))}
        </>
      );
    }
  }
}

function Overview({ cluster }: { cluster: Cluster }) {
  return (
    <>
      <Heading kind="Cluster" title={cluster.name} />
      <Field label="How to read it">
        <p className={styles.text}>
          Each box is an AMPS instance; dashed frames gather the members of a replication group. Arrows are
          replication destinations: solid when synchronous, dashed when asynchronous, two-headed between
          instances replicating to each other. An arrow to a frame is a failover list, delivered to whichever
          member is up.
        </p>
        <p className={styles.text}>Pick a topic or an instance on the left, or click an arrow.</p>
      </Field>
      <Field label="What is shown">
        <p className={styles.text}>
          The cluster is read from its AMPS configuration files. A configuration names the topics it keeps in a
          SOW, journals or replicates; a plain pub/sub topic leaves no trace in it, so it cannot appear here.
        </p>
      </Field>
      {!cluster.fieldNames && (
        <Field label="Field names">
          <p className={styles.text}>
            SOW keys and filters show the wire names the configurations use. Generate the page with{' '}
            <code>--catalog</code> to see field names instead.
          </p>
        </Field>
      )}
      {cluster.warnings.length > 0 && (
        <Field label={`Warnings (${cluster.warnings.length})`}>
          {cluster.warnings.map((warning) => (
            <p key={warning} className={styles.warning}>
              {warning}
            </p>
          ))}
        </Field>
      )}
    </>
  );
}

interface Pickers {
  topic: (name: string) => void;
  queue: (name: string) => void;
  instance: (name: string) => void;
}

function TopicDetails({ cluster, topic, pick }: { cluster: Cluster; topic: TopicInfo; pick: Pickers }) {
  const onPickInstance = pick.instance;
  const sowKeys = topicSowKeys(topic);
  const readBy = queuesReading(cluster, topic.name);
  const messageTypes = [
    ...new Set(
      [...topic.hosts.map((host) => host.topic.messageType), ...topic.replications.map((r) => r.topic.messageType)].filter(
        (type): type is string => type !== null,
      ),
    ),
  ];
  return (
    <>
      <Heading kind="Topic" title={topic.name} />
      {messageTypes.length > 0 && <Field label="Message type">{messageTypes.join(', ')}</Field>}
      {sowKeys.length === 1 && (
        <Field label="SOW key">
          <KeyPaths wire={sowKeys[0]!.paths} names={sowKeys[0]!.names} />
        </Field>
      )}
      {sowKeys.length > 1 && (
        <Field label="SOW keys (instances disagree)">
          {sowKeys.map((key) => (
            <div key={key.paths.join(',')} className={styles.keyLine}>
              <KeyPaths wire={key.paths} names={key.names} />
              <span className={styles.text}> on {key.instances.join(', ')}</span>
            </div>
          ))}
        </Field>
      )}
      <Field label={`Lives on (${topic.hosts.length})`}>
        <ul className={styles.list}>
          {topic.hosts.map(({ instance, topic: hosted }) => (
            <li key={instance} className={styles.card}>
              <button className={styles.link} onClick={() => onPickInstance(instance)}>
                {instance}
              </button>
              <HostedTags topic={hosted} />
              {/* A key shared by every instance is shown once, above. */}
              {hosted.sow !== null && <SowRows sow={hosted.sow} showKey={sowKeys.length > 1} />}
            </li>
          ))}
        </ul>
      </Field>
      {readBy.length > 0 && (
        <Field label={`Read by queues (${readBy.length})`}>
          <ul className={styles.list}>
            {readBy.map((name) => (
              <li key={name}>
                <button className={styles.link} onClick={() => pick.queue(name)}>
                  {name}
                </button>
              </li>
            ))}
          </ul>
        </Field>
      )}
      <Field label={`Replication (${topic.replications.length})`}>
        {topic.replications.length === 0 && <p className={styles.empty}>Not replicated.</p>}
        <ul className={styles.list}>
          {topic.replications.map(({ edge, topic: replicated }, ix) => (
            <li key={ix} className={styles.card}>
              <div className={styles.cardTitle}>
                {edge.source} → {edge.target}
              </div>
              <EdgeRows edge={edge} />
              <ReplicatedRows topic={replicated} />
            </li>
          ))}
        </ul>
      </Field>
    </>
  );
}

const QUEUE_KINDS = {
  distributed: 'distributed queue',
  group_local: 'group-local queue',
  local: 'local queue',
} as const;

function QueueTags({ queue }: { queue: Queue }) {
  return (
    <div className={styles.tags}>
      <Pill kind="queue">{QUEUE_KINDS[queue.kind]}</Pill>
      {queue.semantics !== null && <Pill kind="neutral">{queue.semantics}</Pill>}
      {queue.messageType !== null && <Pill kind="neutral">{queue.messageType}</Pill>}
    </div>
  );
}

function QueueDetails({ queue, topics, pick }: { queue: QueueInfo; topics: TopicInfo[]; pick: Pickers }) {
  const first = queue.hosts[0]!;
  const config = first.queue;
  // A queue is configured alike wherever it is held, but for its domain and
  // owner (a group-local queue's) and the topics its pattern matches; show the
  // first holder's settings, and say so if another holder differs.
  const shared = ({ initialOwner: _o, domain: _d, sources: _s, ...rest }: Queue) => JSON.stringify(rest);
  const alike = queue.hosts.every((host) => shared(host.queue) === shared(config));
  // A group-local queue's domains: one queue each, by owner (holders without a
  // domain are listed under their own name).
  const domains = new Map<string, { owner: string | null; members: string[] }>();
  for (const host of queue.hosts) {
    if (host.queue.kind !== 'group_local') continue;
    const key = host.queue.domain ?? host.instance;
    const domain = domains.get(key) ?? { owner: host.queue.initialOwner, members: [] };
    domain.members.push(host.instance);
    domains.set(key, domain);
  }
  const topicLink = (name: string) =>
    topics.some((topic) => topic.name === name) ? (
      <button key={name} className={styles.link} onClick={() => pick.topic(name)}>
        {name}
      </button>
    ) : (
      <code key={name}>{name}</code>
    );
  return (
    <>
      <Heading kind="Queue" title={queue.name} />
      <div className={styles.field}>
        <QueueTags queue={config} />
      </div>
      {!alike && (
        <p className={styles.warning}>
          The instances holding it configure it differently; this shows {first.instance}&apos;s configuration.
        </p>
      )}
      <Field label="Reads">
        {config.underlyingTopic === null ? (
          <p className={styles.text}>The topic of its own name: publish to {queue.name} directly.</p>
        ) : (
          <>
            {config.sources.length === 0 ? (
              <p className={styles.empty}>No topic of {first.instance} matches it.</p>
            ) : (
              <div className={styles.sources}>{config.sources.map(topicLink)}</div>
            )}
            {config.sources.length !== 1 || config.sources[0] !== config.underlyingTopic ? (
              <Row name="Pattern">
                <code>{config.underlyingTopic}</code>
              </Row>
            ) : null}
            {config.defaultPublishTarget !== null && (
              <Row name="Publish to">{topicLink(config.defaultPublishTarget)}</Row>
            )}
          </>
        )}
      </Field>
      {domains.size > 0 && (
        <Field label={domains.size === 1 ? 'Owner' : `Domains (${domains.size}): one queue each`}>
          <ul className={styles.list}>
            {[...domains.entries()].map(([name, domain]) => (
              <li key={name}>
                {domain.owner !== null ? (
                  <button className={styles.link} onClick={() => pick.instance(domain.owner!)}>
                    {domain.owner}
                  </button>
                ) : (
                  <span>{name}</span>
                )}
                <span className={styles.text}>
                  {' '}
                  · domain {name} · {domain.members.join(', ')}
                </span>
              </li>
            ))}
          </ul>
        </Field>
      )}
      {config.filter !== null && (
        <Field label="Filter">
          <Named wire={config.filter} names={config.filterNames} />
        </Field>
      )}
      {config.priority !== null && (
        <Field label="Priority">
          <Named wire={config.priority} names={config.priorityNames} />
        </Field>
      )}
      {config.barrier !== null && (
        <Field label="Barrier">
          <Named wire={config.barrier} names={config.barrierNames} />
        </Field>
      )}
      {config.settings.length > 0 && (
        <Field label="Settings">
          {config.settings.map((setting) => (
            <Row key={setting.name} name={setting.name}>
              <code>{setting.value}</code>
            </Row>
          ))}
        </Field>
      )}
      <Field label={`Held on (${queue.hosts.length})`}>
        <ul className={styles.list}>
          {queue.hosts.map((host) => (
            <li key={host.instance}>
              <button className={styles.link} onClick={() => pick.instance(host.instance)}>
                {host.instance}
              </button>
              {host.queue.initialOwner === host.instance && <span className={styles.text}> · owner</span>}
            </li>
          ))}
        </ul>
      </Field>
      <Field label={`Replication (${queue.replications.length})`}>
        {queue.replications.length === 0 && <p className={styles.empty}>Not replicated.</p>}
        <ul className={styles.list}>
          {queue.replications.map(({ edge, topic: replicated }, ix) => (
            <li key={ix} className={styles.card}>
              <div className={styles.cardTitle}>
                {edge.source} → {edge.target}
              </div>
              <EdgeRows edge={edge} />
              <ReplicatedRows topic={replicated} />
            </li>
          ))}
        </ul>
      </Field>
    </>
  );
}

// Where an instance replicates a topic or queue to (→), receives it from (←),
// or both (⇄).
function Peers({
  cluster,
  topic,
  onPickInstance,
}: {
  cluster: Cluster;
  topic: InstanceTopic;
  onPickInstance: (name: string) => void;
}) {
  return (
    <ul className={styles.peers}>
      {topicPeers(topic).map((peer) => (
        <li key={peer.name} className={styles.peer} title={DIRECTION_TITLES[peer.direction]}>
          <span className={styles.arrow}>{DIRECTION_ARROWS[peer.direction]}</span>
          {cluster.instances.some((candidate) => candidate.name === peer.name) ? (
            <button className={styles.peerLink} onClick={() => onPickInstance(peer.name)}>
              {peer.name}
            </button>
          ) : (
            <span>{peer.name}</span>
          )}
        </li>
      ))}
    </ul>
  );
}

function EdgeCard({ edge, onPickTopic }: { edge: Edge; onPickTopic: (name: string) => void }) {
  return (
    <div className={styles.card}>
      <div className={styles.cardTitle}>
        {edge.source} → {edge.target}
      </div>
      <EdgeRows edge={edge} />
      {edge.destination.addresses.length > 0 && (
        <Row name="Addresses">
          <code>{edge.destination.addresses.join(', ')}</code>
        </Row>
      )}
      <ul className={styles.list}>
        {edge.destination.topics.map((topic) => (
          <li key={topic.name} style={{ marginTop: 8 }}>
            <button className={styles.link} onClick={() => onPickTopic(topic.name)}>
              {topic.name}
            </button>
            <ReplicatedRows topic={topic} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function EdgeRows({ edge }: { edge: Edge }) {
  return (
    <>
      <Row name="Sync type">{edge.destination.syncType ?? 'not set (AMPS default)'}</Row>
      {edge.targetKind === 'group' && <Row name="Failover">{edge.members.join(', ')}</Row>}
      {edge.targetKind === 'external' && <Row name="Target">a group none of the configurations declares</Row>}
    </>
  );
}

function ReplicatedRows({ topic }: { topic: ReplicatedTopic }) {
  return (
    <>
      {topic.filter !== null && (
        <Row name="Filter">
          <Named wire={topic.filter} names={topic.filterNames} />
        </Row>
      )}
      {topic.excludedValidation.length > 0 && <Row name="Skips">{topic.excludedValidation.join(', ')}</Row>}
    </>
  );
}

function HostedTags({ topic }: { topic: HostedTopic }) {
  const sow = topic.sow === null ? null : holding(topic);
  return (
    <div className={styles.tags}>
      {sow !== null && <Pill kind={sow.kind}>{sow.label}</Pill>}
      {topic.journaled && <Pill kind="neutral">journal</Pill>}
      {topic.messageType !== null && <Pill kind="neutral">{topic.messageType}</Pill>}
    </div>
  );
}

function SowRows({ sow, showKey }: { sow: Sow; showKey: boolean }) {
  return (
    <>
      {showKey && sow.keys.length > 0 && (
        <Row name="Key">
          <KeyPaths wire={sow.keys} names={sow.keyNames} />
        </Row>
      )}
      {sow.file !== null && (
        <Row name="File">
          <code>{sow.file}</code>
        </Row>
      )}
      {sow.recoveryPoint !== null && <Row name="Recovers">{sow.recoveryPoint}</Row>}
    </>
  );
}

// A SOW key's paths, in order: several for a composite key.
function KeyPaths({ wire, names }: { wire: string[]; names: string[] | null }) {
  return <Named wire={wire.join(', ')} names={names?.join(', ') ?? null} />;
}

// A key or filter in field names, as people write them, with the wire form AMPS
// matches on beneath it when it differs; the wire form alone without a catalog.
function Named({ wire, names }: { wire: string; names: string | null }) {
  if (names === null || names === wire) return <code>{wire}</code>;
  return (
    <span className={styles.named}>
      <code>{names}</code>
      <code className={styles.wire} title="on the wire, as the configuration writes it">
        {wire}
      </code>
    </span>
  );
}

function NoTopicsNote() {
  return (
    <p className={styles.empty}>
      No topic is kept in a SOW, journaled or replicated here; plain pub/sub topics do not appear in a configuration.
    </p>
  );
}

function Heading({ kind, title }: { kind: string; title: string }) {
  return (
    <>
      <h2 className={styles.heading}>{kind}</h2>
      <div className={styles.title}>{title}</div>
    </>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className={styles.field}>
      <span className={styles.label}>{label}</span>
      <div>{children}</div>
    </div>
  );
}

function Row({ name, children }: { name: string; children: ReactNode }) {
  return (
    <div className={styles.row}>
      <span>{name}</span>
      <span>{children}</span>
    </div>
  );
}

// How a peer exchanges a topic with the selected instance.
const DIRECTION_ARROWS = { to: '→', from: '←', both: '⇄' } as const;
const DIRECTION_TITLES = {
  to: 'replicates the topic to',
  from: 'receives the topic from',
  both: 'replicates the topic to, and receives it from',
} as const;

// A node id's display name: `instance:Infra1` → `Infra1`.
function nodeName(id: string): string {
  return id.slice(id.indexOf(':') + 1);
}
