"""Internal infrastructure vocabulary: compute, network, storage, data, observability."""

from __future__ import annotations

import ipaddress
from enum import StrEnum
from typing import Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from architecture_agent.domain.cloud import CloudRegion


class _NamedResource(Protocol):
    """Structural type for the resource models validated for unique names."""

    name: str


_MAX_SEGMENT_ADDRESSES = 32_768
"""Largest addressable size accepted for a single segment.

A /16 or larger block is the shape of a whole VPC, not of a segment. Modelling it
as one segment hides the segmentation the model exists to reason about.
"""


class ComputeType(StrEnum):
    """Ways to run application code."""

    BARE_METAL = "bare_metal"
    VIRTUAL_MACHINE = "virtual_machine"
    CONTAINER = "container"
    KUBERNETES_NODE = "kubernetes_node"
    SERVERLESS_FUNCTION = "serverless_function"
    SERVERLESS_CONTAINER = "serverless_container"
    PAAS = "paas"


class ScalingModel(StrEnum):
    """How capacity grows."""

    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    BOTH = "both"
    SERVERLESS = "serverless"
    FIXED = "fixed"


class ComputeUnit(BaseModel):
    """A pool of compute capacity."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)
    compute_type: ComputeType
    quantity: int = Field(ge=1, le=100_000)
    vcpu: float | None = Field(default=None, gt=0)
    memory_gb: float | None = Field(default=None, gt=0)
    scaling: ScalingModel = ScalingModel.FIXED
    multi_az: bool = False
    notes: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def _sizing_consistency(self) -> Self:
        needs_size = self.compute_type in {
            ComputeType.BARE_METAL,
            ComputeType.VIRTUAL_MACHINE,
            ComputeType.KUBERNETES_NODE,
        }
        if needs_size and (self.vcpu is None or self.memory_gb is None):
            msg = f"{self.compute_type.value} capacity requires vcpu and memory_gb"
            raise ValueError(msg)
        if self.multi_az and self.quantity < 2:
            msg = "multi-AZ capacity requires at least two units"
            raise ValueError(msg)
        return self


class NetworkTier(StrEnum):
    """Exposure tiers used to reason about segmentation (FR-049)."""

    INTERNET = "internet"
    EDGE = "edge"
    APPLICATION = "application"
    DATA = "data"
    MANAGEMENT = "management"


class NetworkProtocol(StrEnum):
    """Transport and application protocols the agent reasons about."""

    TCP = "tcp"
    UDP = "udp"
    HTTP = "http"
    HTTPS = "https"
    GRPC = "grpc"
    AMQP = "amqp"
    KAFKA = "kafka"
    REDIS = "redis"
    POSTGRES = "postgres"
    MYSQL = "mysql"
    SSH = "ssh"
    ICMP = "icmp"
    WIREGUARD = "wireguard"


class NetworkSegment(BaseModel):
    """A routed network with a defined exposure level."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)
    cidr: str = Field(min_length=9, max_length=43)
    tier: NetworkTier
    public: bool
    region: CloudRegion | str = CloudRegion.AWS_US_EAST_1
    ingress_from: tuple[NetworkTier, ...] = ()
    egress_to: tuple[NetworkTier, ...] = ()
    encrypted_in_transit: bool = True

    @field_validator("cidr")
    @classmethod
    def _valid_cidr(cls, value: str) -> str:
        try:
            network = ipaddress.ip_network(value, strict=True)
        except ValueError as exc:
            msg = f"invalid CIDR {value!r}: {exc}"
            raise ValueError(msg) from exc
        if network.num_addresses > _MAX_SEGMENT_ADDRESSES:
            msg = f"segment {value!r} is too large; split it for segmentation to be meaningful"
            raise ValueError(msg)
        return str(network)

    @model_validator(mode="after")
    def _exposure_consistency(self) -> Self:
        if self.tier in {NetworkTier.DATA, NetworkTier.MANAGEMENT} and self.public:
            msg = f"{self.tier.value} segments must never be internet reachable"
            raise ValueError(msg)
        if self.tier is NetworkTier.INTERNET and not self.public:
            msg = "the internet tier must be modelled as public"
            raise ValueError(msg)
        return self

    @property
    def network(self) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
        """Parsed network object."""
        return ipaddress.ip_network(self.cidr, strict=True)


class StorageType(StrEnum):
    """Persistence tiers."""

    BLOCK = "block"
    OBJECT = "object"
    FILE = "file"
    NAS = "nas"
    SAN = "san"
    ARCHIVE = "archive"
    DATABASE = "database"


class Redundancy(StrEnum):
    """Durability options."""

    SINGLE_AZ = "single-az"
    MULTI_AZ = "multi-az"
    MULTI_REGION = "multi-region"


