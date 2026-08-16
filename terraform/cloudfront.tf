resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "${local.name}-frontend-oac"
  description                       = "OAC for fringe-monitor frontend bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_origin_access_control" "data" {
  name                              = "${local.name}-data-oac"
  description                       = "OAC for fringe-monitor data bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_distribution" "web" {
  enabled             = true
  is_ipv6_enabled     = true
  comment             = "fringe-monitor"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"

  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "frontend"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }

  origin {
    domain_name              = aws_s3_bucket.data.bucket_regional_domain_name
    origin_id                = "data"
    origin_access_control_id = aws_cloudfront_origin_access_control.data.id
  }

  default_cache_behavior {
    target_origin_id       = "frontend"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }

    min_ttl     = 0
    default_ttl = 60
    max_ttl     = 300
  }

  ordered_cache_behavior {
    path_pattern           = "/data/*"
    target_origin_id       = "data"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }

    min_ttl     = 0
    default_ttl = 30
    max_ttl     = 60
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  custom_error_response {
    error_code            = 403
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }

  custom_error_response {
    error_code            = 404
    response_code         = 200
    response_page_path    = "/index.html"
    error_caching_min_ttl = 0
  }
}

data "aws_iam_policy_document" "frontend_oac" {
  statement {
    sid     = "AllowCloudFrontServicePrincipal"
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.frontend.arn}/*",
    ]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.web.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  policy = data.aws_iam_policy_document.frontend_oac.json
}

data "aws_iam_policy_document" "data_oac" {
  statement {
    sid     = "AllowCloudFrontServicePrincipalData"
    actions = ["s3:GetObject"]
    resources = [
      "${aws_s3_bucket.data.arn}/*",
    ]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.web.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "data" {
  bucket = aws_s3_bucket.data.id
  policy = data.aws_iam_policy_document.data_oac.json
}

resource "aws_s3_object" "seed_config" {
  bucket       = aws_s3_bucket.data.id
  key          = "data/config.json"
  content_type = "application/json"
  content = jsonencode({
    start_date          = var.default_start_date
    end_date            = var.default_end_date
    nearly_threshold    = 20
    notify_email        = var.notify_email
    auto_watch_sold_out = true
  })

  # Seed only: create the object if it's missing, then never touch it again.
  # An explicit attribute list is a trap here — the scan rewrites this object
  # with headers Terraform doesn't know about (see seed_latest below), and any
  # attribute NOT in the list makes Terraform re-PUT the resource's `content`,
  # wiping the scan output even though `content` itself is ignored.
  lifecycle {
    ignore_changes = all
  }
}

resource "aws_s3_object" "seed_latest" {
  bucket       = aws_s3_bucket.data.id
  key          = "data/latest.json"
  content_type = "application/json"
  content = jsonencode({
    fetched_at        = null
    start_date        = var.default_start_date
    end_date          = var.default_end_date
    nearly_threshold  = 20
    show_count        = 0
    performance_count = 0
    counts            = { sold_out = 0, nearly_sold_out = 0, available = 0, shows_with_sold_out = 0 }
    shows             = []
    message           = "No scan yet. Daily Lambda or manual invoke will populate this."
  })

  # Never overwrite scan output after the first create.
  #
  # This MUST stay `all`. The previous explicit list omitted content_encoding,
  # and the daily scan writes this object gzipped (put_json_s3). Terraform saw
  # `content_encoding = "gzip" -> null`, which is a change it was not told to
  # ignore, and an in-place update re-uploads the resource's `content` — so the
  # 2026-08-16 13:10 deploy replaced a 3,050-show payload with the 296-byte
  # "No scan yet" placeholder and the whole site rendered "no match".
  # Ignoring `content` alone does NOT protect the object: any single
  # non-ignored attribute drags the placeholder content along with it.
  lifecycle {
    ignore_changes = all
  }
}
