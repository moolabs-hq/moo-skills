# CENTRALIZED ECS task-definition module. This is the file the CHECKLIST
# points to as Hop 3 in the secret-routing chain — `secrets:` block on
# the container definition that injects the secret as an env var at
# container-boot time.
#
# Example wiring the developer adds after running the codemod:
#
# resource "aws_ecs_task_definition" "service" {
#   container_definitions = jsonencode([
#     {
#       name = var.service_name
#       secrets = [
#         {
#           name      = "MOOLABS_API_KEY"
#           valueFrom = aws_secretsmanager_secret.moolabs_api_key.arn
#         },
#       ]
#     }
#   ])
# }

variable "service_name" {
  type = string
}
