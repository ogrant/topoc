"""The settings of an AMPS message queue, as a `queue` block declares them and
the resolved model carries them to generation.

Plain values with no source locations, shared by the syntax tree and the
resolved model. Each maps to one AMPS queue configuration element; an optional
setting left unset is omitted from the configuration, so AMPS's own default
applies.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Self


class DurationUnit(Enum):
    """A duration's unit, valued by its suffix."""

    MILLISECONDS = "ms"
    SECONDS = "s"
    MINUTES = "m"
    HOURS = "h"
    DAYS = "d"

    @classmethod
    def from_suffix(cls, suffix: str) -> Self | None:
        """The unit a duration literal's suffix spells, if any."""
        return next((unit for unit in cls if unit.value == suffix), None)


@dataclass(frozen=True, slots=True)
class Duration:
    """An AMPS interval: an amount and a unit, written `60s`, `500ms`, `1d`."""

    amount: int
    unit: DurationUnit

    def __str__(self) -> str:
        """The AMPS interval spelling, which is also the `.topo` one: `60s`."""
        return f"{self.amount}{self.unit.value}"


class Leasing(Enum):
    STRICT = "strict"  # only the lease holder may acknowledge a leased message
    SUBLET = "sublet"  # any client may


class ExpirationModel(Enum):
    QUEUE = "queue"
    LATEST = "latest"
    EARLIEST = "earliest"
    DEFAULT = "default"


class Fairness(Enum):
    FAST = "fast"
    ROUND_ROBIN = "round_robin"
    PROPORTIONAL = "proportional"


class QueueRecoveryPoint(Enum):
    """`RecoveryPoint`: where queue recovery starts when there is no state."""

    EPOCH = "epoch"
    NOW = "now"
    CREATION = "creation"


@dataclass(frozen=True, slots=True)
class DeliveryLimits:
    """Settings common to both delivery modes."""

    # `MaxBacklog`: outstanding unacknowledged messages, per instance.
    backlog: int | None
    # `MaxPerSubscriptionBacklog`: outstanding messages per subscription.
    per_subscription_backlog: int | None
    # `Expiration`: how long a message may stay in the queue.
    expiration: Duration | None
    # `ExpirationModel`: whose expiration applies.
    expiration_model: ExpirationModel | None
    # `FairnessModel`: how messages are spread across subscriptions.
    fairness: Fairness | None


@dataclass(frozen=True, slots=True)
class AtLeastOnce:
    """`at-least-once`: a message is leased to one subscriber and redelivered
    unless acknowledged within the lease."""

    # `LeasePeriod`. Required: how long a subscriber may hold a message.
    lease: Duration
    # `Leasing`: who may acknowledge a leased message.
    leasing: Leasing | None
    # `MaxDeliveries`: delivery attempts before the message expires.
    max_deliveries: int | None
    # `MaxCancels`: lease cancellations before the message expires.
    max_cancels: int | None
    limits: DeliveryLimits


@dataclass(frozen=True, slots=True)
class AtMostOnce:
    """`at-most-once`: a message is removed from the queue when it is sent."""

    limits: DeliveryLimits


# How messages are delivered (`Semantics`), with the settings that only make
# sense for that mode.
type Delivery = AtLeastOnce | AtMostOnce
