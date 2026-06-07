variable "moolabs_api_key" {
  type        = string
  description = "Moolabs SDK API key. Source from Secrets Manager or Parameter Store."
  sensitive   = true
}

variable "database_url" {
  type        = string
  description = "Postgres connection string."
  sensitive   = true
}
