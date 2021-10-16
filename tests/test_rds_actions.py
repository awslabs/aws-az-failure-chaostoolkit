from unittest.mock import MagicMock, patch

from azchaosaws.rds.actions import fail_az


@patch("azchaosaws.rds.actions.client", autospec=True)
def test_fail_az_rds_basic(client):
    mock_client = MagicMock()
    client.return_value = mock_client
    az = "ap-southeast-1a"
    dry_run = False
    db_instance_identifier = "mysqlinstance"
    multi_az = True
    tags = [{"Key": "AZ_FAILURE", "Value": "True"}]

    mock_client.get_paginator.return_value.paginate.return_value = [
        {
            "DBInstances": [
                {
                    "DBInstanceIdentifier": db_instance_identifier,
                    "AvailabilityZone": az,
                    "MultiAZ": multi_az,
                    "TagList": tags,
                },
            ]
        }
    ]

    fail_az(az=az, dry_run=dry_run)

    mock_client.reboot_db_instance.assert_called_with(
        DBInstanceIdentifier=db_instance_identifier, ForceFailover=True
    )
