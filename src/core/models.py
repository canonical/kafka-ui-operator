#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Context and data model definitions."""

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass

from charms.data_platform_libs.v0.data_interfaces import (
    PLUGIN_URL_NOT_REQUIRED,
    Data,
    DataPeerData,
    DataPeerUnitData,
    KafkaConnectRequirerData,
    KafkaRequirerData,
    RequirerData,
)
from charms.data_platform_libs.v0.karapace import KarapaceRequiresData
from ops import Object
from ops.model import Application, Relation, RelationDataAccessError, Unit
from typing_extensions import TYPE_CHECKING, override

from literals import (
    DEFAULT_SECURITY_MECHANISM,
    KAFKA_CONNECT_REL,
    KAFKA_REL,
    KARAPACE_REL,
    PEER_REL,
    SUBSTRATE,
    Status,
    Substrates,
)

if TYPE_CHECKING:
    from charm import KafkaUiCharm


@dataclass
class GeneratedCa:
    """Data class to model generated CA artifacts."""

    ca: str
    ca_key: str


@dataclass
class SelfSignedCertificate:
    """Data class to model self signed certificate artifacts."""

    ca: str
    csr: str
    certificate: str
    private_key: str


class WithStatus(ABC):
    """Abstract base mixin class for objects with status."""

    @property
    @abstractmethod
    def status(self) -> Status | None:
        """Returns status of the object."""
        ...

    @property
    def ready(self) -> bool:
        """Returns True if the status is Active and False otherwise."""
        if self.status == Status.ACTIVE:
            return True

        return False


class WithTlsCa(ABC):
    """Abstract base mixin class for objects with `tls-ca` field."""

    @property
    def tls_ca(self) -> str:
        """Returns the TLS CA field if the relation uses TLS, otherwise empty string."""
        if not self.relation or not self.tls_enabled:
            return ""

        return self.relation_data.get("tls-ca", "")


class RelationContext(WithStatus):
    """Relation context object."""

    def __init__(
        self,
        relation: Relation | None,
        data_interface: Data,
        component: Unit | Application | None,
        substrate: Substrates = SUBSTRATE,
    ):
        self.relation = relation
        self.data_interface = data_interface
        self.component = component
        self.substrate = substrate
        self.relation_data = self.data_interface.as_dict(self.relation.id) if self.relation else {}

    def __bool__(self) -> bool:
        """Boolean evaluation based on the existence of self.relation."""
        try:
            return bool(self.relation)
        except AttributeError:
            return False

    def update(self, items: dict[str, str]) -> None:
        """Write to relation_data."""
        delete_fields = [key for key in items if not items[key]]
        update_content = {k: items[k] for k in items if k not in delete_fields}
        self.relation_data.update(update_content)
        for field in delete_fields:
            del self.relation_data[field]

    def _fetch_from_secrets(self, field) -> str:
        """Fetch a field from secrets defined at the remote unit of the relation."""
        if not self.relation or not self.relation.units:
            return ""

        remote_unit = next(iter(self.relation.units))

        try:
            return self.data_interface._fetch_relation_data_with_secrets(
                remote_unit, [field], self.relation
            ).get(field, "")
        except RelationDataAccessError:
            # remote unit has not the secrets yet.
            pass

        return ""

    @property
    def tls_enabled(self) -> bool:
        """Returns True if TLS is enabled on relation."""
        if not self.relation:
            return False

        tls = self.relation_data.get("tls")

        if tls is not None and tls != "disabled":
            return True

        return False


class KafkaClientContext(WithTlsCa, RelationContext):
    """Context collection metadata for kafka_client relation."""

    def __init__(
        self,
        relation: Relation | None,
        data_interface: RequirerData,
    ):
        super().__init__(relation, data_interface, None)

    @property
    def username(self) -> str:
        """Returns the Kafka client username."""
        if not self.relation:
            return ""

        return self.relation_data.get("username", "")

    @property
    def password(self) -> str:
        """Returns the Kafka client password."""
        if not self.relation:
            return ""

        return self.relation_data.get("password", "")

    @property
    def bootstrap_servers(self) -> str:
        """Returns Kafka bootstrap servers."""
        if not self.relation:
            return ""

        return self.relation_data.get("endpoints", "")

    @property
    def security_protocol(self) -> str:
        """Returns the security protocol."""
        return "SASL_PLAINTEXT" if not self.tls_enabled else "SASL_SSL"

    @property
    def security_mechanism(self) -> str:
        """Returns the security mechanism in use."""
        return DEFAULT_SECURITY_MECHANISM

    @property
    @override
    def status(self) -> Status:
        if not self.relation:
            return Status.MISSING_KAFKA

        if not all([self.username, self.password, self.bootstrap_servers]):
            return Status.NO_KAFKA_CREDENTIALS

        return Status.ACTIVE


