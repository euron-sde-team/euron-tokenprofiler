# Private S3 bucket for archived usage reports: `POST /api/reports/archive` writes JSON summaries under `reports/`,
# which finance and platform teams can read without access to the TokenProfiler database.

resource "aws_s3_bucket" "reports" {
  bucket        = "${var.name}-reports"
  force_destroy = var.force_destroy
  tags          = var.tags
}

resource "aws_s3_bucket_public_access_block" "reports" {
  bucket                  = aws_s3_bucket.reports.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_versioning" "reports" {
  bucket = aws_s3_bucket.reports.id
  versioning_configuration {
    status = var.versioning ? "Enabled" : "Suspended"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = var.kms_key_arn == null ? "AES256" : "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id

  # Reports expire after the retention period; interrupted uploads and superseded versions go sooner.
  rule {
    id     = "clean-up"
    status = "Enabled"
    filter {
      prefix = "reports/"
    }
    expiration {
      days = var.retention_days
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 3
    }
    # Superseded versions only exist when versioning is on.
    dynamic "noncurrent_version_expiration" {
      for_each = var.versioning ? [1] : []
      content {
        noncurrent_days = var.noncurrent_days
      }
    }
  }
}

data "aws_iam_policy_document" "tls_only" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.reports.arn, "${aws_s3_bucket.reports.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "tls_only" {
  bucket = aws_s3_bucket.reports.id
  policy = data.aws_iam_policy_document.tls_only.json
}
