import json
import hashlib
import importlib
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture()
def lambda_module():
    """Lambda モジュールをモック化された boto3 クライアントでロードする。"""
    mock_dynamodb = MagicMock()
    mock_r53 = MagicMock()

    def client_factory(service, **kwargs):
        if service == "dynamodb":
            return mock_dynamodb
        if service == "route53":
            return mock_r53
        return MagicMock()

    with patch("boto3.client", side_effect=client_factory):
        import index as mod
        importlib.reload(mod)
        yield {
            "handler": mod.lambda_handler,
            "dynamodb": mock_dynamodb,
            "r53": mock_r53,
        }


def _make_event(body=None, source_ip="203.0.113.1"):
    """Lambda Function URL イベントを生成するヘルパー。"""
    return {
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {
            "http": {
                "sourceIp": source_ip,
                "method": "POST",
                "path": "/",
                "protocol": "HTTP/1.1",
            },
        },
    }


def _make_hash(ip, hostname, secret):
    return hashlib.sha256(f"{ip}{hostname}{secret}".encode("utf-8")).hexdigest()


def _setup_dynamodb_config(mock_ddb, hostname="test.example.com",
                           zone_id="Z12345", ttl=60, secret="mysecret"):
    mock_ddb.get_item.return_value = {
        "Item": {
            "hostname": {"S": hostname},
            "data": {"S": json.dumps({
                "route_53_zone_id": zone_id,
                "route_53_record_ttl": ttl,
                "shared_secret": secret,
            })},
        },
    }


def _setup_route53_get(mock_r53, hostname="test.example.com", ip="10.0.0.1"):
    mock_r53.list_resource_record_sets.return_value = {
        "ResourceRecordSets": [{
            "Name": f"{hostname}.",
            "Type": "A",
            "TTL": 60,
            "ResourceRecords": [{"Value": ip}],
        }],
    }


class TestGetMode:
    def test_returns_source_ip(self, lambda_module):
        event = _make_event({"execution_mode": "get"}, source_ip="203.0.113.1")
        result = lambda_module["handler"](event, None)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["return_status"] == "success"
        assert body["return_message"] == "203.0.113.1"


class TestSetModeSuccess:
    def test_successful_update(self, lambda_module):
        mock_ddb = lambda_module["dynamodb"]
        mock_r53 = lambda_module["r53"]
        _setup_dynamodb_config(mock_ddb)
        _setup_route53_get(mock_r53, ip="10.0.0.1")
        mock_r53.change_resource_record_sets.return_value = {}

        ip = "203.0.113.1"
        h = _make_hash(ip, "test.example.com", "mysecret")
        event = _make_event({
            "execution_mode": "set",
            "ddns_hostname": "test.example.com",
            "validation_hash": h,
        }, source_ip=ip)

        result = lambda_module["handler"](event, None)
        assert result["statusCode"] == 201
        body = json.loads(result["body"])
        assert body["return_status"] == "success"
        assert "updated" in body["return_message"]

    def test_ip_already_matches(self, lambda_module):
        mock_ddb = lambda_module["dynamodb"]
        mock_r53 = lambda_module["r53"]
        ip = "203.0.113.1"
        _setup_dynamodb_config(mock_ddb)
        _setup_route53_get(mock_r53, ip=ip)

        h = _make_hash(ip, "test.example.com", "mysecret")
        event = _make_event({
            "execution_mode": "set",
            "ddns_hostname": "test.example.com",
            "validation_hash": h,
        }, source_ip=ip)

        result = lambda_module["handler"](event, None)
        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["return_status"] == "success"
        assert "matches" in body["return_message"]


class TestValidation:
    def test_invalid_json_body(self, lambda_module):
        event = {
            "body": "not json",
            "requestContext": {"http": {"sourceIp": "1.2.3.4"}},
        }
        result = lambda_module["handler"](event, None)
        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "Invalid" in body["return_message"]

    def test_none_body(self, lambda_module):
        event = _make_event(None)
        result = lambda_module["handler"](event, None)
        assert result["statusCode"] == 400

    def test_empty_body(self, lambda_module):
        event = {"body": "", "requestContext": {"http": {"sourceIp": "1.2.3.4"}}}
        result = lambda_module["handler"](event, None)
        assert result["statusCode"] == 400

    def test_invalid_execution_mode(self, lambda_module):
        event = _make_event({"execution_mode": "delete"})
        result = lambda_module["handler"](event, None)
        assert result["statusCode"] == 400

    def test_missing_execution_mode(self, lambda_module):
        event = _make_event({})
        result = lambda_module["handler"](event, None)
        assert result["statusCode"] == 400

    def test_missing_hostname_in_set(self, lambda_module):
        event = _make_event({
            "execution_mode": "set",
            "validation_hash": "a" * 64,
        })
        result = lambda_module["handler"](event, None)
        assert result["statusCode"] == 400

    def test_missing_validation_hash_in_set(self, lambda_module):
        event = _make_event({
            "execution_mode": "set",
            "ddns_hostname": "test.example.com",
        })
        result = lambda_module["handler"](event, None)
        assert result["statusCode"] == 400

    def test_invalid_hash_format(self, lambda_module):
        mock_ddb = lambda_module["dynamodb"]
        _setup_dynamodb_config(mock_ddb)
        event = _make_event({
            "execution_mode": "set",
            "ddns_hostname": "test.example.com",
            "validation_hash": "not-a-valid-hash",
        })
        result = lambda_module["handler"](event, None)
        assert result["statusCode"] == 400

    def test_hash_too_long(self, lambda_module):
        """re.fullmatch が re.match の前方一致問題を修正していることを検証。"""
        mock_ddb = lambda_module["dynamodb"]
        _setup_dynamodb_config(mock_ddb)
        event = _make_event({
            "execution_mode": "set",
            "ddns_hostname": "test.example.com",
            "validation_hash": "a" * 65,
        })
        result = lambda_module["handler"](event, None)
        assert result["statusCode"] == 400


class TestAuth:
    def test_hash_mismatch(self, lambda_module):
        mock_ddb = lambda_module["dynamodb"]
        _setup_dynamodb_config(mock_ddb)
        event = _make_event({
            "execution_mode": "set",
            "ddns_hostname": "test.example.com",
            "validation_hash": "a" * 64,
        })
        result = lambda_module["handler"](event, None)
        assert result["statusCode"] == 401

    def test_config_not_found(self, lambda_module):
        mock_ddb = lambda_module["dynamodb"]
        mock_ddb.get_item.return_value = {}
        event = _make_event({
            "execution_mode": "set",
            "ddns_hostname": "unknown.example.com",
            "validation_hash": "a" * 64,
        })
        result = lambda_module["handler"](event, None)
        assert result["statusCode"] == 403


class TestRoute53Errors:
    def test_route53_get_error(self, lambda_module):
        from botocore.exceptions import ClientError
        mock_ddb = lambda_module["dynamodb"]
        mock_r53 = lambda_module["r53"]
        _setup_dynamodb_config(mock_ddb)
        mock_r53.list_resource_record_sets.side_effect = ClientError(
            {"Error": {"Code": "NoSuchHostedZone", "Message": "Not found"}},
            "ListResourceRecordSets",
        )

        ip = "203.0.113.1"
        h = _make_hash(ip, "test.example.com", "mysecret")
        event = _make_event({
            "execution_mode": "set",
            "ddns_hostname": "test.example.com",
            "validation_hash": h,
        }, source_ip=ip)

        result = lambda_module["handler"](event, None)
        assert result["statusCode"] == 500
