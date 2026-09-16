variable "environment_name" {
  description = "Short name used to build resource names (rg-capacity-<environment_name>, etc.). 1-32 characters."
  type        = string

  validation {
    condition     = length(var.environment_name) >= 1 && length(var.environment_name) <= 32
    error_message = "environment_name must be between 1 and 32 characters."
  }
}

variable "location" {
  description = "Azure region for all resources except the Durable Task Scheduler."
  type        = string
}

variable "durable_task_scheduler_location" {
  description = "Durable Task Scheduler location. Defaults to East US 2 when location is East US, because the scheduler is not available there."
  type        = string
  default     = null
}

variable "management_group_id" {
  description = "Management group whose descendant subscriptions are inventoried."
  type        = string
}

variable "principal_id" {
  description = "Object ID of an additional deployment principal granted Durable Task Data Contributor and storage upload access. Leave empty to skip."
  type        = string
  default     = ""
}

locals {
  durable_task_scheduler_location = coalesce(
    var.durable_task_scheduler_location,
    var.location == "eastus" ? "eastus2" : var.location
  )
}
