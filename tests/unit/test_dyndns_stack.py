import aws_cdk as cdk
import aws_cdk.assertions as assertions
from aws_cdk import Aspects
from cdk_nag import AwsSolutionsChecks

from dyndns.dyndns_stack import DyndnsStack


def _create_template():
    app = cdk.App()
    stack = DyndnsStack(app, "TestDyndnsStack")
    Aspects.of(app).add(AwsSolutionsChecks(verbose=True))
    return assertions.Template.from_stack(stack)


def test_dynamodb_table_created():
    template = _create_template()
    template.has_resource_properties("AWS::DynamoDB::Table", {
        "KeySchema": [{"AttributeName": "hostname", "KeyType": "HASH"}],
        "BillingMode": "PAY_PER_REQUEST",
        "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
    })


def test_dynamodb_table_retained():
    template = _create_template()
    template.has_resource("AWS::DynamoDB::Table", {
        "DeletionPolicy": "Retain",
        "UpdateReplacePolicy": "Retain",
    })


def test_lambda_function_properties():
    template = _create_template()
    template.has_resource_properties("AWS::Lambda::Function", {
        "Runtime": "python3.14",
        "Architectures": ["arm64"],
        "Handler": "index.lambda_handler",
        "Timeout": 8,
        "ReservedConcurrentExecutions": 10,
    })


def test_lambda_function_url():
    template = _create_template()
    template.has_resource_properties("AWS::Lambda::Url", {
        "AuthType": "NONE",
        "Cors": {"AllowOrigins": ["*"]},
    })


def test_lambda_has_dynamodb_read_policy():
    template = _create_template()
    template.has_resource_properties("AWS::IAM::Policy", {
        "PolicyDocument": assertions.Match.object_like({
            "Statement": assertions.Match.array_with([
                assertions.Match.object_like({
                    "Action": assertions.Match.array_with([
                        "dynamodb:BatchGetItem",
                        "dynamodb:GetItem",
                    ]),
                    "Effect": "Allow",
                }),
            ]),
        }),
    })


def test_iam_role_has_route53_policy():
    template = _create_template()
    template.has_resource_properties("AWS::IAM::Role", {
        "Policies": assertions.Match.array_with([
            assertions.Match.object_like({
                "PolicyName": "r53",
                "PolicyDocument": assertions.Match.object_like({
                    "Statement": [{
                        "Effect": "Allow",
                        "Resource": "arn:aws:route53:::hostedzone/*",
                        "Action": [
                            "route53:ChangeResourceRecordSets",
                            "route53:ListResourceRecordSets",
                        ],
                    }],
                }),
            }),
        ]),
    })


def test_iam_role_has_cloudwatch_policy():
    template = _create_template()
    template.has_resource_properties("AWS::IAM::Role", {
        "Policies": assertions.Match.array_with([
            assertions.Match.object_like({
                "PolicyName": "cw",
            }),
        ]),
    })
