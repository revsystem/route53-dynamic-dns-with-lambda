import aws_cdk as cdk
import aws_cdk.aws_lambda as lambda_
import aws_cdk.aws_dynamodb as dynamodb
import aws_cdk.aws_iam as iam
import aws_cdk.aws_logs as logs
from cdk_nag import NagSuppressions, NagPackSuppression


class DyndnsStack(cdk.Stack):

    def __init__(self, scope: cdk.App, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create DynamoDB table
        table = dynamodb.Table(self, "dyndns_db",
            partition_key=dynamodb.Attribute(
                name="hostname", type=dynamodb.AttributeType.STRING,
            ),
            removal_policy=cdk.RemovalPolicy.RETAIN,
            point_in_time_recovery=True,
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
        )

        # Create Lambda role
        fn_role = iam.Role(self, "dyndns_fn_role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description="DynamicDNS Lambda role",
            inline_policies={
                "r53": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            resources=["arn:aws:route53:::hostedzone/*"],
                            actions=[
                                "route53:ChangeResourceRecordSets",
                                "route53:ListResourceRecordSets",
                            ],
                        ),
                    ],
                ),
                "cw": iam.PolicyDocument(
                    statements=[
                        iam.PolicyStatement(
                            effect=iam.Effect.ALLOW,
                            resources=[
                                cdk.Fn.sub(
                                    "arn:${AWS::Partition}:logs:${AWS::Region}:${AWS::AccountId}:log-group:/aws/lambda/*"
                                ),
                            ],
                            actions=[
                                "logs:CreateLogGroup",
                                "logs:CreateLogStream",
                                "logs:PutLogEvents",
                            ],
                        ),
                    ],
                ),
            },
        )

        fn = lambda_.Function(self, "dyndns_fn",
            runtime=lambda_.Runtime.PYTHON_3_14,
            architecture=lambda_.Architecture.ARM_64,
            handler="index.lambda_handler",
            code=lambda_.Code.from_asset("lambda"),
            role=fn_role,
            timeout=cdk.Duration.seconds(8),
            reserved_concurrent_executions=10,
            log_retention=logs.RetentionDays.ONE_MONTH,
            environment={
                "ddns_config_table": table.table_name,
            },
        )

        # Create FunctionURL for invocation
        fn.add_function_url(
            auth_type=lambda_.FunctionUrlAuthType.NONE,
            cors=lambda_.FunctionUrlCorsOptions(
                allowed_origins=["*"],
            ),
        )

        # Give Lambda permissions to read DynamoDB table
        table.grant_read_data(fn)

        # Suppress AwsSolutions-IAM5 for wildcard resources
        NagSuppressions.add_resource_suppressions(
            construct=fn_role,
            suppressions=[
                NagPackSuppression(
                    id="AwsSolutions-IAM5",
                    reason="Route53 resources use hostedzone/* as the function needs access to any hosted zone. "
                           "CloudWatch Logs resources use /aws/lambda/* pattern.",
                    applies_to=[
                        "Resource::arn:aws:route53:::hostedzone/*",
                        "Resource::arn:<AWS::Partition>:logs:<AWS::Region>:<AWS::AccountId>:log-group:/aws/lambda/*",
                    ],
                ),
            ],
            apply_to_children=True,
        )