class ConnectClientContext(WithTlsCa, RelationContext):
    """Context collection metadata for kafka_connect_client relation."""

    def __init__(
        self,
        relation: Relation | None,
        data_interface: RequirerData,
    ):
        super().__init__(relation, data_interface, None)

    @property
    def plugin_url(self) -> str:
        """Returns the client's plugin-url REST endpoint."""
        if not self.relation:
            return ""

        return self.relation_data.get("plugin-url", "")

    @property
    def endpoints(self) -> str:
        """Returns Kafka Connect endpoints set for the client."""
        if not self.relation:
            return ""

        return self.relation_data.get("endpoints", "")

    @property
    def username(self) -> str:
        """Returns the Kafka Connect client username."""
        if not self.relation:
            return ""

        return f"relation-{self.relation.id}"

    @property
    def password(self) -> str:
        """Returns the Kafka Connect client password."""
        if not self.relation:
            return ""

        return self.relation_data.get("password", "")

    @property
    @override
    def status(self) -> Status:
        return Status.ACTIVE


class KarapaceClientContext(WithTlsCa, RelationContext):
    """Context collection metadata for karapace_client relation."""

    def __init__(
        self,
        relation: Relation | None,
        data_interface: RequirerData,
    ):
        super().__init__(relation, data_interface, None)

    @property
    def endpoints(self) -> str:
        """Returns Kafka Connect endpoints set for the client."""
        if not self.relation:
            return ""

        return self.relation_data.get("endpoints", "")

    @property
    def username(self) -> str:
        """Returns the Kafka Connect client username."""
        if not self.relation:
            return ""

        return f"relation-{self.relation.id}"

    @property
    def password(self) -> str:
        """Returns the Kafka Connect client password."""
        if not self.relation:
            return ""

        return self.relation_data.get("password", "")

    @property
    @override
    def status(self) -> Status:
        return Status.ACTIVE


class TLSContext(RelationContext):
    """TLS metadata of a relation."""

    CA = "ca"
    CHAIN = "chain"
    CERT = "certificate"
    CSR = "csr"
    BROKER_CA = "broker"
    PRIVATE_KEY = "private-key"
    KEYSTORE_PASSWORD = "keystore-password"
    TRUSTSTORE_PASSWORD = "truststore-password"
    KEYS = {CA, CERT, CSR, CHAIN}
    SECRETS = [CERT, CSR, PRIVATE_KEY, KEYSTORE_PASSWORD, TRUSTSTORE_PASSWORD]

    def __init__(self, relation, data_interface, component):
        super().__init__(relation, data_interface, component)

    @property
    def private_key(self) -> str:
        """Private key of the TLS relation."""
        return self.relation_data.get(self.PRIVATE_KEY, "")

    @property
    def csr(self) -> str:
        """Certificate Signing Request (CSR) of the TLS relation."""
        return self.relation_data.get(self.CSR, "")

    @property
    def certificate(self) -> str:
        """The signed certificate from the provider relation."""
        return self.relation_data.get(self.CERT, "")

    @property
    def ca(self) -> str:
        """The CA used to sign the certificate."""
        return self.relation_data.get(self.CA, "")

    @property
    def chain(self) -> list[str]:
        """The chain used to sign unit cert."""
        full_chain = json.loads(self.relation_data.get(self.CHAIN, "null")) or []
        # to avoid adding certificate to truststore if self-signed
        clean_chain: set[str] = set(full_chain) - {self.certificate, self.ca}

        return list(clean_chain)

    @property
    def bundle(self) -> list[str]:
        """The cert bundle used for TLS identity."""
        if not all([self.certificate, self.ca]):
            return []

        # manual-tls-certificates is loaded with the signed cert,
        # the intermediate CA that signed it,
        # and then the missing chain for that CA.
        # We need to present the full bundle - aka Keystore
        # we need to trust each item in the bundle - aka Truststore
        bundle = [self.certificate, self.ca] + self.chain
        return sorted(set(bundle), key=bundle.index)  # ordering might matter

    @property
    def keystore_password(self) -> str:
        """The keystore password."""
        return self.relation_data.get(self.KEYSTORE_PASSWORD, "")

    @property
    def truststore_password(self) -> str:
        """The truststore password."""
        return self.relation_data.get(self.TRUSTSTORE_PASSWORD, "")

    @property
    @override
    def status(self) -> Status | None:
        if all([self.certificate, self.ca, self.private_key]):
            return Status.ACTIVE

        return None


