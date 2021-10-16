import os
from unittest.mock import MagicMock, patch

from azchaosaws.asg.actions import fail_az


@patch("azchaosaws.asg.actions.client", autospec=True)
def test_fail_az_asg_multi_az(client):
    mock_client = MagicMock()
    client.return_value = mock_client
    az = "ap-southeast-1a"
    dry_run = False
    state_path = "test_fail_az_asg_multi_az.json"
    asg_name = "my-auto-scaling-group"
    scaling_processes = ["AZRebalance"]
    subnet_ids = ["subnet-0ecac448", "subnet-15aaab61", "subnet-b61f49f0"]
    remaining_subnets = subnet_ids[1:3]

    mock_client.get_paginator = get_mock_paginate_multi_az

    mock_client.describe_auto_scaling_groups.return_value = {
        "AutoScalingGroups": [
            {
                "AutoScalingGroupName": asg_name,
                "AvailabilityZones": [
                    "ap-southeast1a",
                    "ap-southeast-1b",
                    "ap-southeast-1c",
                ],
            }
        ]
    }

    fail_az(az=az, dry_run=dry_run, state_path=state_path)

    mock_client.suspend_processes.assert_called_with(
        AutoScalingGroupName=asg_name, ScalingProcesses=scaling_processes
    )

    mock_client.update_auto_scaling_group.assert_called_with(
        AutoScalingGroupName=asg_name, VPCZoneIdentifier=",".join(remaining_subnets)
    )

    os.remove(state_path)


@patch("azchaosaws.asg.actions.client", autospec=True)
def test_fail_az_asg_single_az(client):
    mock_client = MagicMock()
    client.return_value = mock_client
    az = "ap-southeast-1a"
    dry_run = False
    state_path = "test_fail_az_asg_single_az.json"
    asg_name = "my-auto-scaling-group"
    min_size, max_size, desired_cap = 0, 0, 0

    mock_client.get_paginator = get_mock_paginate_single_az

    mock_client.describe_auto_scaling_groups.return_value = {
        "AutoScalingGroups": [
            {
                "AutoScalingGroupName": asg_name,
                "AvailabilityZones": [
                    "ap-southeast1a",
                ],
            }
        ]
    }

    fail_az(az=az, dry_run=dry_run, state_path=state_path)

    mock_client.update_auto_scaling_group.assert_called_with(
        AutoScalingGroupName=asg_name,
        MinSize=min_size,
        MaxSize=max_size,
        DesiredCapacity=desired_cap,
    )

    os.remove(state_path)


def get_mock_paginate_multi_az(operation_name):
    return {
        "describe_tags": MagicMock(
            paginate=MagicMock(
                return_value=[
                    {
                        "Tags": [
                            {
                                "ResourceId": "my-auto-scaling-group",
                                "ResourceType": "auto-scaling-group",
                            },
                        ]
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


def get_mock_paginate_single_az(operation_name):
    return {
        "describe_tags": MagicMock(
            paginate=MagicMock(
                return_value=[
                    {
                        "Tags": [
                            {
                                "ResourceId": "my-auto-scaling-group",
                                "ResourceType": "auto-scaling-group",
                            },
                        ]
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
                                ],
                                "MinSize": 5,
                                "MaxSize": 10,
                                "DesiredCapacity": 7,
                            },
                        ],
                    }
                ]
            )
        ),
    }.get(operation_name, MagicMock())
