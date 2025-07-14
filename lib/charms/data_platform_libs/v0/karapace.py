# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""A library for handling Karapace client relations."""

import logging
from datetime import datetime
from typing import List, Optional

from charms.data_platform_libs.v0.data_interfaces import (
    SECRET_GROUPS,
    AuthenticationEvent,
    EventHandlers,
    ExtraRoleEvent,
    ProviderData,
    RequirerData,
    RequirerEventHandlers,
)
from ops import Model, RelationCreatedEvent, SecretChangedEvent
from ops.charm import (
    CharmBase,
    CharmEvents,
    RelationChangedEvent,
    RelationEvent,
)
from ops.framework import EventSource

# The unique Charmhub library identifier, never change it
LIBID = ""

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


logger = logging.getLogger(__name__)


class KarapaceProvidesEvent(RelationEvent):
    """Base class for Karapace events."""

    @property
    def subject(self) -> Optional[str]:
        """Returns the subject that was requested."""
        if not self.relation.app:
            return None

        return self.relation.data[self.relation.app].get("subject")


class SubjectRequestedEvent(KarapaceProvidesEvent, ExtraRoleEvent):
    """Event emitted when a new subject is requested for use on this relation."""


class KarapaceProvidesEvents(CharmEvents):
    """Karapace events.

    This class defines the events that the Karapace can emit.
    """

    subject_requested = EventSource(SubjectRequestedEvent)


class KarapaceRequiresEvent(RelationEvent):
    """Base class for Karapace events."""

    @property
    def subject(self) -> Optional[str]:
        """Returns the subject."""
        if not self.relation.app:
            return None

        return self.relation.data[self.relation.app].get("subject")

    @property
    def endpoints(self) -> Optional[str]:
        """Returns a comma-separated list of broker uris."""
        if not self.relation.app:
            return None

        return self.relation.data[self.relation.app].get("endpoints")


class SubjectAllowedEvent(AuthenticationEvent, KarapaceRequiresEvent):
    """Event emitted when a new subject ACL is created for use on this relation."""


class EndpointsChangedEvent(AuthenticationEvent, KarapaceRequiresEvent):
    """Event emitted when the endpoints are changed."""


class KarapaceRequiresEvents(CharmEvents):
    """Karapace events.

    This class defines the events that the Karapace can emit.
    """

    subject_allowed = EventSource(SubjectAllowedEvent)
    server_changed = EventSource(EndpointsChangedEvent)


# Karapace Provides and Requires


class KarapaceProvidesData(ProviderData):
    """Provider-side of the Karapace relation."""

    def __init__(self, model: Model, relation_name: str) -> None:
        super().__init__(model, relation_name)

    def set_subject(self, relation_id: int, subject: str) -> None:
        """Set subject name in the application relation databag.

        Args:
            relation_id: the identifier for a particular relation.
            subject: the subject name.
        """
        self.update_relation_data(relation_id, {"subject": subject})

    def set_endpoint(self, relation_id: int, endpoint: str) -> None:
        """Set the endpoint in the application relation databag.

        Args:
            relation_id: the identifier for a particular relation.
            endpoint: the server address.
        """
        self.update_relation_data(relation_id, {"endpoints": endpoint})


class KarapaceProvidesEventHandlers(EventHandlers):
    """Provider-side of the Karapace relation."""

    on = KarapaceProvidesEvents()  # pyright: ignore [reportAssignmentType]

    def __init__(self, charm: CharmBase, relation_data: KarapaceProvidesData) -> None:
        super().__init__(charm, relation_data)
        # Just to keep lint quiet, can't resolve inheritance. The same happened in super().__init__() above
        self.relation_data = relation_data

    def _on_relation_changed_event(self, event: RelationChangedEvent) -> None:
        """Event emitted when the relation has changed."""
        # Leader only
        if not self.relation_data.local_unit.is_leader():
            return

        # Check which data has changed to emit customs events.
        diff = self._diff(event)

        # Emit a subject requested event if the setup key (subject name and optional
        # extra user roles) was added to the relation databag by the application.
        if "subject" in diff.added:
            getattr(self.on, "subject_requested").emit(
                event.relation, app=event.app, unit=event.unit
            )


