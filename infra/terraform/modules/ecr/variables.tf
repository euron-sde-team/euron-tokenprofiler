variable "name" {
  type = string
}

variable "repositories" {
  type    = list(string)
  default = ["api", "console"]
}

variable "keep_images" {
  type    = number
  default = 20
}

variable "force_delete" {
  type    = bool
  default = false
}

variable "tags" {
  type    = map(string)
  default = {}
}
