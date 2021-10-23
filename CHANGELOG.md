# Changelog

## [Unreleased]

### Changed
- Multithreaded support for io bounded rds tasks
  
## [0.1.4][] - 2021-10-19
[0.1.4]: https://github.com/awslabs/aws-az-failure-chaostoolkit/tree/v0.1.4

### Added
- Support for Aurora cluster failover in rds.actions.fail_az
  
## [0.1.3][] - 2021-10-16
[0.1.3]: https://github.com/awslabs/aws-az-failure-chaostoolkit/tree/v0.1.3

### Added
- Unit tests for rds, elasticache, elb, elbv2, mq, asg, eks
- Sorting for imports
  
### Changed
- Pinned core dependencies
- Separated read and write state to helper funcs
- Standardized usage of pagination API
- Reduced ElastiCache waiter delay

## [0.1.2][] - 2021-10-10
[0.1.2]: https://github.com/awslabs/aws-az-failure-chaostoolkit/tree/v0.1.2

### Added
- Black and flake8
- Unit test for ec2 fail_az action
  
### Changed
- Minor enhancements to existing actions
  
## [0.1.1][] - 2021-09-24
[0.1.1]: https://github.com/awslabs/aws-az-failure-chaostoolkit/tree/v0.1.1

### Changed
- Repository url
- Minor updates to docs

## [0.1.0][] - 2021-09-22
[0.1.0]: https://github.com/awslabs/aws-az-failure-chaostoolkit/tree/v0.1.0

### Added
- fail_az actions for EC2, ElastiCache, RDS, CLB, ALB, Amazon MQ (ActiveMQ), RDS, EKS (Managed NodeGroups) and ASG
- recover_az actions for EC2, CLB, ALB, EKS (Managed NodeGroups) and ASG