import os
from unittest.mock import MagicMock, patch

from azchaosaws.eks.actions import fail_az


@patch("azchaosaws.eks.actions.client", autospec=True)
def test_fail_az_eks_instance(client):
    mock_client = MagicMock()
    client.return_value = mock_client
    az = "ap-southeast-1a"
    dry_run = False
    failure_type = "instance"
    state_path = "test_fail_az_eks_instance.json"
    cluster_name = "my-eks-cluster"
    asg_name = "my-auto-scaling-group"
    scaling_processes = ["AZRebalance"]
    subnet_ids = ["subnet-0ecac448", "subnet-15aaab61", "subnet-b61f49f0"]
    remaining_subnets = subnet_ids[1:3]

    instance_ids = ["i-12345678901234567", "i-01234567123456789", "i-98765432109876543"]

    mock_client.get_paginator = get_mock_paginate

    mock_client.describe_cluster.return_value = {
        "cluster": {
            "name": cluster_name,
            "tags": {"AZ_FAILURE": "True"},
        }
    }

    mock_client.describe_nodegroup.return_value = {
        "nodegroup": {
            "nodegroupName": "my-ng-01",
            "clusterName": "string",
            "resources": {
                "autoScalingGroups": [
                    {"name": "my-auto-scaling-group"},
                ],
            },
        }
    }

    mock_client.describe_instances.return_value = {
        "Reservations": [
            {
                "Instances": [
                    {"InstanceId": instance_ids[0], "InstanceLifecycle": "normal"}
                ]
            }
        ]
    }

    fail_az(az=az, dry_run=dry_run, state_path=state_path, failure_type=failure_type)

    mock_client.suspend_processes.assert_called_with(
        AutoScalingGroupName=asg_name, ScalingProcesses=scaling_processes
    )

    mock_client.update_auto_scaling_group.assert_called_with(
        AutoScalingGroupName=asg_name, VPCZoneIdentifier=",".join(remaining_subnets)
    )

    mock_client.stop_instances.assert_called_with(
        InstanceIds=[instance_ids[0]], Force=True
    )

    os.remove(state_path)


@patch("azchaosaws.eks.actions.client", autospec=True)
def test_fail_az_eks_network(client):
    mock_client = MagicMock()
    client.return_value = mock_client
    az = "ap-southeast-1a"
    dry_run = False
    failure_type = "network"
    state_path = "test_fail_az_eks_network.json"
    cluster_name = "my-eks-cluster"
    asg_name = "my-auto-scaling-group"
    scaling_processes = ["AZRebalance"]
    subnet_ids = ["subnet-0ecac448", "subnet-15aaab61", "subnet-b61f49f0"]
    remaining_subnets = subnet_ids[1:3]
    vpc_id = "vpc-a01106c2"
    acl_id = "acl-9aeb5ef7"
    network_association_id = "aclassoc-66ea5f0b"

    # create_network_acl_entry params
    (
        blackhole_acl_id,
        rule_num,
        protocol,
        cidr_block,
        from_port,
        to_port,
        rule_action,
    ) = ("acl-5fb85d36", 1, "-1", "0.0.0.0/0", 0, 65535, "DENY")

    mock_client.get_paginator = get_mock_paginate

    mock_client.describe_cluster.return_value = {
        "cluster": {
            "name": cluster_name,
            "tags": {"AZ_FAILURE": "True"},
        }
    }

    mock_client.describe_nodegroup.return_value = {
        "nodegroup": {
            "nodegroupName": "my-ng-01",
            "clusterName": "string",
            "resources": {
                "autoScalingGroups": [
                    {"name": "my-auto-scaling-group"},
                ],
            },
            "subnets": subnet_ids,
        }
    }

    mock_client.describe_network_acls.return_value = {
        "NetworkAcls": [
            {
                "Associations": [
                    {
                        "NetworkAclAssociationId": network_association_id,
                        "NetworkAclId": acl_id,
                        "SubnetId": subnet_ids[0],
                    },
                ],
                "Tags": [],
                "NetworkAclId": acl_id,
                "VpcId": vpc_id,
            },
        ],
    }

    mock_client.create_network_acl.return_value = {
        "NetworkAcl": {"NetworkAclId": blackhole_acl_id}
    }

    mock_client.replace_network_acl_association.return_value = {
        "NewAssociationId": "aclassoc-e5b95c8c"
    }

    fail_az(az=az, dry_run=dry_run, state_path=state_path, failure_type=failure_type)

    mock_client.suspend_processes.assert_called_with(
        AutoScalingGroupName=asg_name, ScalingProcesses=scaling_processes
    )

    mock_client.update_auto_scaling_group.assert_called_with(
        AutoScalingGroupName=asg_name, VPCZoneIdentifier=",".join(remaining_subnets)
    )

    mock_client.create_network_acl.assert_called_with(
        VpcId=vpc_id,
        TagSpecifications=[
            {
                "ResourceType": "network-acl",
                "Tags": [{"Key": "Name", "Value": "blackhole_nacl"}],
            }
        ],
    )

    mock_client.create_network_acl_entry.assert_any_call(
        NetworkAclId=blackhole_acl_id,
        RuleNumber=rule_num,
        Protocol=protocol,
        CidrBlock=cidr_block,
        Egress=False,
        RuleAction=rule_action,
        PortRange={"From": from_port, "To": to_port},
    )

    mock_client.create_network_acl_entry.assert_any_call(
        NetworkAclId=blackhole_acl_id,
        RuleNumber=rule_num,
        Protocol=protocol,
        CidrBlock=cidr_block,
        Egress=True,
        RuleAction=rule_action,
        PortRange={"From": from_port, "To": to_port},
    )

    mock_client.replace_network_acl_association.assert_called_with(
        AssociationId=network_association_id, NetworkAclId=blackhole_acl_id
    )

    os.remove(state_path)


