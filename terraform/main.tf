resource "juju_application" "ui" {
  model_uuid = var.model_uuid
  name       = var.app_name

  charm {
    name     = "kafka-ui"
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }

  units       = var.units
  constraints = var.constraints
  config      = var.config
}
