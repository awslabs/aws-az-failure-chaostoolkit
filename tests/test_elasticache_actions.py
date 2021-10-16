from unittest.mock import MagicMock, patch

from azchaosaws.elasticache.actions import fail_az


@patch("azchaosaws.elasticache.actions.client", autospec=True)
def test_fail_az_non_cluster_mode_shard(client):
    mock_client = MagicMock()
    client.return_value = mock_client
    az = "ap-southeast-1a"
    dry_run = False
    replication_group_id = "my-redis-rg"
    node_group_id = "0001"

    mock_client.get_paginator = get_mock_paginate_non_cluster_mode

    mock_client.list_tags_for_resource.return_value = {
        "TagList": [
            {"Key": "AZ_FAILURE", "Value": "True"},
        ]
    }

    fail_az(az=az, dry_run=dry_run)

    mock_client.test_failover.assert_called_with(
        ReplicationGroupId=replication_group_id, NodeGroupId=node_group_id
    )


@patch("azchaosaws.elasticache.actions.client", autospec=True)
def test_fail_az_cluster_mode_shard(client):
    mock_client = MagicMock()
    client.return_value = mock_client
    az = "ap-southeast-1a"
    dry_run = False
    replication_groups = [
        {
            "replication_group_id": "my-redis-rg",
            "cache_cluster_ids": ["my-redis-rg-0001-001"],
        }
    ]
    replication_group_id = "my-redis-rg"
    node_group_id = "0001"

    mock_client.describe_replication_groups.return_value = {
        "ReplicationGroups": [
            {
                "ReplicationGroupId": replication_group_id,
                "NodeGroups": [
                    {
                        "NodeGroupId": node_group_id,
                        "NodeGroupMembers": [
                            {
                                "CacheClusterId": "my-redis-rg-0001-001",
                                "CacheNodeId": "0001",
                                "PreferredAvailabilityZone": "ap-southeast-1a",
                            },
                            {
                                "CacheClusterId": "my-redis-rg-0001-002",
                                "CacheNodeId": "0001",
                                "PreferredAvailabilityZone": "us-east-1b",
                            },
                            {
                                "CacheClusterId": "my-redis-rg-0001-003",
                                "CacheNodeId": "0001",
                                "PreferredAvailabilityZone": "us-east-1c",
                            },
                        ],
                    },
                    {
                        "NodeGroupId": "0002",
                        "NodeGroupMembers": [
                            {
                                "CacheClusterId": "my-redis-rg-0002-001",
                                "CacheNodeId": "0001",
                                "PreferredAvailabilityZone": "ap-southeast-1b",
                            },
                            {
                                "CacheClusterId": "my-redis-rg-0002-002",
                                "CacheNodeId": "0001",
                                "PreferredAvailabilityZone": "us-east-1b",
                            },
                            {
                                "CacheClusterId": "my-redis-rg-0002-003",
                                "CacheNodeId": "0001",
                                "PreferredAvailabilityZone": "us-east-1c",
                            },
                        ],
                    },
                ],
                "AutomaticFailover": "enabled",
                "MultiAZ": "enabled",
                "ClusterEnabled": True,
                "ARN": "arn:aws:elasticache:ap-southeast-1:0123456789:replicationgroup:{}".format(
                    replication_group_id
                ),
            },
        ]
    }

    mock_client.list_tags_for_resource.return_value = {
        "TagList": [
            {"Key": "AZ_FAILURE", "Value": "True"},
        ]
    }

    fail_az(az=az, dry_run=dry_run, replication_groups=replication_groups)

    mock_client.test_failover.assert_called_with(
        ReplicationGroupId=replication_group_id, NodeGroupId=node_group_id
    )


def get_mock_paginate_non_cluster_mode(operation_name):
    return {
        "describe_replication_groups": MagicMock(
            paginate=MagicMock(
                return_value=[
                    {
                        "ReplicationGroups": [
                            {
                                "ReplicationGroupId": "my-redis-rg",
                                "NodeGroups": [
                                    {
                                        "NodeGroupId": "0001",
                                        "NodeGroupMembers": [
                                            {
                                                "CacheClusterId": "my-redis-rg-001",
                                                "CacheNodeId": "0001",
                                                "PreferredAvailabilityZone": "ap-southeast-1a",
                                                "CurrentRole": "primary",
                                            },
                                            {
                                                "CacheClusterId": "my-redis-rg-002",
                                                "CacheNodeId": "0001",
                                                "CurrentRole": "replica",
                                                "PreferredAvailabilityZone": "us-east-1b",
                                            },
                                            {
                                                "CacheClusterId": "my-redis-rg-003",
                                                "CacheNodeId": "0001",
                                                "CurrentRole": "replica",
                                                "PreferredAvailabilityZone": "us-east-1c",
                                            },
                                        ],
                                    },
                                ],
                                "AutomaticFailover": "enabled",
                                "MultiAZ": "enabled",
                                "ClusterEnabled": False,
                                "ARN": "arn:aws:elasticache:ap-southeast-1:0123456789:replicationgroup:my-redis-rg",
                            },
                        ]
                    }
                ]
            )
        ),
    }.get(operation_name, MagicMock())
