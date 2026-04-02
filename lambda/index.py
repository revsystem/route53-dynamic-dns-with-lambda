import json
import re
import hmac
import hashlib
import os

import boto3
from botocore.exceptions import ClientError

dynamodb_client = boto3.client("dynamodb")
r53_client = boto3.client("route53")


def _response(status_code, status, message):
    """Lambda レスポンスを構築する。"""
    return {
        "statusCode": status_code,
        "headers": {
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET",
        },
        "body": json.dumps({
            "return_status": status,
            "return_message": message,
        }),
    }


def read_config(key_hostname):
    """DynamoDB から設定 JSON を取得し dict で返す。"""
    response = dynamodb_client.get_item(
        TableName=os.environ.get("ddns_config_table"),
        Key={"hostname": {"S": key_hostname}},
    )
    return json.loads(response["Item"]["data"]["S"])


def handle_route53(execution_mode, route_53_zone_id,
                   route_53_record_name, route_53_record_ttl,
                   route_53_record_type, public_ip):
    """Route53 レコードの取得・更新を行う。"""
    if execution_mode == "get_record":
        try:
            current_route53_record_set = r53_client.list_resource_record_sets(
                HostedZoneId=route_53_zone_id,
                StartRecordName=route_53_record_name,
                StartRecordType=route_53_record_type,
                MaxItems="1",
            )
            try:
                if (current_route53_record_set["ResourceRecordSets"][0]["Name"].rstrip(".")
                        == route_53_record_name.rstrip(".")):
                    currentroute53_ip = current_route53_record_set[
                        "ResourceRecordSets"][0]["ResourceRecords"][0]["Value"]
                else:
                    currentroute53_ip = "0"
            except (KeyError, IndexError):
                currentroute53_ip = "0"
            return {"return_status": "success", "return_message": currentroute53_ip}
        except ClientError as e:
            print(f"Route53 get_record ClientError: {e}")
            return {"return_status": "fail", "return_message": "Failed to query DNS record."}
        except Exception as e:
            print(f"Route53 get_record error: {e}")
            return {"return_status": "fail", "return_message": "Failed to query DNS record."}

    if execution_mode == "set_record":
        try:
            r53_client.change_resource_record_sets(
                HostedZoneId=route_53_zone_id,
                ChangeBatch={
                    "Changes": [{
                        "Action": "UPSERT",
                        "ResourceRecordSet": {
                            "Name": route_53_record_name,
                            "Type": route_53_record_type,
                            "TTL": route_53_record_ttl,
                            "ResourceRecords": [{"Value": public_ip}],
                        },
                    }],
                },
            )
            return _response(
                201, "success",
                f"{route_53_record_name} has been updated to {public_ip}",
            )
        except ClientError as e:
            print(f"Route53 set_record ClientError: {e}")
            return _response(500, "fail", str(e))
        except Exception as e:
            print(f"Route53 set_record error: {e}")
            return _response(500, "fail", "Failed to update DNS record.")


def run_set_mode(ddns_hostname, validation_hash, source_ip):
    """SET モードのビジネスロジック。ハッシュ検証と Route53 更新。"""
    try:
        full_config = read_config(ddns_hostname)
    except (ClientError, KeyError) as e:
        print(f"Config lookup failed for {ddns_hostname}: {e}")
        return _response(403, "fail", f"Configuration not found for {ddns_hostname}")

    route_53_zone_id = full_config["route_53_zone_id"]
    route_53_record_ttl = full_config["route_53_record_ttl"]
    route_53_record_type = "A"
    shared_secret = full_config["shared_secret"]

    if not re.fullmatch(r"[0-9a-fA-F]{64}", validation_hash):
        return _response(400, "fail", "You must pass a valid sha256 hash in the hash= argument.")

    calculated_hash = hashlib.sha256(
        (source_ip + ddns_hostname + shared_secret).encode("utf-8"),
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, validation_hash):
        return _response(401, "fail", "Validation hashes do not match.")

    route53_get_response = handle_route53(
        "get_record", route_53_zone_id, ddns_hostname,
        route_53_record_ttl, route_53_record_type, "",
    )
    if route53_get_response["return_status"] == "fail":
        return _response(500, "fail", route53_get_response["return_message"])

    route53_ip = route53_get_response["return_message"]
    if route53_ip == source_ip:
        return _response(200, "success", "Your IP address matches the current Route53 DNS record.")

    return handle_route53(
        "set_record", route_53_zone_id, ddns_hostname,
        route_53_record_ttl, route_53_record_type, source_ip,
    )


def lambda_handler(event, context):
    """Lambda エントリポイント。"""
    try:
        body = json.loads(event.get("body") or "{}")
    except (json.JSONDecodeError, TypeError):
        return _response(400, "fail", "Invalid or missing JSON body.")

    execution_mode = body.get("execution_mode")
    source_ip = event["requestContext"]["http"]["sourceIp"]

    if execution_mode == "get":
        return _response(200, "success", source_ip)

    if execution_mode == "set":
        ddns_hostname = body.get("ddns_hostname", "")
        validation_hash = body.get("validation_hash", "")
        if not ddns_hostname or not validation_hash:
            return _response(400, "fail", "ddns_hostname and validation_hash are required.")
        return run_set_mode(ddns_hostname, validation_hash, source_ip)

    return _response(400, "fail", "You must pass execution_mode=get or execution_mode=set.")
