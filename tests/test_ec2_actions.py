import os

from unittest.mock import MagicMock, patch
from azchaosaws.ec2.actions import fail_az


@patch("azchaosaws.ec2.actions.client", autospec=True)
def test_fail_az_normal_instance(client):
    ec2_client = MagicMock()
    client.return_value = ec2_client
    az = "ap-southeast-1a"
    dry_run = False
    failure_type = "instance"
    state_path = "test_fail_az_normal_instance.json"
    normal_inst_id = "i-12345678901234567"

    ec2_client.describe_instances.return_value = {
        "Reservations": [
            {
                "Instances": [
                    {"InstanceId": normal_inst_id, "InstanceLifecycle": "normal"}
                ]
            }
        ]
    }

    fail_az(az=az, dry_run=dry_run, failure_type=failure_type, state_path=state_path)

    ec2_client.stop_instances.assert_called_with(
        InstanceIds=[normal_inst_id], Force=True
    )

    os.remove(state_path)


@patch("azchaosaws.ec2.actions.client", autospec=True)
def test_fail_az_spot_instance(client):
    ec2_client = MagicMock()
    client.return_value = ec2_client
    az = "ap-southeast-1a"
    dry_run = False
    failure_type = "instance"
    state_path = "test_fail_az_spot_instance.json"
    one_time_spot_inst_id = "i-01234567123456789"
    persistent_spot_inst_id = "i-98765432109876543"
    one_time_spot_instance_req_id = "sir-1a2b3c4d"
    persistent_spot_instance_req_id = "sir-2b2b3c4d"

    ec2_client.describe_instances.return_value = {
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

    ec2_client.describe_spot_instance_requests.return_value = {
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

    fail_az(az=az, dry_run=dry_run, failure_type=failure_type, state_path=state_path)

    ec2_client.stop_instances.assert_called_with(
        InstanceIds=[persistent_spot_inst_id], Force=True
    )
    ec2_client.cancel_spot_instance_requests.assert_called_with(
        SpotInstanceRequestIds=[one_time_spot_instance_req_id]
    )
    ec2_client.terminate_instances.assert_called_with(
        InstanceIds=[one_time_spot_inst_id]
    )

    os.remove(state_path)
