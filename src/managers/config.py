#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling Kafka UI app configuration."""

import yaml

from core.models import Context
from core.structured_config import CharmConfig
from core.workload import WorkloadBase
from literals import SUBSTRATE


class ConfigManager:
    """Manager for handling Kafka UI configuration."""

    config: CharmConfig
    workload: WorkloadBase
    context: Context

    def __init__(
        self,
        context: Context,
        workload: WorkloadBase,
        config: CharmConfig,
        current_version: str = "",
    ):
        self.context = context
        self.workload = workload
        self.config = config
        self.current_version = current_version

    @property
    def java_opts(self) -> list[str]:
        """Return JAVA_OPTS environment variable setting."""
        return ["JAVA_OPTS='-Xms1G -Xmx1G -XX:+UseG1GC'"]

    @property
    def spring_boot_tls_config(self) -> dict:
        """Return TLS config for Spring Boot application."""
        if not self.context.unit.tls.ready or SUBSTRATE == "k8s":
            return {}

        return {
            "ssl": {
                "bundle": {
                    "jks": {
                        "server": {
                            "keystore": {
                                "location": self.workload.paths.keystore,
                                "password": self.context.unit.tls.keystore_password,
                                "type": "PKCS12",
                            }
                        }
                    }
                }
            }
        }

    @property
    def basic_auth_and_tls_config(self) -> dict:
        """Return basic auth & TLS config for the Spring Boot application."""
        return {
            "auth": {"type": "LOGIN_FORM"},
            "spring": {
                "security": {
                    "user": {
                        "name": self.context.app.ADMIN_USERNAME,
                        "password": self.context.app.admin_password,
                    }
                }
            }
            | self.spring_boot_tls_config,
        }

    @property
    def webclient_config(self) -> dict:
        """Return `webclient` configuration."""
        return {"webclient": {"max-in-memory-buffer-size": "50MB"}}

    @property
    def monitoring_config(self) -> dict:
        """Return `management` configuration related to observability endpoints."""
        return {
            "management": {
                "endpoint": {"info": {"enabled": True}, "health": {"enabled": True}},
                "endpoints": {"web": {"exposure": {"include": "info,health,prometheus"}}},
            }
        }

    @property
    def kafka_client_properties_config(self) -> dict:
        """Return general AdminClient configuration properties."""
        return {
            "security.protocol": self.context.kafka_client.security_protocol,
            "sasl.mechanism": "SCRAM-SHA-512",
            "sasl.jaas.config": f"org.apache.kafka.common.security.scram.ScramLoginModule "
            f'required username="{self.context.kafka_client.username}" '
            f'password="{self.context.kafka_client.password}";',
        }

    @property
    def cluster_tls_properties(self) -> dict:
        """Return TLS properties for the Kafka cluster."""
        if not self.context.kafka_client.tls_enabled:
            return {}

        return {
            "ssl": {
                "truststore-location": self.workload.paths.truststore,
                "truststore-password": self.context.unit.tls.truststore_password,
                "verify-ssl": True,
            }
        }

    @property
    def kafka_connect_config(self) -> list[dict] | None:
        """Return Kafka Connect connection configuration."""
        if not self.context.kafka_connect_client:
            return

        return [
            {
                "name": "kafka-connect",
                "address": self.context.kafka_connect_client.endpoints,
                "username": self.context.kafka_connect_client.username,
                "password": self.context.kafka_connect_client.password,
            }
        ]

    @property
    def schema_registry_auth_config(self) -> dict | None:
        """Return schema registry (Karapace) connection configuration."""
        if not self.context.karapace_client:
            return

        return {
            "username": self.context.karapace_client.username,
            "password": self.context.karapace_client.password,
        }

    @property
    def kafka_cluster_config(self) -> dict:
        """Return Apache Kafka cluster connection configuration."""
        return {
            "kafka": {
                "clusters": [
                    {
                        "name": "kafka",
                        "bootstrap-servers": self.context.kafka_client.bootstrap_servers,
                        **self.cluster_tls_properties,
                        "properties": self.kafka_client_properties_config,
                        "kafka-connect": self.kafka_connect_config,
                        "schemaRegistry": f"http://{self.context.karapace_client.endpoints}"
                        or None,
                        "schema-registry-auth": self.schema_registry_auth_config,
                        "metrics": {"type": "PROMETHEUS", "port": 9101},
                        "read-only": True,
                        "polling-throttle-rate": 30,
                        "consumer-properties": {"max.partition.fetch.bytes": 104857600},
                    }
                ],
            },
        }

    @property
    def server_tls_config(self) -> dict:
        """Return TLS (HTTPS) config for the Kafka UI webserver."""
        return (
            {"ssl": {"bundle": "server"}}
            if self.context.unit.tls.ready and SUBSTRATE == "vm"
            else {}
        )

    @property
    def context_path_config(self) -> dict:
        """Return Spring Boot context-path config for when the app is behind a reverse proxy."""
        return (
            {"servlet": {"context-path": self.context.context_path}} if SUBSTRATE == "k8s" else {}
        )

    @property
    def server_config(self):
        """Return Spring Boot `server` config."""
        _config = self.server_tls_config | self.context_path_config

        return {"server": _config} if _config else {}

    @property
    def application_local_config(self) -> dict:
        """Return the final application configuration object."""
        return (
            self.kafka_cluster_config
            | self.monitoring_config
            | self.basic_auth_and_tls_config
            | self.webclient_config
            | self.server_config
        )

    @property
    def clean_yaml_config(self) -> str:
        """Return a writable string representation of the application config."""
        raw_yaml_string = yaml.dump(self.application_local_config, default_flow_style=False)

        return "\n".join([line for line in raw_yaml_string.splitlines() if line[-4:] != "null"])

    def config_changed(self):
        """Check if written config is different from current context."""
        raw = "\n".join(self.workload.read(self.workload.paths.application_local_config))

        return raw != self.clean_yaml_config
