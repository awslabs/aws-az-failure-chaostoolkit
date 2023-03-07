# Chaos Toolkit AZ Failure Extension for AWS

[![Python versions](https://img.shields.io/badge/python-3.7+-blue.svg)](https://www.python.org/downloads/release/python-360/)
[![PyPi version](https://img.shields.io/pypi/v/aws-az-failure-chaostoolkit.svg)](https://pypi.org/project/aws-az-failure-chaostoolkit/#history)
[![Downloads](https://pepy.tech/badge/aws-az-failure-chaostoolkit)](https://pepy.tech/project/aws-az-failure-chaostoolkit)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](./LICENSE)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
![Lint](https://github.com/awslabs/aws-az-failure-chaostoolkit/workflows/lint/badge.svg?branch=main)
![Tests](https://github.com/awslabs/aws-az-failure-chaostoolkit/workflows/tests/badge.svg?branch=main)
![CodeQL](https://github.com/awslabs/aws-az-failure-chaostoolkit/workflows/CodeQL/badge.svg?branch=main)
![Coverage](https://img.shields.io/badge/coverage-70%25-green.svg)

> Warning: You are strongly advised to only utilize this extension in environments with non-production workloads, as the actions may cause unwanted downtime to your users. Be sure to check if there are any production workloads running in the target AWS account before running Chaos Toolkit experiments with this extension.

This project is a collection of [actions][], gathered as an
extension to the [Chaos Toolkit][chaostoolkit] to simulate an Availability Zone (AZ) failure across multiple AWS services for you to test the resiliency of your hosted applications. This project is purposefully built for simulating AZ failures. If you wish to utilize other fault injection actions with Chaos Toolkit, you might want to consider looking at the [Chaos Toolkit Extension for AWS](https://github.com/chaostoolkit-incubator/chaostoolkit-aws) for your experiments.

[actions]: https://chaostoolkit.org/reference/api/experiment/#action
[chaostoolkit]: https://chaostoolkit.org

## Install

This package requires Python 3.7 or newer.

To be used from your experiment, this package must be installed in the Python
environment where [chaostoolkit][] already lives.

### Install via pip

```
pip install -U aws-az-failure-chaostoolkit
```

## Usage

To use the actions from this package, add the blocks of code below to your Chaos Toolkit experiment file. Replace `TagKey1` and `TagValue1` with the appropriate key-value pair you tagged your resources with. Replace the value of `az` argument with an availability zone of your choice.

### Failure Actions

#### Auto Scaling Group (ASG)

This action removes subnets belonging to the target AZ in all tagged ASGs and suspends the AZRebalance process if its running. If the ASG is only configured for a single AZ, it updates the min, max and desired capacity to 0:
```yaml
- type: action
  name: Simulate AZ Failure for ASG
  provider:
    type: python
    module: azchaosaws.asg.actions
    func: fail_az
    arguments:
      az: "ap-southeast-1a"
      dry_run: True
      tags:
        - Key: "TagKey1"
          Value: "TagValue1"
```

#### Elastic Compute Cloud (EC2)

This action with `failure_type` set to `network` will affect tagged/filtered subnets in the target AZ by replacing the current NACL associations with a newly created blackhole NACL:
```yaml
- type: action
  name: Simulate AZ Failure for EC2
  provider:
    type: python
    module: azchaosaws.ec2.actions
    func: fail_az
    arguments:
      az: "ap-southeast-1a"
      dry_run: True
      failure_type: "network"
      filters:
        - Name: tag:TagKey1
          Values:
            - "TagValue1"
```

This action with `failure_type` set to `instance` will affect tagged/filtered normal/spot instances in the target AZ that are in pending/running state by stopping/terminating them depending on the instance lifecycle:
```yaml
- type: action
  name: Simulate AZ Failure for EC2
  provider:
    type: python
    module: azchaosaws.ec2.actions
    func: fail_az
    arguments:
      az: "ap-southeast-1a"
      dry_run: True
      failure_type: "instance"
      filters:
        - Name: tag:TagKey1
          Values:
            - "TagValue1"
```

#### Application Load Balancer (ALB)

This action removes target AZ subnets in application load balancers:
```yaml
- type: action
  name: Simulate AZ Failure for ALB
  provider:
    type: python
    module: azchaosaws.elbv2.actions
    func: fail_az
    arguments:
      az: "ap-southeast-1a"
      dry_run: True
      tags:
        - Key: "TagKey1"
          Value: "TagValue1"
```

#### Classic Load Balancer (CLB)

This action detaches classic load balancers from subnets belonging to target AZ if they are in a non-default VPC and disables the target AZ from classic load balancers if they are in a default VPC:
```yaml
- type: action
  name: Simulate AZ Failure for CLB
  provider:
    type: python
    module: azchaosaws.elb.actions
    func: fail_az
    arguments:
      az: "ap-southeast-1a"
      dry_run: True
      tags:
        - Key: "TagKey1"
          Value: "TagValue1"
```

#### Relational Database Service (RDS)

This action forces RDS to reboot and failover to another AZ, and/or promotes one of the Aurora Replicas (read-only instances) in the DB cluster to be the primary instance (cluster writer):
```yaml
- type: action
  name: Simulate AZ Failure for RDS
  provider:
    type: python
    module: azchaosaws.rds.actions
    func: fail_az
    arguments:
      az: "ap-southeast-1a"
      dry_run: True
      tags:
        - Key: "TagKey1"
          Value: "TagValue1"
```

#### ElastiCache

This action forces ElastiCache (cluster mode disabled) to failover primary nodes if exists in the target AZ:
```yaml
- type: action
  name: Simulate AZ Failure for ElastiCache (cluster mode disabled)
  provider:
    type: python
    module: azchaosaws.elasticache.actions
    func: fail_az
    arguments:
      az: "ap-southeast-1a"
      dry_run: True
      tags:
        - Key: "TagKey1"
          Value: "TagValue1"
```

This action forces ElastiCache (cluster mode enabled) to failover the shards provided as cache cluster ids (sequential if multiple shards of same cluster) (replace ReplicationGroup1, CacheClusterId1 and CacheClusterId2 as required):
```yaml
- type: action
  name: Simulate AZ Failure for ElastiCache (cluster mode enabled)
  provider:
    type: python
    module: azchaosaws.elasticache.actions
    func: fail_az
    arguments:
      az: "ap-southeast-1a"
      dry_run: True
      tags:
        - Key: "TagKey1"
          Value: "TagValue1"
      replication_groups:
        - replication_group_id: ReplicationGroup1
          cache_cluster_ids:
            - CacheClusterId1
            - CacheClusterId2
```

#### Elastic Kubernetes Service (EKS)

This action removes subnets belonging to the target AZ in all nodegroup ASGs that are part of the tagged EKS clusters and suspends the AZRebalance process if its running. `failure_type` set to `network` will affect target AZ subnets of the nodegroups by associating them with a newly created blackhole NACL. All its previous NACL associations will be replaced with the blackhole NACL:
```yaml
- type: action
  name: Simulate AZ Failure for EKS Clusters
  provider:
    type: python
    module: azchaosaws.eks.actions
    func: fail_az
    arguments:
      az: "ap-southeast-1a"
      dry_run: True
      failure_type: "network"
      tags:
        - TagKey1: "TagValue1"
```

This action removes subnets belonging to the target AZ in all nodegroup ASGs that are part of the tagged EKS clusters and suspends the AZRebalance process if its running.`failure_type` set to `instance` will affect worker nodes that are part of the managed node groups and are in a pending/running state in the target AZ by stopping/terminating normal/spot instances:
```yaml
- type: action
  name: Simulate AZ Failure for EKS Clusters
  provider:
    type: python
    module: azchaosaws.eks.actions
    func: fail_az
    arguments:
      az: "ap-southeast-1a"
      dry_run: True
      failure_type: "instance"
      tags:
        - TagKey1: "TagValue1"
```

#### Managed Message Broker Service (MQ)

This action reboots ActiveMQ brokers that have an active-standby setup:
```yaml
- type: action
  name: Simulate AZ Failure for Amazon MQ (ActiveMQ)
  provider:
    type: python
    module: azchaosaws.mq.actions
    func: fail_az
    arguments:
      az: "ap-southeast-1a"
      dry_run: True
      tags:
        - TagKey1: "TagValue1"
```

### Tips

* To 'rollback' the changes made by the `fail_az` action, you can use `recover_az` in your experiment template. The `recover_az` action will read the state file generated and rollback if it's a service that's supported.
* Do also note that by default, the `dry_run` argument for each `fail_az` action is required. Setting it to `True` will only run read-only operations and not impact the target resources. Set it to `False` if you want the actions to make changes your resources. It is best practice to set it on an experiment level under the configuration block and then reference it for every action. 
* To have granular filtering of resources, you can also provide a list of tags as part of the argument for the `fail_az` action.

Please explore the code to see existing actions and supported arguments. Alternatively, you can run `chaos discover aws-az-failure-chaostoolkit` to view the list of supported actions along with their required and optional arguments for each service in the generated `discovery.json` file.

## Configuration

### Develop

If you wish to develop on this project, make sure to install the development
dependencies. But first, [create a virtual environment][venv] and then install
those dependencies.

[venv]: http://chaostoolkit.org/reference/usage/install/#create-a-virtual-environment

```console
make install-dev
```

Now, you can edit the files and they will be automatically be seen by your
environment, even when running from the `chaos` command locally.

### Format

To format your code execute the following:

```console
make fmt
```

### Lint

To check your code with a linter execute the following:

```console
make lint
```

### Test

To run the tests for the project execute the following:

```console
make test
```

## Security

See [CONTRIBUTING](CONTRIBUTING.md#security-issue-notifications) for more information.

## License

This project is licensed under the Apache-2.0 License.