class KarapaceProvides(KarapaceProvidesData, KarapaceProvidesEventHandlers):
    """Provider-side of the Karapace relation."""

    def __init__(self, charm: CharmBase, relation_name: str) -> None:
        KarapaceProvidesData.__init__(self, charm.model, relation_name)
        KarapaceProvidesEventHandlers.__init__(self, charm, self)


class KarapaceRequiresData(RequirerData):
    """Requirer-side of the Karapace relation."""

    def __init__(
        self,
        model: Model,
        relation_name: str,
        subject: str,
        extra_user_roles: Optional[str] = None,
        additional_secret_fields: Optional[List[str]] = [],
    ):
        """Manager of Karapace client relations."""
        super().__init__(model, relation_name, extra_user_roles, additional_secret_fields)
        self.subject = subject

    @property
    def subject(self):
        """Topic to use in Karapace."""
        return self._subject

    @subject.setter
    def subject(self, value):
        # Avoid wildcards
        if value == "*":
            raise ValueError(f"Error on subject '{value}', cannot be a wildcard.")
        self._subject = value


class KarapaceRequiresEventHandlers(RequirerEventHandlers):
    """Requires-side of the Karapace relation."""

    on = KarapaceRequiresEvents()  # pyright: ignore [reportAssignmentType]

    def __init__(self, charm: CharmBase, relation_data: KarapaceRequiresData) -> None:
        super().__init__(charm, relation_data)
        # Just to keep lint quiet, can't resolve inheritance. The same happened in super().__init__() above
        self.relation_data = relation_data

    def _on_relation_created_event(self, event: RelationCreatedEvent) -> None:
        """Event emitted when the Karapace relation is created."""
        super()._on_relation_created_event(event)

        if not self.relation_data.local_unit.is_leader():
            return

        # Sets subject and extra user roles
        relation_data = {"subject": self.relation_data.subject}

        if self.relation_data.extra_user_roles:
            relation_data["extra-user-roles"] = self.relation_data.extra_user_roles

        self.relation_data.update_relation_data(event.relation.id, relation_data)

    def _on_secret_changed_event(self, event: SecretChangedEvent):
        """Event notifying about a new value of a secret."""
        pass

    def _on_relation_changed_event(self, event: RelationChangedEvent) -> None:
        """Event emitted when the Karapace relation has changed."""
        # Check which data has changed to emit customs events.
        diff = self._diff(event)

        # Check if the subject ACLs are created
        # (the Karapace charm shared the credentials).

        # Register all new secrets with their labels
        if any(newval for newval in diff.added if self.relation_data._is_secret_field(newval)):
            self.relation_data._register_secrets_to_relation(event.relation, diff.added)

        secret_field_user = self.relation_data._generate_secret_field_name(SECRET_GROUPS.USER)
        if (
            "username" in diff.added and "password" in diff.added
        ) or secret_field_user in diff.added:
            # Emit the default event (the one without an alias).
            logger.info("subject ACL created at %s", datetime.now())
            getattr(self.on, "subject_allowed").emit(
                event.relation, app=event.app, unit=event.unit
            )

            # To avoid unnecessary application restarts do not trigger
            # “endpoints_changed“ event if “subject_allowed“ is triggered.
            return

        # Emit an endpoints changed event if the Karapace endpoints added or changed
        # this info in the relation databag.
        if "endpoints" in diff.added or "endpoints" in diff.changed:
            # Emit the default event (the one without an alias).
            logger.info("endpoints changed on %s", datetime.now())
            getattr(self.on, "server_changed").emit(
                event.relation, app=event.app, unit=event.unit
            )  # here check if this is the right design
            return


class KarapaceRequires(KarapaceRequiresData, KarapaceRequiresEventHandlers):
    """Provider-side of the Karapace relation."""

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        subject: str,
        extra_user_roles: Optional[str] = None,
        additional_secret_fields: Optional[List[str]] = [],
    ) -> None:
        KarapaceRequiresData.__init__(
            self,
            charm.model,
            relation_name,
            subject,
            extra_user_roles,
            additional_secret_fields,
        )
        KarapaceRequiresEventHandlers.__init__(self, charm, self)
