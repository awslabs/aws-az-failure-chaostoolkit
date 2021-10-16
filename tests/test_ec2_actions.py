import os
from unittest.mock import MagicMock, patch

from azchaosaws.ec2.actions import fail_az


@patch("azchaosaws.ec2.actions.client", autospec=True)
def test_fail_az_normal_instance(client):
    mock_client = MagicMock()
    client.return_value = mock_client
    az = "ap-southeast-1a"
    dry_run = False
    failure_type = "instance"
    state_path = "test_fail_az_normal_instance.json"
    normal_inst_id = "i-12345678901234567"

    mock_client.describe_instances.return_value = {
        "Reservations": [
            {
                "Instances": [
                    {"InstanceId": normal_inst_id, "InstanceLifecycle": "normal"}
                ]
            }
        ]
    }

    fail_az(az=az, dry_run=dry_run, failure_type=failure_type, state_path=state_path)

    mock_client.stop_instances.assert_called_with(
        InstanceIds=[normal_inst_id], Force=True
    )

    os.remove(state_path)


@patch("azchaosaws.ec2.actions.client", autospec=True)
def test_fail_az_spot_instance(client):
    mock_client = MagicMock()
    client.return_value = mock_client
    az = "ap-southeast-1a"
    dry_run = False
    failure_type = "instance"
    state_path = "test_fail_az_spot_instance.json"
    one_time_spot_inst_id = "i-01234567123456789"
    persistent_spot_inst_id = "i-98765432109876543"
    one_time_spot_instance_req_id = "sir-1a2b3c4d"
    persistent_spot_instance_req_id = "sir-2b2b3c4d"

    mock_client.describe_instances.return_value = {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": one_time_spot_inst_id,
                        "InstanceLifecycle": "spot",
                        "SpotInstanceRequestId": one_time_spot_instance_req_id,
                    },
                    {
                        "InstanceId": persistent_spot_inst_id,
                        "InstanceLifecycle": "spot",
                        "SpotInstanceRequestId": persistent_spot_instance_req_id,
                    },
                ]
            }
        ]
    }

    mock_client.get_paginator.return_value.paginate.return_value = [
        {
            "SpotInstanceRequests": [
                {
                    "InstanceId": one_time_spot_inst_id,
                    "SpotInstanceRequestId": one_time_spot_instance_req_id,
                    "Type": "one-time",
                },
                {
                    "InstanceId": persistent_spot_inst_id,
                    "SpotInstanceRequestId": persistent_spot_instance_req_id,
                    "Type": "persistent",
                },
            ],
        }
    ]

    fail_az(az=az, dry_run=dry_run, failure_type=failure_type, state_path=state_path)

    mock_client.stop_instances.assert_called_with(
        InstanceIds=[persistent_spot_inst_id], Force=True
    )
    mock_client.cancel_spot_instance_requests.assert_called_with(
        SpotInstanceRequestIds=[one_time_spot_instance_req_id]
    )
    mock_client.terminate_instances.assert_called_with(
        InstanceIds=[one_time_spot_inst_id]
    )

    os.remove(state_path)


@patch("azchaosaws.ec2.actions.client", autospec=True)
def test_fail_az_network(client):
    mock_client = MagicMock()
    client.return_value = mock_client
    az = "ap-southeast-1a"
    dry_run = False
    failure_type = "network"
    state_path = "test_fail_az_network.json"
    subnet_id = "subnet-b61f49f0"
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

    mock_client.get_paginator.return_value.paginate.return_value = [
        {
            "Subnets": [
                {"SubnetId": subnet_id, "VpcId": vpc_id, "AvailabilityZone": az}
            ],
        }
    ]

    mock_client.describe_network_acls.return_value = {
        "NetworkAcls": [
            {
                "Associations": [
                    {
                        "NetworkAclAssociationId": network_association_id,
                        "NetworkAclId": acl_id,
                        "SubnetId": subnet_id,
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

    fail_az(az=az, dry_run=dry_run, failure_type=failure_type, state_path=state_path)

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
