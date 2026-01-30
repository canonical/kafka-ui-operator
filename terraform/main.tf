resource "juju_application" "ui" {
  model_uuid = var.model_uuid
  name       = var.app_name

  charm {
    name     = "kafka-ui"
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }

  units       = length(var.machines) == 0 ? var.units : null
  machines    = length(var.machines) > 0 ? var.machines : null
  constraints = var.constraints
  config      = var.config
}
