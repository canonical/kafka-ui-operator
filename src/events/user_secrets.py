#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Event Handler for user-defined secret events."""

import logging
from typing import TYPE_CHECKING

from ops import ModelError, SecretNotFoundError
from ops.charm import (
    SecretChangedEvent,
)
from ops.framework import Object

from core.models import AppContext

if TYPE_CHECKING:
    from charm import KafkaUiCharm


logger = logging.getLogger(__name__)


class SecretsHandler(Object):
    """Handler for events related to user-defined secrets."""

    def __init__(self, charm: "KafkaUiCharm") -> None:
        super().__init__(charm, "kafka-ui-secrets")
        self.charm: "KafkaUiCharm" = charm
        self.context = self.charm.context
        self.workload = self.charm.workload

        self.framework.observe(getattr(self.charm.on, "config_changed"), self._on_secret_changed)
        self.framework.observe(getattr(self.charm.on, "secret_changed"), self._on_secret_changed)

    def _on_secret_changed(self, event: SecretChangedEvent) -> None:
        """Handle the `secret_changed` event."""
        if not self.model.unit.is_leader():
            return

        if not (credentials := self.load_auth_secret()):
            return

        saved_state = {self.context.app.ADMIN_USERNAME: self.context.app.admin_password}
        changed = {u for u in credentials if credentials[u] != saved_state.get(u)}

        if not changed:
            return

        logger.info(f"Credentials change detected for {changed}")

        # Store the password on application databag
        for username in changed:
            new_password = credentials[username]
            if username == AppContext.ADMIN_USERNAME:
                # Currently, the only internally managed username
                self.context.app.admin_password = new_password

        self.charm.on.config_changed.emit()

    def load_auth_secret(self) -> dict[str, str]:
        """Load user-defined credentials from the secrets."""
        if not (secret_id := self.charm.config.system_users):
            return {}

        try:
            secret_content = self.model.get_secret(id=secret_id).get_content(refresh=True)
        except (SecretNotFoundError, ModelError) as e:
            logging.error(f"Failed to fetch the secret, details: {e}")
            return {}

        creds = {
            username: password
            for username, password in secret_content.items()
            if username == AppContext.ADMIN_USERNAME
        }

        denied_users = set(secret_content) - set(creds)

        if denied_users:
            logger.error(f"Can't set password for non-internal user(s) {', '.join(denied_users)}")

        return creds
