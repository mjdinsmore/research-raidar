"""
Research rAIdar — CDK Stack
Deploys: Lambda + EventBridge schedule + SES/Bedrock permissions.
Assumes an existing VPC is NOT required (Lambda runs in managed network).
"""

import os
import tomllib
from pathlib import Path

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
    aws_events as events,
    aws_events_targets as targets,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_s3 as s3,
    aws_servicecatalogappregistry as appregistry,
)
from aws_cdk.aws_lambda import Architecture
from constructs import Construct

APP_NAME   = "Research-rAIdar"
APP_DISPLAY_NAME = "Research rAIdar"
CONFIG_ENV = "dev"

_pyproject = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
APP_VERSION = _pyproject["project"]["version"]


def _application_tag_arn(app_name: str, region: str, account_id: str) -> str:
    """Construct the awsApplication tag ARN for AWS MyApplications cost tracking.

    AppRegistry creates a tag-based resource group named:
      AWS_AppRegistry_AppTag_{account_id}-{app_name}
    The name is fully deterministic so no API call is needed.
    Returns empty string if account ID is unavailable.
    """
    if not account_id:
        return ""
    group_name = f"AWS_AppRegistry_AppTag_{account_id}-{app_name}"
    return f"arn:aws:resource-groups:{region}:{account_id}:group/{group_name}"


class ResearchRAIdarStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── Parameters (override at deploy time with --parameters) ────────────

        ses_sender = cdk.CfnParameter(
            self, "SesSender",
            description="SES-verified sender address",
            default="you@example.com",
        )
        ses_recipient = cdk.CfnParameter(
            self, "SesRecipient",
            description="Digest recipient address",
            default="you@example.com",
        )

        # ── S3 config bucket ──────────────────────────────────────────────────

        config_bucket_name = f"research-raidar-{CONFIG_ENV}"

        config_bucket = s3.Bucket(
            self, "ConfigBucket",
            bucket_name=config_bucket_name,
            removal_policy=RemovalPolicy.RETAIN,   # keep config if stack is destroyed
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
        )

        # ── Lambda (Python 3.13, bundled inside Docker with pip) ───────────────

        fn = lambda_.Function(
            self, "ResearchRAIdarFn",
            function_name="research-raidar",
            description="Fetches recent research papers, scores them for relevance via Bedrock, and delivers a daily digest via SES.",
            runtime=lambda_.Runtime.PYTHON_3_13,
            architecture=Architecture.ARM_64,
            handler="handler.handler",
            code=lambda_.Code.from_asset(
                "../lambda",
                bundling=cdk.BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_13.bundling_image,
                    command=[
                        "bash", "-c",
                        "pip install -r requirements.txt -t /asset-output && cp -r . /asset-output",
                    ],
                ),
            ),
            timeout=Duration.minutes(5),
            memory_size=512,
            log_group=logs.LogGroup(
                self, "FnLogs",
                retention=logs.RetentionDays.TWO_WEEKS,
                removal_policy=RemovalPolicy.DESTROY,
            ),
            environment={
                "SES_SENDER":          ses_sender.value_as_string,
                "SES_RECIPIENT":       ses_recipient.value_as_string,
                "CONFIG_ENV": CONFIG_ENV,
                # All other tuning (categories, scoring, interests) lives in
                # s3://research-raidar-{CONFIG_ENV}/config.json
            },
        )

        # ── S3 config read permission ─────────────────────────────────────────

        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject"],
                resources=[config_bucket.arn_for_objects("*")],
            )
        )

        # ── SES send permission ────────────────────────────────────────────────

        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["ses:SendRawEmail", "ses:SendEmail"],
                resources=["*"],  # scope to verified identity ARN in production
            )
        )

        # ── Bedrock permission ─────────────────────────────────────────────────

        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku*",
                    f"arn:aws:bedrock:*:{cdk.Aws.ACCOUNT_ID}:inference-profile/us.anthropic.claude-haiku*",
                ],
            )
        )

        # ── EventBridge Scheduler role (used by retry schedules) ──────────────

        scheduler_role = iam.Role(
            self, "SchedulerRole",
            assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        )
        fn.grant_invoke(scheduler_role)

        fn.add_environment("SCHEDULER_ROLE_ARN", scheduler_role.role_arn)

        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["scheduler:CreateSchedule"],
                resources=[f"arn:aws:scheduler:{self.region}:{self.account}:schedule/default/research-raidar-retry-*"],
            )
        )
        fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[scheduler_role.role_arn],
            )
        )

        # ── EventBridge schedule: 06:30 UTC daily ─────────────────────────────

        rule = events.Rule(
            self, "DailyTrigger",
            schedule=events.Schedule.cron(
                minute="30",
                hour="6",       # 06:30 UTC = 02:30 ET; adjust to taste
                day="*",
                month="*",
                year="*",
            ),
            description="Trigger research rAIdar every morning",
        )
        rule.add_target(targets.LambdaFunction(fn))

        # ── MyApplications (cost tracking) ────────────────────────────────────

        appregistry.CfnApplication(
            self, "Application",
            name=APP_NAME,
            description="Daily research paper digest scored by Claude via Bedrock",
        )

        # ── Outputs ────────────────────────────────────────────────────────────

        cdk.CfnOutput(self, "FunctionName",  value=fn.function_name)
        cdk.CfnOutput(self, "FunctionArn",   value=fn.function_arn)
        cdk.CfnOutput(self, "ScheduleRule",  value=rule.rule_arn)
        cdk.CfnOutput(self, "ConfigBucketName", value=config_bucket_name)


app     = cdk.App()
account = os.environ.get("CDK_DEFAULT_ACCOUNT", "")
region  = os.environ.get("CDK_DEFAULT_REGION", "us-east-1")

stack = ResearchRAIdarStack(
    app, f"ResearchRAIdarStack-{CONFIG_ENV.capitalize()}",
    env=cdk.Environment(account=account, region=region),
)

app_arn = _application_tag_arn(APP_NAME, region, account)
if app_arn:
    cdk.Tags.of(stack).add("awsApplication", app_arn)

cdk.Tags.of(stack).add("Name",    APP_DISPLAY_NAME)
cdk.Tags.of(stack).add("Version", APP_VERSION)

app.synth()