def get_mock_paginate(operation_name):
    return {
        "list_clusters": MagicMock(
            paginate=MagicMock(
                return_value=[
                    {
                        "clusters": [
                            "my-eks-cluster",
                        ]
                    }
                ]
            )
        ),
        "list_nodegroups": MagicMock(
            paginate=MagicMock(
                return_value=[
                    {
                        "nodegroups": [
                            "my-ng-01",
                        ],
                    }
                ]
            )
        ),
        "describe_auto_scaling_groups": MagicMock(
            paginate=MagicMock(
                return_value=[
                    {
                        "AutoScalingGroups": [
                            {
                                "AutoScalingGroupName": "my-auto-scaling-group",
                                "AvailabilityZones": [
                                    "ap-southeast-1a",
                                    "ap-southeast-1b",
                                    "ap-southeast-1c",
                                ],
                                "SuspendedProcesses": [],
                                "VPCZoneIdentifier": "subnet-0ecac448,subnet-15aaab61,subnet-b61f49f0",
                                "Instances": [
                                    {
                                        "InstanceId": "i-12345678901234567",
                                    },
                                    {
                                        "InstanceId": "i-01234567123456789",
                                    },
                                    {
                                        "InstanceId": "i-98765432109876543",
                                    },
                                ],
                            },
                        ],
                    }
                ]
            )
        ),
        "describe_subnets": MagicMock(
            paginate=MagicMock(
                return_value=[
                    {
                        "Subnets": [
                            {
                                "SubnetId": "subnet-0ecac448",
                                "VpcId": "vpc-a01106c2",
                                "AvailabilityZone": "ap-southeast-1a",
                            },
                            {
                                "SubnetId": "subnet-15aaab61",
                                "VpcId": "vpc-a01106c2",
                                "AvailabilityZone": "ap-southeast-1b",
                            },
                            {
                                "SubnetId": "subnet-b61f49f0",
                                "VpcId": "vpc-a01106c2",
                                "AvailabilityZone": "ap-southeast-1c",
                            },
                        ],
                    }
                ]
            )
        ),
    }.get(operation_name, MagicMock())
