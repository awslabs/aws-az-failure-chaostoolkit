import boto3
from botocore.waiter import WaiterModel, create_waiter_with_client


def cluster_available_waiter(
    client: boto3.client,
    count: int,
    cache_node_id: str = "0001",
    delay: int = 20,
    max_attempts: int = 50,
):
    """This waiter targets messages from describe_events"""

    waiter_name = "ClusterAvailable"
    waiter_config = {
        "version": 2,
        "waiters": {
            "ClusterAvailable": {
                "operation": "DescribeEvents",
                "delay": delay,
                "maxAttempts": max_attempts,
                "acceptors": [
                    {
                        "matcher": "path",
                        "expected": count,
                        "argument": "length(Events[].Message|[?contains(@, 'Finished recovery for cache nodes {}')])".format(
                            cache_node_id
                        ),
                        "state": "success",
                    }
                ],
            }
        },
    }
    waiter_model = WaiterModel(waiter_config)
    return create_waiter_with_client(waiter_name, waiter_model, client)
