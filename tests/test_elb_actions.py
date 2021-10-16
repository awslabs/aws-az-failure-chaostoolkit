import os
from unittest.mock import MagicMock, patch

from azchaosaws.elb.actions import fail_az


@patch("azchaosaws.elb.actions.client", autospec=True)
def test_fail_az_clb_non_default_vpc(client):
    mock_client = MagicMock()
    client.return_value = mock_client
    az = "ap-southeast-1a"
    dry_run = False
    state_path = "test_fail_az_clb_non_default_vpc.json"
    load_balancer_name = "my-load-balancer"
    subnet_ids = ["subnet-b61f49f0", "subnet-15aaab61", "subnet-0ecac448"]
    vpc_id = "vpc-a01106c2"

    mock_client.get_paginator = get_mock_paginate

    mock_client.describe_tags.return_value = {
        "TagDescriptions": [
            {
                "LoadBalancerName": load_balancer_name,
                "Tags": [
                    {"Key": "AZ_FAILURE", "Value": "True"},
                ],
            },
        ]
    }

    mock_client.describe_vpcs.return_value = {"Vpcs": []}

    mock_client.describe_load_balancers.return_value = {
        "LoadBalancerDescriptions": [
            {
                "LoadBalancerName": load_balancer_name,
                "AvailabilityZones": [
                    "ap-southeast-1a",
                    "ap-southeast-1b",
                    "ap-southeast-1c",
                ],
                "Subnets": subnet_ids,
                "VPCId": vpc_id,
            }
        ]
    }

    mock_client.detach_load_balancer_from_subnets.return_value = {
        "Subnets": [
            subnet_ids[1],
            subnet_ids[2],
        ]
    }

    fail_az(az=az, dry_run=dry_run, state_path=state_path)

    mock_client.detach_load_balancer_from_subnets.assert_called_with(
        LoadBalancerName=load_balancer_name, Subnets=[subnet_ids[0]]
    )

    os.remove(state_path)


@patch("azchaosaws.elb.actions.client", autospec=True)
def test_fail_az_clb_default_vpc(client):
    mock_client = MagicMock()
    client.return_value = mock_client
    az = "ap-southeast-1a"
    dry_run = False
    state_path = "test_fail_az_clb_default_vpc.json"
    load_balancer_name = "my-load-balancer"
    subnet_ids = ["subnet-b61f49f0", "subnet-15aaab61", "subnet-0ecac448"]
    vpc_id = "vpc-a01106c2"

    mock_client.get_paginator = get_mock_paginate

    mock_client.describe_tags.return_value = {
        "TagDescriptions": [
            {
                "LoadBalancerName": load_balancer_name,
                "Tags": [
                    {"Key": "AZ_FAILURE", "Value": "True"},
                ],
            },
        ]
    }

    mock_client.describe_vpcs.return_value = {"Vpcs": [{"VpcId": vpc_id}]}

    mock_client.describe_load_balancers.return_value = {
        "LoadBalancerDescriptions": [
            {
                "LoadBalancerName": load_balancer_name,
                "AvailabilityZones": [
                    "ap-southeast-1a",
                    "ap-southeast-1b",
                    "ap-southeast-1c",
                ],
                "Subnets": subnet_ids,
                "VPCId": vpc_id,
            }
        ]
    }

    mock_client.disable_availability_zones_for_load_balancer.return_value = {
        "AvailabilityZones": [
            "ap-southeast-1b",
            "ap-southeast-1c",
        ]
    }

    fail_az(az=az, dry_run=dry_run, state_path=state_path)

    mock_client.disable_availability_zones_for_load_balancer.assert_called_with(
        LoadBalancerName=load_balancer_name, AvailabilityZones=[az]
    )

    os.remove(state_path)


def get_mock_paginate(operation_name):
    return {
        "describe_load_balancers": MagicMock(
            paginate=MagicMock(
                return_value=[
                    {
                        "LoadBalancerDescriptions": [
                            {
                                "LoadBalancerName": "my-load-balancer",
                                "AvailabilityZones": [
                                    "ap-southeast-1a",
                                    "ap-southeast-1b",
                                    "ap-southeast-1c",
                                ],
                            }
                        ]
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
                                "SubnetId": "subnet-b61f49f0",
                                "VpcId": "vpc-a01106c2",
                                "AvailabilityZone": "ap-southeast-1a",
                            },
                        ],
                    }
                ]
            )
        ),
    }.get(operation_name, MagicMock())
