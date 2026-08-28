terraform {
  required_version = ">= 1.5.0"

  required_providers {
    juju = {
      source  = "juju/juju"
      version = ">= 1.0.0, < 2.0.0"
    }
  }
}
