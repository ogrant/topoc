"""The fixed parts of every generated AMPS configuration, in one place.

These are deliberately not configurable from the topology: they describe how
this deployment runs AMPS, not the shape of the cluster.
"""

from typing import Final

# Transport the Galvanometer SQL console is pointed at.
SQL_TRANSPORT: Final = "websocket-any"

JOURNAL_DIRECTORY: Final = "${AMPS_DIR}/journal"

LOGGING_PROTOCOL: Final = "stdout"
LOGGING_LEVEL: Final = "info"

# Endpoint placeholder suffixes: `${<INSTANCE>_<SUFFIX>}`.
ADMIN_ENDPOINT: Final = "ADMIN_INETADDR"
CLIENT_ENDPOINT: Final = "CLIENT_INETADDR"
WEBSOCKET_ENDPOINT: Final = "WEBSOCKET_INETADDR"
REPLICATION_ENDPOINT: Final = "REPLICATION_INETADDR"

# Validation checks a one-way distribution (`replicate`) edge excludes: its
# target is not a failover partner, so it need not replicate back.
DISTRIBUTION_EXCLUSIONS: Final = "cascade,replicate,sow"

# Validation checks a two-way (`replicate <->`) edge excludes: it does replicate
# back, so it keeps `replicate`, but its ends are not failover partners.
TWO_WAY_EXCLUSIONS: Final = "cascade,sow"

# The check a `mesh` edge waives for topics that also leave the system over a
# non-mesh edge: `cascade` would demand the partner fully validate its own
# onward destinations, which contradicts the exclusions above.
MESH_EXCLUSIONS_FOR_DISTRIBUTED: Final = "cascade"
