# One load balancer in front of the app host: the API on its own paths, the console on everything else.

resource "aws_security_group" "alb" {
  name_prefix = "${var.name}-alb-"
  description = "TokenProfiler load balancer"
  vpc_id      = var.vpc_id
  tags        = merge(var.tags, { Name = "${var.name}-alb" })

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "http" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = var.ingress_cidr
  from_port         = var.certificate_arn == null ? 80 : 443
  to_port           = var.certificate_arn == null ? 80 : 443
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "to_vpc" {
  security_group_id = aws_security_group.alb.id
  cidr_ipv4         = var.vpc_cidr
  ip_protocol       = "-1"
}

resource "aws_lb" "this" {
  name                       = var.name
  load_balancer_type         = "application"
  internal                   = false
  security_groups            = [aws_security_group.alb.id]
  subnets                    = var.public_subnet_ids
  drop_invalid_header_fields = true
  enable_deletion_protection = var.deletion_protection
  # Proxied completions stream for minutes: do not cut idle connections early.
  idle_timeout = 300
  tags         = var.tags
}

resource "aws_lb_target_group" "api" {
  name        = "${var.name}-api"
  port        = var.api_port
  protocol    = "HTTP"
  target_type = "instance"
  vpc_id      = var.vpc_id

  health_check {
    path                = "/readyz"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    interval            = 15
  }

  # SSE streams are long-lived: give clients time to finish during a deploy.
  deregistration_delay = 60
  tags                 = var.tags
}

resource "aws_lb_target_group" "console" {
  name        = "${var.name}-console"
  port        = var.console_port
  protocol    = "HTTP"
  target_type = "instance"
  vpc_id      = var.vpc_id

  health_check {
    path = "/"
  }
  tags = var.tags
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.this.arn
  port              = var.certificate_arn == null ? 80 : 443
  protocol          = var.certificate_arn == null ? "HTTP" : "HTTPS"
  ssl_policy        = var.certificate_arn == null ? null : "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.console.arn
  }
}

resource "aws_lb_listener_rule" "api" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 10

  condition {
    path_pattern {
      values = ["/v1/*", "/proxy/*", "/api/*", "/docs", "/openapi.json", "/healthz", "/readyz"]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}
