import json
import argparse
import boto3
from botocore.exceptions import ClientError

route53 = boto3.client('route53')
dynamodb = boto3.client('dynamodb')
cloudformation = boto3.client('cloudformation')

STACK_NAME = 'DyndnsStack'
RECORD_TYPE = 'A'


def get_table_name():
    """Resolve DynamoDB table name from CloudFormation stack."""
    try:
        stack = cloudformation.describe_stacks(StackName=STACK_NAME)
        status = stack['Stacks'][0]['StackStatus']
        if status not in ('CREATE_COMPLETE', 'UPDATE_COMPLETE'):
            print(f'Stack is not ready (status: {status}), try again in a few minutes.')
            exit(1)
    except ClientError as e:
        print(f'Dyndns stack not found: {e}')
        print('Ensure the right AWS CLI profile is being used.')
        exit(1)

    resources = cloudformation.list_stack_resources(StackName=STACK_NAME)
    for resource in resources['StackResourceSummaries']:
        if resource['ResourceType'] == 'AWS::DynamoDB::Table':
            return resource['PhysicalResourceId']

    print('DynamoDB table not found in stack resources.')
    exit(1)


def get_dynamo_record(table, hostname):
    """Fetch a record from DynamoDB and return it as a dict, or None if not found."""
    try:
        response = dynamodb.get_item(
            TableName=table,
            Key={'hostname': {'S': hostname}},
        )
    except ClientError as e:
        print(f'Failed to read DynamoDB: {e}')
        exit(1)

    if 'Item' not in response:
        return None
    return json.loads(response['Item']['data']['S'])


def get_route53_record(zone_id, hostname):
    """Fetch the current A record from Route 53, or None if not found."""
    try:
        result = route53.list_resource_record_sets(
            HostedZoneId=zone_id,
            StartRecordName=hostname,
            StartRecordType=RECORD_TYPE,
            MaxItems='1',
        )
    except ClientError as e:
        print(f'Failed to query Route 53: {e}')
        exit(1)

    sets = result.get('ResourceRecordSets', [])
    if not sets:
        return None
    rrs = sets[0]
    if rrs['Name'].rstrip('.') != hostname.rstrip('.'):
        return None
    return {
        'ip': rrs['ResourceRecords'][0]['Value'],
        'ttl': rrs['TTL'],
    }


def cmd_show(args, table):
    """Display the current configuration for a hostname."""
    data = get_dynamo_record(table, args.hostname)
    if data is None:
        print(f'No record found for {args.hostname}')
        exit(1)

    print(f'Hostname        : {args.hostname}')
    print(f'Hosted zone ID  : {data["route_53_zone_id"]}')
    print(f'TTL             : {data["route_53_record_ttl"]}')
    print(f'Secret          : ********')

    r53_record = get_route53_record(data['route_53_zone_id'], args.hostname)
    if r53_record:
        print(f'Current IP      : {r53_record["ip"]}')
        print(f'Route 53 TTL    : {r53_record["ttl"]}')
    else:
        print('Route 53 record : not found')


def find_zone_for_hostname(hostname):
    """Find the Route 53 hosted zone ID that contains the given hostname.

    Walks up the domain labels until a matching hosted zone is found.
    Returns the zone ID string, or None if not found.
    """
    labels = hostname.rstrip('.').split('.')
    for i in range(len(labels) - 1):
        candidate = '.'.join(labels[i:])
        try:
            result = route53.list_hosted_zones_by_name(DNSName=candidate, MaxItems='1')
            zones = result.get('HostedZones', [])
            if zones and zones[0]['Name'].rstrip('.') == candidate:
                return zones[0]['Id'].split('/')[-1]
        except ClientError:
            pass
    return None


def _delete_route53_record(zone_id, hostname):
    """Delete the Route 53 A record for hostname. Returns True on success."""
    r53_record = get_route53_record(zone_id, hostname)
    if r53_record is None:
        print('Route 53 record not found, skipping.')
        return True
    try:
        route53.change_resource_record_sets(
            HostedZoneId=zone_id,
            ChangeBatch={
                'Changes': [{
                    'Action': 'DELETE',
                    'ResourceRecordSet': {
                        'Name': hostname,
                        'Type': RECORD_TYPE,
                        'TTL': r53_record['ttl'],
                        'ResourceRecords': [{'Value': r53_record['ip']}],
                    },
                }],
            },
        )
        print(f'Route 53 record deleted: {hostname}')
        return True
    except ClientError as e:
        print(f'Failed to delete Route 53 record: {e}')
        return False


