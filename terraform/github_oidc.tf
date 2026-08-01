# GitHub Actions OIDC: lets the CI workflow assume an IAM role via short-lived
# tokens instead of long-lived AWS access keys stored as repo secrets.

variable "github_repo" {
  description = "owner/repo allowed to assume the CI role"
  type        = string
  default     = "Hadarwaldman/fringe-monitor"
}

# One provider per account for GitHub's OIDC issuer.
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

data "aws_iam_policy_document" "github_actions_assume" {
  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # Any workflow context in THIS repo (main push, PRs, dispatch). The repo
    # prefix is the security boundary: a fork's token carries its own repo in
    # `sub`, so it cannot match. Fork PRs are also not issued an OIDC token.
    condition {
      test     = "StringLike"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repo}:*"]
    }
  }
}

resource "aws_iam_role" "github_actions" {
  name               = "${local.name}-github-actions"
  assume_role_policy = data.aws_iam_policy_document.github_actions_assume.json
}

# Least-privilege-ish: full access only to the services this stack manages
# (plan/apply + frontend sync + CloudFront invalidation + remote state in S3).
# Scoped by service rather than admin so the role can't touch EC2/RDS/VPC/etc.
data "aws_iam_policy_document" "github_actions" {
  statement {
    sid = "StackServices"
    actions = [
      "s3:*",           # app buckets, frontend sync, and terraform state + lockfile
      "lambda:*",       # the three functions + permissions + code updates
      "cloudfront:*",   # distribution, OAC, invalidations, list-distributions
      "apigateway:*",   # HTTP API, integrations, routes, stage
      "dynamodb:*",     # table + config/watchlist items
      "ses:*",          # email identity for reopen alerts
      "events:*",       # EventBridge rules + targets (daily / 15-min)
      "logs:*",         # Lambda log groups
      "iam:*",          # manage the lambda role, this OIDC role, and the provider
    ]
    resources = ["*"]
  }

  statement {
    sid       = "Identity"
    actions   = ["sts:GetCallerIdentity"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github_actions" {
  name   = "${local.name}-github-actions"
  role   = aws_iam_role.github_actions.id
  policy = data.aws_iam_policy_document.github_actions.json
}
