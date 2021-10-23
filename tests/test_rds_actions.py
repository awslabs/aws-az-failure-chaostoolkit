from unittest.mock import MagicMock, patch

from azchaosaws.rds.actions import fail_az


@patch("azchaosaws.rds.actions.client", autospec=True)
def test_fail_az_rds_basic(client):
    mock_client = MagicMock()
    client.return_value = mock_client
    az = "ap-southeast-1a"
    dry_run = False
    db_instance_identifier = "mysqlinstance"
    db_cluster_identifier = "my-cluster"

    mock_client.get_paginator = get_mock_paginate

    mock_client.describe_db_instances.return_value = {
        "DBInstances": [
            {
                "DBInstanceIdentifier": "my-post-gres-instance-1",
                "AvailabilityZone": "ap-southeast-1a",
            },
        ]
    }

    fail_az(az=az, dry_run=dry_run)

    mock_client.reboot_db_instance.assert_called_with(
        DBInstanceIdentifier=db_instance_identifier, ForceFailover=True
    )

    mock_client.failover_db_cluster.assert_called_with(
        DBClusterIdentifier=db_cluster_identifier
    )


def get_mock_paginate(operation_name):
    return {
        "describe_db_instances": MagicMock(
            paginate=MagicMock(
                return_value=[
                    {
                        "DBInstances": [
                            {
                                "DBInstanceIdentifier": "mysqlinstance",
                                "AvailabilityZone": "ap-southeast-1a",
                                "MultiAZ": True,
                                "TagList": [{"Key": "AZ_FAILURE", "Value": "True"}],
                            },
                        ]
                    }
                ]
            )
        ),
        "describe_db_clusters": MagicMock(
            paginate=MagicMock(
                return_value=[
                    {
                        "DBClusters": [
                            {
                                "DBClusterIdentifier": "my-cluster",
                                "MultiAZ": True,
                                "DBClusterMembers": [
                                    {
                                        "DBInstanceIdentifier": "my-post-gres-instance-1",
                                        "IsClusterWriter": True,
                                    },
                                    {
                                        "DBInstanceIdentifier": "my-post-gres-instance-2",
                                        "IsClusterWriter": False,
                                    },
                                ],
                                "TagList": [{"Key": "AZ_FAILURE", "Value": "True"}],
                            },
                        ]
                    }
                ]
            )
        ),
    }.get(operation_name, MagicMock())
