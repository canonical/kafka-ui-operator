#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling TLS configuration."""

import logging
import re
import socket
import subprocess
from dataclasses import dataclass
from datetime import timedelta
from functools import cached_property

from charms.tls_certificates_interface.v4.tls_certificates import (
    PrivateKey,
    generate_ca,
    generate_certificate,
    generate_csr,
    generate_private_key,
)
from cryptography import x509
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from ops.pebble import ExecError

from core.models import Context, GeneratedCa, SelfSignedCertificate, TLSContext, UnitContext
from core.workload import WorkloadBase
from literals import GROUP, SNAP_NAME, USER_NAME, Substrates

logger = logging.getLogger(__name__)


@dataclass
class Sans:
    """Data class for modeling TLS SANs."""

    sans_ip: list[str]
    sans_dns: list[str]


class TLSManager:
    """Manager for building necessary files for Java TLS auth."""

    DEFAULT_HASH_ALGORITHM: hashes.HashAlgorithm = hashes.SHA256()

    def __init__(
        self,
        context: Context,
        workload: WorkloadBase,
        substrate: Substrates,
    ):
        self.context = context
        self.unit_context: UnitContext = context.unit
        self.tls_context: TLSContext = self.unit_context.tls
        self.workload = workload
        self.substrate = substrate

    @cached_property
    def keytool(self):
        """Return the `keytool` utility depending on substrate."""
        return f"{SNAP_NAME}.keytool" if self.substrate == "vm" else "keytool"

    def generate_alias(self, app_name: str, relation_id: int) -> str:
        """Generate an alias from a relation. Used to identify ca certs."""
        return f"{app_name}-{relation_id}"

    def generate_internal_ca(self) -> GeneratedCa:
        """Set up internal CA to issue self-signed certificates for internal communications."""
        ca_key = generate_private_key()
        ca = generate_ca(
            private_key=ca_key,
            validity=timedelta(days=3650),
            common_name=f"{self.context.unit.unit.app.name}",
        )

        return GeneratedCa(ca=ca.raw, ca_key=ca_key.raw)

    def generate_self_signed_certificate(self) -> SelfSignedCertificate | None:
        """Generate self-signed certificate for the unit to be used for internal communications."""
        internal = self.generate_internal_ca()

        ca_key, ca = internal.ca_key, internal.ca
        if ca is None or ca_key is None:
            logger.error("Internal CA is not setup yet.")
            return

        private_key = (
            PrivateKey(self.tls_context.private_key)
            if self.tls_context.private_key
            else generate_private_key()
        )

        # Generate CSR & cert
        sans = self.build_sans()
        csr = generate_csr(
            private_key=private_key,
            common_name=f"{self.context.unit.unit.name}",
            sans_ip=frozenset(sans.sans_ip),
            sans_dns=frozenset(sans.sans_dns),
        )
        certificate = generate_certificate(
            csr=csr, ca=ca, ca_private_key=ca_key, validity=timedelta(days=3650)
        )

        return SelfSignedCertificate(
            ca=ca, csr=csr.raw, certificate=certificate.raw, private_key=private_key.raw
        )

    def set_server_key(self) -> None:
        """Set the private-key."""
        if not self.tls_context.private_key:
            logger.error("Can't set private-key to unit, missing private-key in relation data")
            return

        self.workload.write(
            content=self.tls_context.private_key,
            path=f"{self.workload.paths.config_dir}/server.key",
        )

    def set_ca(self) -> None:
        """Set the unit CA."""
        if not self.tls_context.ca:
            logger.error("Can't set CA to unit, missing CA in relation data")
            return

        self.workload.write(
            content=self.tls_context.ca, path=f"{self.workload.paths.config_dir}/ca.pem"
        )

    def set_certificate(self) -> None:
        """Set the unit certificate."""
        if not self.tls_context.certificate:
            logger.error("Can't set certificate to unit, missing certificate in relation data")
            return

        self.workload.write(
            content=self.tls_context.certificate,
            path=f"{self.workload.paths.config_dir}/server.pem",
        )

    def set_bundle(self) -> None:
        """Set the unit cert bundle."""
        if not self.tls_context.certificate or not self.tls_context.ca:
            logger.error(
                "Can't set cert bundle to unit, missing certificate or CA in relation data"
            )
            return

        self.workload.write(
            content="\n".join(self.tls_context.bundle),
            path=f"{self.workload.paths.config_dir}/bundle.pem",
        )

    def set_chain(self) -> None:
        """Set the unit chain."""
        if not self.tls_context.bundle:
            logger.error("Can't set chain to unit, missing chain in relation data")
            return

        for i, chain_cert in enumerate(self.tls_context.bundle):
            self.workload.write(
                content=chain_cert, path=f"{self.workload.paths.config_dir}/bundle{i}.pem"
            )

    def set_truststore(self) -> None:
        """Add CA to JKS truststore."""
        trust_aliases = [f"bundle{i}" for i in range(len(self.tls_context.bundle))]

        for alias in trust_aliases:
            self.import_cert(alias, f"{alias}.pem")

        self.workload.exec(f"chown {USER_NAME}:{GROUP} {self.workload.paths.truststore}".split())
        self.workload.exec(["chmod", "770", self.workload.paths.truststore])

    def set_keystore(self) -> None:
        """Create and add unit cert and private-key to the keystore."""
        in_file = "bundle.pem" if self.tls_context.bundle else "server.pem"
        command = [
            "openssl",
            "pkcs12",
            "-export",
            "-in",
            in_file,
            "-inkey",
            "server.key",
            "-passin",
            f"pass:{self.tls_context.keystore_password}",
            "-certfile",
            "server.pem",
            "-out",
            "keystore.p12",
            "-password",
            f"pass:{self.tls_context.keystore_password}",
        ]
        try:
            self.workload.exec(command=command, working_dir=self.workload.paths.config_dir)
            self.workload.exec(f"chown {USER_NAME}:{GROUP} {self.workload.paths.keystore}".split())
            self.workload.exec(["chmod", "770", self.workload.paths.keystore])
        except (subprocess.CalledProcessError, ExecError) as e:
            logger.error(e.stdout)
            raise e

    def import_cert(self, alias: str, filename: str, cert_content: str | None = None) -> None:
        """Add a certificate to the truststore."""
        if cert_content:
            self.workload.write(
                content=cert_content, path=f"{self.workload.paths.config_dir}/{filename}"
            )
        command = [
            self.keytool,
            "-import",
            "-v",
            "-alias",
            alias,
            "-file",
            filename,
            "-keystore",
            self.workload.paths.truststore,
            "-storepass",
            self.tls_context.truststore_password,
            "-noprompt",
        ]
        try:
            self.workload.exec(command=command, working_dir=self.workload.paths.config_dir)
        except (subprocess.CalledProcessError, ExecError) as e:
            # in case this reruns and fails
            if e.stdout and "already exists" in e.stdout:
                logger.debug(e.stdout)
                return
            logger.error(e.stdout)
            raise e

    def remove_cert(self, alias: str) -> None:
        """Remove a cert from the truststore."""
        command = [
            self.keytool,
            "-delete",
            "-v",
            "-alias",
            alias,
            "-keystore",
            self.workload.paths.truststore,
            "-storepass",
            self.tls_context.truststore_password,
            "-noprompt",
        ]
        try:
            self.workload.exec(command=command, working_dir=self.workload.paths.config_dir)
            self.workload.exec(
                f"rm -f {alias}.pem".split(), working_dir=self.workload.paths.config_dir
            )
        except (subprocess.CalledProcessError, ExecError) as e:
            if e.stdout and "does not exist" in e.stdout:
                logger.debug(e.stdout)
                return
            logger.error(e.stdout)
            raise e

    def build_sans(
        self,
    ) -> Sans:
        """Build a SAN dict of DNS names and IPs for the unit."""
        if self.substrate == "vm":
            return Sans(
                sans_ip=[self.unit_context.internal_address],
                sans_dns=[self.unit_context.unit.name, socket.getfqdn()],
            )
        else:
            return Sans(
                sans_ip=sorted(
                    [
                        str(self.context.bind_address),
                        # self.unit_context.node_ip,
                    ]
                ),
                sans_dns=sorted(
                    [
                        self.unit_context.internal_address.split(".")[0],
                        self.unit_context.internal_address,
                        socket.getfqdn(),
                    ]
                ),
            )

    def get_current_sans(self) -> Sans | None:
        """Get the current SANs for the unit cert."""
        if not self.tls_context.certificate:
            return

        command = ["openssl", "x509", "-noout", "-ext", "subjectAltName", "-in", "server.pem"]

        try:
            sans_lines = self.workload.exec(
                command=command, working_dir=self.workload.paths.config_dir
            ).splitlines()
        except (subprocess.CalledProcessError, ExecError) as e:
            logger.error(e.stdout)
            raise e

        for line in sans_lines:
            if "DNS" in line and "IP" in line:
                break

        sans_ip = []
        sans_dns = []
        for item in line.split(", "):
            san_type, san_value = item.split(":")

            if san_type.strip() == "DNS":
                sans_dns.append(san_value)
            if san_type.strip() == "IP Address":
                sans_ip.append(san_value)

        return Sans(sans_ip=sorted(sans_ip), sans_dns=sorted(sans_dns))

    @property
    def sans_change_detected(self) -> bool:
        """Check whether SANs has changed or not.

        Done via a comparison of TLS context with the last state available to the manager.
        """
        if not self.tls_context.ready:
            return False

        current_sans = self.get_current_sans()
        expected_sans = self.build_sans()

        current_sans_ip = set(current_sans.sans_ip) if current_sans else set()
        expected_sans_ip = set(expected_sans.sans_ip) if current_sans else set()
        sans_ip_changed = current_sans_ip ^ expected_sans_ip

        current_sans_dns = set(current_sans.sans_dns) if current_sans else set()
        expected_sans_dns = set(expected_sans.sans_dns) if current_sans else set()
        sans_dns_changed = current_sans_dns ^ expected_sans_dns

        if not sans_ip_changed and not sans_dns_changed:
            return False

        logger.info(
            (
                f"SANs change detected - "
                f"OLD SANs IP = {current_sans_ip - expected_sans_ip}, "
                f"NEW SANs IP = {expected_sans_ip - current_sans_ip}, "
                f"OLD SANs DNS = {current_sans_dns - expected_sans_dns}, "
                f"NEW SANs DNS = {expected_sans_dns - current_sans_dns}"
            )
        )
        return True

    def truststore_changed(self) -> bool:
        """Check if related apps certs is different from what is stored in the truststore."""
        currently_trusted = self.get_trusted_certificates(self.workload.paths.truststore).values()
        changed = False
        for client in (
            self.context.kafka_client,
            self.context.kafka_connect_client,
            self.context.karapace_client,
        ):
            if (
                client.tls_ca
                and self.certificate_fingerprint(client.tls_ca) not in currently_trusted
            ):
                changed = True

        return changed

    def remove_stores(self) -> None:
        """Clean up all keys/certs/stores on a unit."""
        for pattern in ["*.pem", "*.key", "*.p12", "*.jks"]:
            for path in (self.workload.root / self.workload.paths.config_dir).glob(pattern):
                logger.debug(f"Removing {path}")
                path.unlink()

    def configure(self) -> None:
        """Write all necessary files and makes all required configuration for TLS manager."""
        if not self.tls_context.ready:
            return

        self.set_server_key()
        self.set_ca()
        self.set_certificate()
        self.set_chain()
        self.set_bundle()
        self.set_truststore()
        self.set_keystore()

    def get_trusted_certificates(self, truststore_path: str) -> dict[str, bytes]:
        """Return a mapping of alias to certificate fingerprint (hash) for a given truststore."""
        if not (self.workload.root / truststore_path).exists():
            return {}

        command = [
            self.keytool,
            "-list",
            "-keystore",
            truststore_path,
            "-storepass",
            self.tls_context.truststore_password,
            "-noprompt",
        ]
        raw = self.workload.exec(command=command, working_dir=self.workload.paths.config_dir)

        # each record in the truststore has the following format:
        #
        # May DD, YYYY, trustedCertEntry,
        # Certificate fingerprint (SHA-256): E5:2E:...:EB:F3
        #
        # SHA-256 is 32 bytes, so the hash would be 64 hex chars + 31 colons = 95 chars
        return {
            match[0]: self.keytool_hash_to_bytes(match[1])
            for match in re.findall("(.+?),.+?trustedCertEntry.*?\n.+?([0-9a-fA-F:]{95})\n", raw)
        }

    def update_truststore(self) -> None:
        """Update Kafka, Kafka Connect and Karapace client certificates in the truststore."""
        for client in (
            self.context.kafka_client,
            self.context.kafka_connect_client,
            self.context.karapace_client,
        ):
            if client.tls_ca:
                alias = client.relation.name
                self.remove_cert(alias)
                self.import_cert(alias=alias, filename=f"{alias}.pem", cert_content=client.tls_ca)

    @staticmethod
    def certificate_fingerprint(cert: str):
        """Return the certificate fingerprint using the default TLSManager algorithm."""
        cert_obj = x509.load_pem_x509_certificate(cert.encode("utf-8"), default_backend())
        hash_algorithm = cert_obj.signature_hash_algorithm or TLSManager.DEFAULT_HASH_ALGORITHM
        return cert_obj.fingerprint(hash_algorithm)

    @staticmethod
    def keytool_hash_to_bytes(hash: str) -> bytes:
        """Convert a hash in the keytool format (AB:CD:0F:...) to a bytes object."""
        return bytes([int(s, 16) for s in hash.split(":")])
