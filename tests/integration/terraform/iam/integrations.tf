resource "juju_offer" "traefik_route" {
  name             = "traefik-route"
  application_name = juju_application.traefik_public.name
  endpoints        = ["traefik-route"]
  model_uuid       = juju_model.core.uuid
}

resource "juju_offer" "postgresql" {
  name             = "postgresql"
  application_name = juju_application.postgresql.name
  endpoints        = ["database"]
  model_uuid       = juju_model.core.uuid
}

resource "juju_offer" "send_ca_cert" {
  name             = "send-ca-cert"
  application_name = juju_application.certificates.name
  endpoints        = ["send-ca-cert"]
  model_uuid       = juju_model.core.uuid
}

resource "juju_integration" "traefik_certificates" {
  application {
    name     = juju_application.traefik_public.name
    endpoint = "certificates"
  }

  application {
    name     = juju_application.certificates.name
    endpoint = "certificates"
  }

  model_uuid = juju_model.core.uuid
}
