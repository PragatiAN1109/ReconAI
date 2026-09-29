terraform {
  backend "s3" {
    bucket       = "reconai-tfstate-pragati-2026"
    key          = "reconai/demo/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }
}
