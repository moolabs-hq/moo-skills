# CENTRALIZED secrets module — used by EVERY service in this repo.
# This is the moolabs shape: one shared Terraform module defines all
# app-secret variables; per-service composition pulls them in via
# `module "secrets" { source = "../../modules/secrets" }`.

variable "database_password" {
  type        = string
  description = "RDS password — pulled from Secrets Manager at apply time."
  sensitive   = true
}

variable "stripe_api_key" {
  type        = string
  description = "Stripe webhook secret."
  sensitive   = true
}

# After PR #531's fix, the cost-billing-instrument codemod will emit a
# CHECKLIST entry pointing here, saying "add a moolabs_api_key variable
# of the same shape, then wire it into modules/ecs-service/main.tf's
# task definition's `secrets:` block." It will NOT auto-modify this file
# because it's shared by every service.
