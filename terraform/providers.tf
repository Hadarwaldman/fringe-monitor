provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile != "" ? var.aws_profile : null

  default_tags {
    tags = {
      Project     = "fringe-monitor"
      ManagedBy   = "terraform"
      Application = "fringe-monitor"
    }
  }
}
