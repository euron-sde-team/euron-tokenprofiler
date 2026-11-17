# The machine that runs TokenProfiler: one small EC2 instance in a private subnet, reachable only through the load
# balancer, managed through SSM (no SSH, no key pair, IMDSv2 only). An auto-scaling group of size one replaces it if it dies.

data "aws_ssm_parameter" "ami" {
  name = "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
}

data "aws_region" "current" {}

resource "aws_security_group" "host" {
  name_prefix = "${var.name}-host-"
  description = "TokenProfiler app host"
  vpc_id      = var.vpc_id
  tags        = merge(var.tags, { Name = "${var.name}-host" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "from_alb" {
  for_each                     = { api = var.api_port, console = var.console_port }
  security_group_id            = aws_security_group.host.id
  referenced_security_group_id = var.alb_security_group_id
  from_port                    = each.value
  to_port                      = each.value
  ip_protocol                  = "tcp"
  description                  = "ALB to ${each.key}"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.host.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "LLM providers (for the proxy), ECR and AWS APIs (via NAT)"
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "host" {
  name_prefix        = "${var.name}-host-"
  assume_role_policy = data.aws_iam_policy_document.assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.host.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "host" {
  statement {
    sid       = "ReadAppSecret"
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [var.secret_arn]
  }
  statement {
    sid       = "ReportsAndBackups"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload"]
    resources = ["${var.reports_bucket_arn}/reports/*", "${var.reports_bucket_arn}/backups/*"]
  }
  statement {
    sid       = "ListReportsBucket"
    actions   = ["s3:ListBucket"]
    resources = [var.reports_bucket_arn]
  }
  statement {
    sid       = "PullImages"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
  statement {
    sid       = "PullImageLayers"
    actions   = ["ecr:BatchGetImage", "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"]
    resources = var.ecr_repository_arns
  }
}

resource "aws_iam_role_policy" "host" {
  name   = "tokenprofiler"
  role   = aws_iam_role.host.id
  policy = data.aws_iam_policy_document.host.json
}

resource "aws_iam_instance_profile" "host" {
  name_prefix = "${var.name}-host-"
  role        = aws_iam_role.host.name
}

resource "aws_launch_template" "host" {
  name_prefix   = "${var.name}-"
  image_id      = data.aws_ssm_parameter.ami.value
  instance_type = var.instance_type

  iam_instance_profile {
    arn = aws_iam_instance_profile.host.arn
  }

  vpc_security_group_ids = [aws_security_group.host.id]

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  block_device_mappings {
    device_name = "/dev/sda1"
    ebs {
      volume_size           = var.volume_gb
      volume_type           = "gp3"
      throughput            = 250
      encrypted             = true
      delete_on_termination = true
    }
  }

  user_data = base64encode(templatefile("${path.module}/user_data.sh.tftpl", {
    region         = data.aws_region.current.region
    secret_arn     = var.secret_arn
    reports_bucket = var.reports_bucket
    api_image      = var.api_image
    console_image  = var.console_image
    api_port       = var.api_port
    console_port   = var.console_port
    registry       = split("/", var.api_image)[0]
  }))

  tag_specifications {
    resource_type = "instance"
    tags          = merge(var.tags, { Name = var.name })
  }

  update_default_version = true
  tags                   = var.tags
}

resource "aws_autoscaling_group" "host" {
  name_prefix         = "${var.name}-"
  min_size            = 1
  max_size            = 1
  desired_capacity    = 1
  vpc_zone_identifier = var.subnet_ids
  target_group_arns   = var.target_group_arns
  health_check_type   = "ELB"
  # Pulling images and restoring the latest database backup takes a few minutes.
  health_check_grace_period = 600

  launch_template {
    id      = aws_launch_template.host.id
    version = aws_launch_template.host.latest_version
  }

  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 0
    }
  }

  tag {
    key                 = "Name"
    value               = var.name
    propagate_at_launch = true
  }
}