def cmd_delete(args, table):
    """Delete a record from DynamoDB, and optionally from Route 53."""
    data = get_dynamo_record(table, args.hostname)

    if data is None:
        if not args.also_route53:
            print(f'No record found for {args.hostname}')
            exit(1)
        # DynamoDB record is already gone; attempt Route 53 deletion only.
        zone_id = find_zone_for_hostname(args.hostname)
        if zone_id is None:
            print(f'No DynamoDB record and no Route 53 hosted zone found for {args.hostname}')
            exit(1)
        print(f'Hostname        : {args.hostname}')
        print('DynamoDB record : already deleted')
        print(f'Hosted zone ID  : {zone_id}')
        print('Route 53 record will be deleted.')
        print('\nDo you want to continue? (y/n)')
        if input().strip() != 'y':
            print('Aborted.')
            exit(0)
        if not _delete_route53_record(zone_id, args.hostname):
            exit(1)
        return

    print(f'Hostname        : {args.hostname}')
    print(f'Hosted zone ID  : {data["route_53_zone_id"]}')
    if args.also_route53:
        print('Route 53 record will also be deleted.')

    print('\nDo you want to continue? (y/n)')
    if input().strip() != 'y':
        print('Aborted.')
        exit(0)

    try:
        dynamodb.delete_item(
            TableName=table,
            Key={'hostname': {'S': args.hostname}},
        )
        print(f'DynamoDB record deleted: {args.hostname}')
    except ClientError as e:
        print(f'Failed to delete DynamoDB record: {e}')
        exit(1)

    if args.also_route53:
        if not _delete_route53_record(data['route_53_zone_id'], args.hostname):
            exit(1)


def cmd_update_ttl(args, table):
    """Update TTL in Route 53 immediately and sync DynamoDB."""
    data = get_dynamo_record(table, args.hostname)
    if data is None:
        print(f'No record found for {args.hostname}')
        exit(1)

    zone_id = data['route_53_zone_id']
    r53_record = get_route53_record(zone_id, args.hostname)
    if r53_record is None:
        print(f'Route 53 record not found for {args.hostname}')
        print('Run dyndns.sh -m set to create the record first.')
        exit(1)

    print(f'Hostname        : {args.hostname}')
    print(f'Current TTL     : {r53_record["ttl"]}  -->  New TTL: {args.ttl}')
    print(f'Current IP      : {r53_record["ip"]}')
    print('\nDo you want to continue? (y/n)')
    if input().strip() != 'y':
        print('Aborted.')
        exit(0)

    try:
        route53.change_resource_record_sets(
            HostedZoneId=zone_id,
            ChangeBatch={
                'Changes': [{
                    'Action': 'UPSERT',
                    'ResourceRecordSet': {
                        'Name': args.hostname,
                        'Type': RECORD_TYPE,
                        'TTL': args.ttl,
                        'ResourceRecords': [{'Value': r53_record['ip']}],
                    },
                }],
            },
        )
        print('Route 53 record updated.')
    except ClientError as e:
        print(f'Failed to update Route 53 record: {e}')
        exit(1)

    data['route_53_record_ttl'] = args.ttl
    try:
        dynamodb.put_item(
            TableName=table,
            Item={
                'hostname': {'S': args.hostname},
                'data': {'S': json.dumps(data)},
            },
        )
        print('DynamoDB record updated.')
    except ClientError as e:
        print(f'Failed to update DynamoDB record: {e}')
        exit(1)


def main():
    parser = argparse.ArgumentParser(description='Manage DDNS records.')
    sub = parser.add_subparsers(dest='command', required=True)

    p_show = sub.add_parser('show', help='Show current configuration')
    p_show.add_argument('hostname', help='DDNS hostname (e.g. router.example.jp)')

    p_del = sub.add_parser('delete', help='Delete a DDNS record')
    p_del.add_argument('hostname', help='DDNS hostname')
    p_del.add_argument('--also-route53', action='store_true',
                       help='Also delete the Route 53 DNS record')

    p_ttl = sub.add_parser('update-ttl', help='Update TTL immediately')
    p_ttl.add_argument('hostname', help='DDNS hostname')
    p_ttl.add_argument('ttl', type=int, help='New TTL in seconds')

    args = parser.parse_args()
    table = get_table_name()

    if args.command == 'show':
        cmd_show(args, table)
    elif args.command == 'delete':
        cmd_delete(args, table)
    elif args.command == 'update-ttl':
        cmd_update_ttl(args, table)


if __name__ == '__main__':
    main()