class AppContext(RelationContext):
    """Context collection metadata for Kafka UI peer relation."""

    ADMIN_USERNAME = "admin"
    ADMIN_PASSWORD = "admin-password"

    def __init__(self, relation, data_interface, component):
        super().__init__(relation, data_interface, component)

    @property
    def admin_password(self) -> str:
        """Internal admin user's password."""
        if not self.relation:
            return ""

        return self.relation_data.get(self.ADMIN_PASSWORD, "")

    @property
    @override
    def status(self) -> Status:
        return Status.ACTIVE


class UnitContext(RelationContext):
    """Context collection metadata for a single Kafka Connect worker unit."""

    def __init__(
        self,
        relation: Relation | None,
        data_interface: DataPeerUnitData,
        component: Unit,
    ):
        super().__init__(relation, data_interface, component)
        self.data_interface = data_interface
        self.unit = component

    @property
    def unit_id(self) -> int:
        """The id of the unit from the unit name."""
        return int(self.unit.name.split("/")[1])

    @property
    def tls(self) -> TLSContext:
        """TLS Context of the worker unit."""
        return TLSContext(self.relation, self.data_interface, self.component)

    @property
    def internal_address(self) -> str:
        """The IPv4 address or FQDN of the worker unit."""
        addr = ""
        if self.substrate == "vm":
            for key in ["hostname", "ip", "private-address"]:
                if addr := self.relation_data.get(key, ""):
                    break

        if self.substrate == "k8s":
            addr = f"{self.unit.name.split('/')[0]}-{self.unit_id}.{self.unit.name.split('/')[0]}-endpoints"  # noqa: E501

        return addr

    @property
    @override
    def status(self) -> Status:
        return Status.ACTIVE


class Context(WithStatus, Object):
    """Context model for the Kafka UI charm."""

    def __init__(self, charm: "KafkaUiCharm"):
        super().__init__(parent=charm, key="charm_context")
        self.config = charm.config

        # peer
        self.peer_app_interface = DataPeerData(
            self.model,
            relation_name=PEER_REL,
            additional_secret_fields=[AppContext.ADMIN_PASSWORD],
        )
        self.peer_unit_interface = DataPeerUnitData(
            self.model, relation_name=PEER_REL, additional_secret_fields=TLSContext.SECRETS
        )

        # clients
        self.kafka_client_interface = KafkaRequirerData(
            self.model,
            relation_name=KAFKA_REL,
            topic="__kafka-ui",
            extra_user_roles="admin",
        )
        self.connect_client_interface = KafkaConnectRequirerData(
            self.model, relation_name=KAFKA_CONNECT_REL, plugin_url=PLUGIN_URL_NOT_REQUIRED
        )
        self.karapace_client_interface = KarapaceRequiresData(
            self.model, relation_name=KARAPACE_REL, subject="__kafka-ui", extra_user_roles="admin"
        )

    @property
    def unit(self) -> UnitContext:
        """Returns context of the peer unit relation."""
        return UnitContext(
            self.model.get_relation(PEER_REL),
            self.peer_unit_interface,
            component=self.model.unit,
        )

    @property
    def app(self) -> AppContext:
        """Returns context of the peer app relation."""
        return AppContext(
            self.model.get_relation(PEER_REL),
            self.peer_app_interface,
            component=self.model.app,
        )

    @property
    def peer_relation(self) -> Relation | None:
        """The Kafka UI peer relation."""
        return self.model.get_relation(PEER_REL)

    @property
    def kafka_client(self) -> KafkaClientContext:
        """Returns context of the kafka-client relation."""
        return KafkaClientContext(self.model.get_relation(KAFKA_REL), self.kafka_client_interface)

    @property
    def kafka_connect_client(self) -> ConnectClientContext:
        """Returns context of the kafka-client relation."""
        return ConnectClientContext(
            self.model.get_relation(KAFKA_CONNECT_REL), self.connect_client_interface
        )

    @property
    def karapace_client(self) -> KarapaceClientContext:
        """Returns context of the kafka-client relation."""
        return KarapaceClientContext(
            self.model.get_relation(KARAPACE_REL), self.karapace_client_interface
        )

    @property
    def bind_address(self) -> str:
        """The network binding address from the peer relation."""
        bind_address = ""

        if self.peer_relation:
            if binding := self.model.get_binding(self.peer_relation):
                bind_address = binding.network.bind_address

        return str(bind_address)

    @property
    def context_path(self) -> str:
        """Return the relative path used to serve the UI."""
        return f"/{self.model.name}-{self.model.app.name}" if SUBSTRATE == "k8s" else ""

    @property
    def endpoint(self) -> str:
        """Returns the UI web server endpoint."""
        proto = "http" if not SUBSTRATE == "k8s" else "https"
        return f"{proto}://{self.unit.internal_address}:8080{self.context_path}"

    @property
    @override
    def status(self) -> Status:
        if not self.kafka_client.ready:
            return self.kafka_client.status

        return Status.ACTIVE
