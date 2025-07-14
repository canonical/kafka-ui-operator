import yaml

from core.models import Context
from core.structured_config import CharmConfig
from core.workload import WorkloadBase


class ConfigManager:
    """Manager for handling Kafka Connect configuration."""

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
        return ["JAVA_OPTS='-Xms1G -Xmx1G -XX:+UseG1GC'"]

    @property
    def basic_auth_config(self) -> dict:
        return {
            "auth": {"type": "LOGIN_FORM"},
            "spring": {
                "security": {
                    "user": {
                        "name": self.context.app.ADMIN_USERNAME,
                        "password": self.context.app.admin_password,
                    }
                }
            },
        }

    @property
    def webclient_config(self) -> dict:
        return {"webclient": {"max-in-memory-buffer-size": "50MB"}}

    @property
    def monitoring_config(self) -> dict:
        return {
            "management": {
                "endpoint": {"info": {"enabled": True}, "health": {"enabled": True}},
                "endpoints": {"web": {"exposure": {"include": "info,health,prometheus"}}},
            }
        }

    @property
    def kafka_client_properties_config(self) -> dict:
        return {
            "security.protocol": "SASL_PLAINTEXT",
            "sasl.mechanism": "SCRAM-SHA-512",
            "sasl.jaas.config": f'org.apache.kafka.common.security.scram.ScramLoginModule required username="{self.context.kafka_client.username}" password="{self.context.kafka_client.password}";',
        }

    @property
    def kafka_connect_config(self) -> list[dict] | None:
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
        if not self.context.karapace_client:
            return

        return {
            "username": self.context.karapace_client.username,
            "password": self.context.karapace_client.password,
        }

    @property
    def kafka_cluster_config(self) -> dict:
        return {
            "kafka": {
                "clusters": [
                    {
                        "name": "kafka",
                        "bootstrap-servers": self.context.kafka_client.bootstrap_servers,
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
    def application_local_config(self) -> dict:
        return (
            self.kafka_cluster_config
            | self.monitoring_config
            | self.basic_auth_config
            | self.webclient_config
        )

    @property
    def clean_yaml_config(self) -> str:
        raw_yaml_string = yaml.dump(self.application_local_config, default_flow_style=False)

        return "\n".join([line for line in raw_yaml_string.splitlines() if line[-4:] != "null"])
