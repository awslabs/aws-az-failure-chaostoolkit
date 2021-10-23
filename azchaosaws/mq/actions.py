# -*- coding: utf-8 -*-
from concurrent.futures import wait
from concurrent.futures.thread import ThreadPoolExecutor
from typing import Any, Dict, List

import boto3
from chaoslib.exceptions import FailedActivity
from chaoslib.types import Configuration
from logzero import logger

from azchaosaws import client
from azchaosaws.utils import args_fmt

__all__ = ["fail_az"]


@args_fmt
def fail_az(
    az: str = None,
    dry_run: bool = None,
    tags: List[Dict[str, str]] = [{"AZ_FAILURE": "True"}],
    configuration: Configuration = None,
) -> Dict[str, Any]:
    """
    This function forces a reboot for Amazon MQ (ActiveMQ) running brokers that have an active-standby setup (ACTIVE_STANDBY_MULTI_AZ).
    The reboot operation is asynchronous as documented in https://docs.aws.amazon.com/amazon-mq/latest/api-reference/brokers-broker-id-reboot.html#RebootBroker
    Please ensure that your brokers are tagged with the key-value pairs provided.

    Parameters:
        Required:
            az (str): An availability zone
            dry_run (bool): The boolean flag to simulate a dry run or not. Setting to True will only run read-only operations and not make changes to resources. (Accepted values: True | False)

        Optional:
            tags (List[Dict[str, str]]): A list of key-value pairs to filter the broker(s) by. (Default: [{'AZ_FAILURE': 'True'}])

    Return Structure:
        {
            "AvailabilityZone": str,
            "DryRun": bool,
            "Brokers":
                    {
                        "Success": {
                            "BrokerIds": List[str]
                        },
                        "Failed": {
                            "BrokerIds": List[str]
                        }
                    }
        }
    """

    if dry_run is None:
        raise FailedActivity(
            "To simulate AZ failure, you must specify"
            "a dry_run boolean parameter to indicate if you want to run read-only operations (Accepted values: true | false)"
        )

    if not az:
        raise FailedActivity(
            "To simulate AZ failure, you must specify an Availability Zone"
        )

    mq_client = client("mq", configuration)
    ec2_client = client("ec2", configuration)

    fail_az_state = {
        "AvailabilityZone": az,
        "DryRun": dry_run,
        "Brokers": {"Success": {"BrokerIds": []}, "Failed": {"BrokerIds": []}},
    }

    logger.info("[MQ] Fetching tagged MQ brokers...")

    tagged_brokers = get_brokers_by_tags_and_az(
        mq_client=mq_client, ec2_client=ec2_client, tags=tags, az=az
    )

    activemq_brokers = []
    success_activemq_brokers, failed_activemq_brokers = [], []

    filtered_brokers = list(
        filter(
            lambda x: x["EngineType"] == "ActiveMQ"
            and x["DeploymentMode"] == "ACTIVE_STANDBY_MULTI_AZ",
            tagged_brokers,
        )
    )
    activemq_brokers = [b["BrokerId"] for b in filtered_brokers]

    if activemq_brokers:
        logger.warning(
            "[MQ] Based on the config provided, broker(s) ({}) will reboot".format(
                str(activemq_brokers)
            )
        )
    else:
        raise FailedActivity(
            """No ActiveMQ broker(s) with the provided tags and configured with Active-Standby MultiAZ deployment found...
Ensure that the brokers you specified are tagged with the tags you provided or tagged with the default value."""
        )

    if not dry_run:
        executor = ThreadPoolExecutor()
        futures = [
            executor.submit(
                reboot_broker,
                mq_client,
                i,
                success_activemq_brokers,
                failed_activemq_brokers,
            )
            for i in activemq_brokers
        ]
        wait(futures)

        if success_activemq_brokers:
            logger.info(
                "[MQ] Broker(s) that were rebooted: {} count({})".format(
                    success_activemq_brokers, len(success_activemq_brokers)
                )
            )

        if failed_activemq_brokers:
            logger.info(
                "[MQ] Broker(s) that failed to reboot: {} count({})".format(
                    failed_activemq_brokers, len(failed_activemq_brokers)
                )
            )

    # Add to state
    fail_az_state["Brokers"]["Success"]["BrokerIds"] = success_activemq_brokers
    fail_az_state["Brokers"]["Failed"]["BrokerIds"] = failed_activemq_brokers

    return fail_az_state


def get_brokers_by_tags_and_az(
    mq_client: boto3.client,
    ec2_client: boto3.client,
    tags: List[Dict[str, str]],
    az: str,
) -> List[Dict[str, Any]]:
    """Fetch list of brokers that has the specified tags

    Args:
        tags (List[Dict[str, str]]): tags to cross check
        client (boto3.client): MQ client
        az: (str): availability zone

    Returns:
        List[Dict[str, Any]]: list of brokers that have the specified tags

    Return Structure:
        [
            {
                'BrokerArn': str,
                'BrokerId': str,
                'BrokerName': str,
                'BrokerState': 'CREATION_IN_PROGRESS'|'CREATION_FAILED'|'DELETION_IN_PROGRESS'|'RUNNING'|'REBOOT_IN_PROGRESS',
                'Created': datetime(2015, 1, 1),
                'DeploymentMode': 'SINGLE_INSTANCE'|'ACTIVE_STANDBY_MULTI_AZ'|'CLUSTER_MULTI_AZ',
                'EngineType': 'ActiveMQ'|'RABBITMQ',
                'HostInstanceType': str
            },
            ....
        ]
    """

    paginator = mq_client.get_paginator("list_brokers")
    brokers = []
    for p in paginator.paginate():
        brokers = [bs for bs in p["BrokerSummaries"]]

    filtered_brokers = []
    for b in brokers:
        response = mq_client.list_tags(ResourceArn=b["BrokerArn"])

        if response["Tags"]:
            if all(
                response["Tags"].get(k, None) == v for t in tags for k, v in t.items()
            ):

                broker_subnet_ids = mq_client.describe_broker(BrokerId=b["BrokerId"])[
                    "SubnetIds"
                ]

                az_subnets = ec2_client.describe_subnets(
                    Filters=[
                        {"Name": "availability-zone", "Values": [az]},
                    ],
                    SubnetIds=broker_subnet_ids,
                )["Subnets"]

                if not az_subnets:
                    continue

                filtered_brokers.append(b)

    if not filtered_brokers:
        raise FailedActivity(
            "No broker(s) found with matching tag(s) and az: {} {}.".format(tags, az)
        )

    return filtered_brokers


def reboot_broker(
    client: boto3.client,
    broker_id: str,
    success_results: List[str],
    failed_results: List[str],
) -> None:
    try:
        logger.warning("[MQ] Rebooting broker '{}'".format(broker_id))
        response = client.reboot_broker(BrokerId=broker_id)
        logger.debug(response)
        success_results.append(broker_id)
    except Exception as e:
        logger.error(
            "[MQ] Failed rebooting broker '{}': '{}'".format(broker_id, str(e))
        )
        failed_results.append(broker_id)