class StorageUnit(BaseModel):
    """A storage allocation."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)
    storage_type: StorageType
    size_gb: float = Field(gt=0)
    redundancy: Redundancy = Redundancy.SINGLE_AZ
    encrypted_at_rest: bool = True
    versioned: bool = False
    lifecycle_policy: str | None = Field(default=None, max_length=512)
    notes: str | None = Field(default=None, max_length=1_000)


class DatabaseEngine(StrEnum):
    """Data stores the agent is able to reason about."""

    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    SQL_SERVER = "sql_server"
    ORACLE = "oracle"
    SQLITE = "sqlite"
    MONGODB = "mongodb"
    DYNAMODB = "dynamodb"
    CASSANDRA = "cassandra"
    REDIS = "redis"
    ELASTICSEARCH = "elasticsearch"
    OPENSEARCH = "opensearch"
    CLICKHOUSE = "clickhouse"
    NEO4J = "neo4j"
    OTHER = "other"


class DatabaseTopology(StrEnum):
    """Availability topology of a data store."""

    SINGLE = "single"
    PRIMARY_REPLICA = "primary-replica"
    MULTI_AZ_PRIMARY_REPLICA = "multi-az-primary-replica"
    CLUSTER = "cluster"
    SERVERLESS = "serverless"
    SHARDED = "sharded"


class DatabaseUnit(BaseModel):
    """A database deployment described architecturally, not as a SKU."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)
    engine: DatabaseEngine
    topology: DatabaseTopology = DatabaseTopology.SINGLE
    version: str | None = Field(default=None, max_length=64)
    storage_gb: float | None = Field(default=None, gt=0)
    connection_pooling: bool = True
    automated_backups: bool = False
    point_in_time_recovery: bool = False
    publicly_reachable: bool = False
    read_replica: bool = False
    notes: str | None = Field(default=None, max_length=1_000)

    @model_validator(mode="after")
    def _availability_consistency(self) -> Self:
        if self.publicly_reachable:
            msg = (
                f"database {self.name!r} is modelled as publicly reachable; "
                "an exceptional reason must be documented in notes"
            )
            raise ValueError(msg)
        if (
            self.topology
            in {
                DatabaseTopology.MULTI_AZ_PRIMARY_REPLICA,
                DatabaseTopology.CLUSTER,
                DatabaseTopology.SHARDED,
            }
            and not self.read_replica
            and self.topology is DatabaseTopology.MULTI_AZ_PRIMARY_REPLICA
        ):
            msg = "multi-AZ topology implies a standby replica"
            raise ValueError(msg)
        return self


class ObservabilitySignal(StrEnum):
    """Telemetry kinds (FR-014)."""

    LOG = "log"
    METRIC = "metric"
    TRACE = "trace"
    ALERT = "alert"
    HEALTH_CHECK = "health_check"
    DASHBOARD = "dashboard"
    SLO = "slo"
    AUDIT = "audit"


class ObservabilityStack(BaseModel):
    """The telemetry capability attached to an architecture."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    logs: bool = False
    metrics: bool = False
    traces: bool = False
    alerting: bool = False
    health_checks: bool = False
    dashboards: bool = False
    slo_defined: bool = False
    audit_logging: bool = False
    retention_days: int = Field(default=0, ge=0, le=3_650)
    backend: tuple[str, ...] = ()

    def missing_signals(self) -> tuple[ObservabilitySignal, ...]:
        """Return the telemetry kinds the stack does not cover."""
        coverage: dict[ObservabilitySignal, bool] = {
            ObservabilitySignal.LOG: self.logs,
            ObservabilitySignal.METRIC: self.metrics,
            ObservabilitySignal.TRACE: self.traces,
            ObservabilitySignal.ALERT: self.alerting,
            ObservabilitySignal.HEALTH_CHECK: self.health_checks,
            ObservabilitySignal.DASHBOARD: self.dashboards,
            ObservabilitySignal.SLO: self.slo_defined,
            ObservabilitySignal.AUDIT: self.audit_logging,
        }
        return tuple(signal for signal, present in coverage.items() if not present)

    @property
    def is_operable(self) -> bool:
        """Whether logs, metrics and alerting are all present."""
        return self.logs and self.metrics and self.alerting


class AvailabilityObjective(BaseModel):
    """Availability and recovery targets (FR-012)."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    availability_target: float = Field(ge=90.0, le=100.0)
    slo_description: str | None = Field(default=None, max_length=512)
    rto_minutes: int | None = Field(default=None, ge=0, le=100_000)
    rpo_minutes: int | None = Field(default=None, ge=0, le=100_000)
    multi_az: bool = False
    multi_region: bool = False

    @model_validator(mode="after")
    def _recovery_consistency(self) -> Self:
        if (
            self.rto_minutes is not None
            and self.rpo_minutes is not None
            and self.rpo_minutes > self.rto_minutes
        ):
            msg = "rpo_minutes must not exceed rto_minutes"
            raise ValueError(msg)
        if self.availability_target >= 99.99 and not self.multi_region:
            msg = "a 99.99% target requires multi-region design or a documented exception"
            raise ValueError(msg)
        return self


class InfrastructurePlan(BaseModel):
    """A complete internal infrastructure design."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=128)
    compute: tuple[ComputeUnit, ...] = ()
    network: tuple[NetworkSegment, ...] = ()
    storage: tuple[StorageUnit, ...] = ()
    databases: tuple[DatabaseUnit, ...] = ()
    observability: ObservabilityStack = Field(default_factory=ObservabilityStack)
    availability: AvailabilityObjective = Field(
        default_factory=lambda: AvailabilityObjective(availability_target=99.9)
    )

    @field_validator("compute", "network", "storage", "databases")
    @classmethod
    def _unique_names(cls, value: tuple[_NamedResource, ...]) -> tuple[_NamedResource, ...]:
        names = [item.name for item in value]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            msg = f"duplicate resource names: {sorted(duplicates)}"
            raise ValueError(msg)
        return value

    def public_segments(self) -> tuple[NetworkSegment, ...]:
        """Segments reachable from the internet."""
        return tuple(segment for segment in self.network if segment.public)

    def segment_for_tier(self, tier: NetworkTier) -> tuple[NetworkSegment, ...]:
        """Segments belonging to ``tier``."""
        return tuple(segment for segment in self.network if segment.tier is tier)
