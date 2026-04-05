# Serverless Dynamic DNS

## Cloud Development Kit (CDK) Deployment

This repository contains all the required code to deploy a Serverless Dynamic DNS solution in AWS.

![Architecture diagram](images/architecture.png?raw=true "Architecture")

CDK will manage the deployment of the following resources:

- Lambda Function
- DynamoDB Table
- Lambda Function IAM Role

The Lambda function will be configured with a FunctionURL for PUBLIC invocation.
The Lambda IAM Role will have the following permissions in addition to the standard Lambda role:

- READ (all actions) for the deployed DynamoDB Table
- Route53 List and Change record set

To deploy the CDK stack to an AWS account is suggested to use a CloudShell session: 
https://docs.aws.amazon.com/cloudshell/latest/userguide/welcome.html

Clone this repository:
>` git clone https://github.com/awslabs/route53-dynamic-dns-with-lambda.git`

Install Python requirements:

> `pip install -r requirements.txt`

To test DNS record update on the CloudShell session `perl-Digest-SHA` must be installed to add the `shasum` package.
 ```
 sudo yum update
 sudo yum install perl-Digest-SHA
 ```

If CDK was never used in the deployment account bootstrap it for CDK:
https://docs.aws.amazon.com/cdk/v2/guide/bootstrapping.html

> `cdk bootstrap`

If you get an error about CDK CLI not being up to date run the following:
> `sudo npm install -g aws-cdk`

> Then retry `cdk bootstrap`

Deploy the stack

> `cdk deploy`

## Configuration

### Route53 Hosted zone and record set

A Route53 Hosted Zone (https://docs.aws.amazon.com/Route53/latest/DeveloperGuide/hosted-zones-working-with.html) is required to update the hostname, the Hosted Zone ID must be included in the configuration and stored in the deployed DynamoDB table using the hostname as key and in the _data_ attribute the following JSON object ([Sample configuration](www.example.com.json)):

```JSON
{
  "route_53_zone_id": "XYZ1234567890",
  "route_53_record_ttl": 60,
  "shared_secret": "SHARED_SECRET_1"
}
```

To facilitate the configuration process execute the included [newrecord.py](newrecord.py) Python script.
(Execute this script for each hostname to be configured)

> `python3 newrecord.py`

The script will verify CDK stack deployment is deployed, if not it will return:

```
Dyndns stack not found, ensure the right AWS CLI profile is being used.
```

If the stack is present but deployment is not completed it will return:

```
Stack not yet deployed try again in few minutes
```

if the stack is successfully deployed the script will prompt:

```
Hosted zone name, i.e. example.com
```

Type the Hosted Zone name:

> `example.com`

If the Hosted Zone does not exist a confirmation prompt will ask for confirmation to create a new one:

```bash
Hosted zone example.com not found.
Do you want to create it? (y/n)
```

> Type `y` to continue or `n` to abort.

In the next steps the script will prompt for:

- Hostname (default www. i.e.: www.example.com)
- TTL (default 60)

If the default configuration is correct, just press `Enter` to continue, if not for each prompt type the required settings, i.e. `test.example.com` for the hostname etc...

### Shared secret

The next prompt will ask to type a shared secret and confirm it. The shared secret will be saved in the JSON configuration and hashed when invoking the Lambda function. Lambda will read the shared secret from DynamoDB and hash it to validate the request is authorized. For example here `SHARED_SECRET_123` is provided.

```bash
Enter the secret for the new record set.
SHARED_SECRET_123
Confirm the secret:
SHARED_SECRET_123
```

The script will summarise the configuration and prompt to confirm:

```bash
##############################################
#                                            #
# The following configuration will be saved: #
#                                            #
  Host name:  www.example.com
  Hosted zone id: ZYZ12345678901234
  Record set TTL: 60
  Secret: SHARED_SECRET_123
#                                            #
#      do you want to continue? (y/n)        #
#                                            #
##############################################
```

Type `n` to abort if anything is incorrect.

> If a Hosted Zone was created during the configuration, a prompt will ask confirmation to delete the created Hosted Zone:

Type `y` to confirm and save the configuration:

```
#####################################################
#                                                   #
# The Serverless Dynamic DNS solution is now ready. #
#                                                   #
#####################################################

www.example.com can be updated with the following command:
./dyndns.sh -m set -u https://xyz1234567890xyz.lambda-url.eu-west-1.on.aws/ -h www.example.com -s SHARED_SECRET_123
```

The [dyndns.sh](dyndns.sh) bash script provided, can be use to invoke the deployed Lambda URL. This can be run via a CRON or SystemD timer to periodically update your hostname.
_newrecord.py_ provides all the flags to successfully run the script:

```bash
./dyndns.sh -m set -u https://xyz1234567890xyz.lambda-url.eu-west-1.on.aws/ -h www.example.com -s SHARED_SECRET_123
```

More information on how to invoke the Lambda URL can be found here: [invocation.md](invocation.md)

## Managing DNS Records

To view, update, or delete an existing DNS record configuration, use the included [managerecord.py](managerecord.py) script.
(Execute this script for each hostname to be managed)

> `python3 managerecord.py <subcommand> <hostname>`

The DynamoDB table name is resolved automatically from the CloudFormation stack — no manual lookup is required.

### Show the current configuration

> `python3 managerecord.py show www.example.com`

The script will display the DynamoDB configuration and the current Route 53 record:

```
Hostname        : www.example.com
Hosted zone ID  : ZYZ12345678901234
TTL             : 60
Secret          : ********
Current IP      : 203.0.113.1
Route 53 TTL    : 60
```

### Update TTL immediately

The Lambda function only updates the Route 53 record when the public IP address changes. If the TTL is changed in DynamoDB via [newrecord.py](newrecord.py), the new value is not reflected in Route 53 until the next IP change.

To apply a new TTL right away without waiting for an IP change:

> `python3 managerecord.py update-ttl www.example.com 300`

The script will display the current and new TTL, then prompt for confirmation:

```
Hostname        : www.example.com
Current TTL     : 60  -->  New TTL: 300
Current IP      : 203.0.113.1

Do you want to continue? (y/n)
```

Type `y` to update both the Route 53 record and the DynamoDB configuration.

### Delete a record

To delete the DynamoDB configuration only:

> `python3 managerecord.py delete www.example.com`

To delete both the DynamoDB configuration and the Route 53 DNS record:

> `python3 managerecord.py delete --also-route53 www.example.com`

The script will display the record to be deleted and prompt for confirmation before making any changes:

```
Hostname        : www.example.com
Hosted zone ID  : ZYZ12345678901234
Route 53 record will also be deleted.

Do you want to continue? (y/n)
```

Type `y` to confirm.

> If the DynamoDB record has already been deleted, running `delete --also-route53` will still locate and remove the Route 53 record by resolving the hosted zone from the hostname.
