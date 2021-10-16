from unittest.mock import MagicMock, patch

from azchaosaws.mq.actions import fail_az


@patch("azchaosaws.mq.actions.client", autospec=True)
def test_fail_az_activemq(client):
    mock_client = MagicMock()
    client.return_value = mock_client
    az = "ap-southeast-1a"
    dry_run = False
    broker_id = "b-1234a5b6-78cd-901e-2fgh-3i45j6k178l9"

    mock_client.get_paginator = get_mock_paginate

    mock_client.list_tags.return_value = {"Tags": {"AZ_FAILURE": "True"}}

    fail_az(az=az, dry_run=dry_run)

    mock_client.reboot_broker.assert_called_with(BrokerId=broker_id)


def get_mock_paginate(operation_name):
    return {
        "list_brokers": MagicMock(
            paginate=MagicMock(
                return_value=[
                    {
                        "BrokerSummaries": [
                            {
                                "BrokerArn": "arn:aws:mq:ap-southeast-1:123456789012:broker:MyBroker:b-1234a5b6-78cd-901e-2fgh-3i45j6k178l9",
                                "BrokerId": "b-1234a5b6-78cd-901e-2fgh-3i45j6k178l9",
                                "BrokerName": "MyBroker",
                                "BrokerState": "RUNNING",
                                "DeploymentMode": "ACTIVE_STANDBY_MULTI_AZ",
                                "EngineType": "ActiveMQ",
                            },
                        ]
                    }
                ]
            )
        ),
    }.get(operation_name, MagicMock())
