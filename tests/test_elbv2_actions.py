import os
from unittest.mock import MagicMock, patch

from azchaosaws.elbv2.actions import fail_az


@patch("azchaosaws.elbv2.actions.client", autospec=True)
def test_fail_az_alb(client):
    mock_client = MagicMock()
    client.return_value = mock_client
    az = "ap-southeast-1a"
    dry_run = False
    state_path = "test_fail_az_alb.json"
    load_balancer_name = "my-load-balancer"
    resource_arn = "arn:aws:elasticloadbalancing:ap-southeast-1:123456789012:loadbalancer/app/my-load-balancer/50dc6c495c0c9188"
    subnet_ids = ["subnet-0ecac448", "subnet-15aaab61", "subnet-b61f49f0"]
    remaining_subnets = subnet_ids[1:3]

    mock_client.get_paginator = get_mock_paginate

    mock_client.describe_tags.return_value = {
        "TagDescriptions": [
            {
                "ResourceArn": resource_arn,
                "Tags": [
                    {"Key": "AZ_FAILURE", "Value": "True"},
                ],
            },
        ]
    }

    mock_client.describe_load_balancers.return_value = {
        "LoadBalancers": [
            {
                "LoadBalancerArn": resource_arn,
                "LoadBalancerName": load_balancer_name,
                "Type": "application",
                "State": {"Code": "active"},
                "AvailabilityZones": [
                    {"ZoneName": "ap-southeast-1a", "SubnetId": "subnet-0ecac448"},
                    {"ZoneName": "ap-southeast-1b", "SubnetId": "subnet-15aaab61"},
                    {"ZoneName": "ap-southeast-1c", "SubnetId": "subnet-b61f49f0"},
                ],
            },
        ]
    }

    fail_az(az=az, dry_run=dry_run, state_path=state_path)

    mock_client.set_subnets.assert_called_with(
        LoadBalancerArn=resource_arn, Subnets=remaining_subnets
    )

    os.remove(state_path)


def get_mock_paginate(operation_name):
    return {
        "describe_load_balancers": MagicMock(
            paginate=MagicMock(
                return_value=[
                    {
                        "LoadBalancers": [
                            {
                                "LoadBalancerArn": "arn:aws:elasticloadbalancing:ap-southeast-1:123456789012:loadbalancer/app/my-load-balancer/50dc6c495c0c9188",
                                "LoadBalancerName": "my-load-balancer",
                                "Type": "application",
                                "AvailabilityZones": [
                                    {
                                        "ZoneName": "ap-southeast-1a",
                                        "SubnetId": "subnet-0ecac448",
                                    },
                                    {
                                        "ZoneName": "ap-southeast-1b",
                                        "SubnetId": "subnet-15aaab61",
                                    },
                                    {
                                        "ZoneName": "ap-southeast-1c",
                                        "SubnetId": "subnet-b61f49f0",
                                    },
                                ],
                            },
                        ]
                    }
                ]
            )
        ),
    }.get(operation_name, MagicMock())
