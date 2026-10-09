"""Cloud provider vocabulary.

Provider-aware reasoning requires an explicit, closed vocabulary of providers and
service categories. Recommendations must name the provider; provider-agnostic
advice is a different statement and is expressed by leaving ``provider``
unresolved on a comparison.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CloudProvider(StrEnum):
    """Supported deployment targets."""

    AWS = "aws"
    AZURE = "azure"
    GCP = "gcp"
    ON_PREMISES = "on-premises"
    MULTI_CLOUD = "multi-cloud"
    UNSPECIFIED = "unspecified"


class CloudServiceCategory(StrEnum):
    """Functional grouping used when comparing services."""

    COMPUTE = "compute"
    CONTAINER = "container"
    SERVERLESS = "serverless"
    DATABASE = "database"
    CACHE = "cache"
    STORAGE = "storage"
    NETWORK = "network"
    LOAD_BALANCING = "load_balancing"
    CDN = "cdn"
    DNS = "dns"
    QUEUE = "queue"
    MESSAGING = "messaging"
    IAM = "iam"
    SECURITY = "security"
    OBSERVABILITY = "observability"
    CI_CD = "ci_cd"


class ManagedService(StrEnum):
    """How much of the service the provider operates."""

    MANAGED = "managed"
    SERVERLESS = "serverless"
    SELF_MANAGED = "self_managed"
    IaaS = "iaas"


class CloudService(BaseModel):
    """A candidate cloud service together with why it might fit."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    provider: CloudProvider
    name: str = Field(min_length=1, max_length=128)
    category: CloudServiceCategory
    management: ManagedService = ManagedService.MANAGED
    summary: str = Field(min_length=3, max_length=1_000)
    suitable_for: tuple[str, ...] = ()
    not_suitable_for: tuple[str, ...] = ()
    source_url: str | None = None


class CloudRegion(StrEnum):
    """Curated set of frequently referenced regions.

    The model accepts any region string; this enum exists to make the common
    cases discoverable and typo-resistant in MCP schemas.
    """

    AWS_US_EAST_1 = "us-east-1"
    AWS_US_WEST_2 = "us-west-2"
    AWS_EU_WEST_1 = "eu-west-1"
    AWS_EU_CENTRAL_1 = "eu-central-1"
    AZURE_EAST_US = "eastus"
    AZURE_WEST_EUROPE = "westeurope"
    AZURE_BRAZIL_SOUTH = "brazilsouth"
    GCP_US_CENTRAL_1 = "us-central1"
    GCP_EUROPE_WEST_1 = "europe-west1"
    GCP_SOUTH_AMERICA_EAST_1 = "southamerica-east1"


class CloudArchitecture(BaseModel):
    """A cloud-specific architecture proposal."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    provider: CloudProvider
    regions: tuple[str, ...] = Field(min_length=1)
    services: tuple[CloudService, ...] = Field(min_length=1)
    multi_region: bool = False
    rationale: tuple[str, ...] = ()
    constraints: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _services_match_provider(self) -> Self:
        mismatched = sorted(
            {
                service.name
                for service in self.services
                if service.provider is not self.provider
                and self.provider not in {CloudProvider.MULTI_CLOUD, CloudProvider.UNSPECIFIED}
            }
        )
        if mismatched:
            msg = f"services do not belong to {self.provider.value}: {mismatched}"
            raise ValueError(msg)
        if self.multi_region and len(self.regions) < 2:
            msg = "multi_region architectures must declare at least two regions"
            raise ValueError(msg)
        if len(set(self.regions)) != len(self.regions):
            msg = "regions must be unique"
            raise ValueError(msg)
        return self

    def services_in_category(self, category: CloudServiceCategory) -> tuple[CloudService, ...]:
        """Return every service in ``category``."""
        return tuple(service for service in self.services if service.category is category)


class ComparisonCriterion(StrEnum):
    """Dimensions used when comparing services (FR-006)."""

    FUNCTIONALITY = "functionality"
    SCALABILITY = "scalability"
    COMPLEXITY = "complexity"
    OPERATIONAL_OVERHEAD = "operational_overhead"
    RELIABILITY = "reliability"
    SECURITY = "security"
    COST = "cost"
    TEAM_EXPERTISE = "team_expertise"
    VENDOR_LOCK_IN = "vendor_lock_in"
    MATURITY = "maturity"
    PORTABILITY = "portability"


class ServiceComparison(BaseModel):
    """A side-by-side comparison of two or more services."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    question: str = Field(min_length=5, max_length=512)
    services: tuple[CloudService, ...] = Field(min_length=2)
    criteria: tuple[ComparisonCriterion, ...] = Field(min_length=2)
    recommendation: str = Field(min_length=5, max_length=2_000)
    recommendation_rationale: str = Field(min_length=10, max_length=4_000)
    situational_exceptions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _distinct_services(self) -> Self:
        keys = {(service.provider, service.name) for service in self.services}
        if len(keys) != len(self.services):
            msg = "comparison requires distinct services"
            raise ValueError(msg)
        return self
