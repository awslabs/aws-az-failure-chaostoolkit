# -*- coding: utf-8 -*-
from typing import List, Dict, Any

import boto3
from chaoslib.exceptions import FailedActivity
from chaoslib.types import Configuration
from logzero import logger

from azchaosaws import client
from azchaosaws.utils import args_fmt

__all__ = ["fail_az"]


@args_fmt
def fail_az(az: str = None, dry_run: bool = None, broker_ids: List[str] = None, tags: List[Dict[str, str]] = [{"AZ_FAILURE": "True"}],
            configuration: Configuration = None) -> Dict[str, Any]:
    """
    This function forces a reboot for Amazon MQ ActiveMQ running brokers that have active-standby setup (ACTIVE_STANDBY_MULTI_AZ). 
    The reboot operation is asynchronous as documented in https://docs.aws.amazon.com/amazon-mq/latest/api-reference/brokers-broker-id-reboot.html#RebootBroker
    Provide a list of broker_ids to reboot, and ensure these brokers are tagged with the key-value pair provided.

    Parameters:
        Required:
            az: an availability zone. This parameter is required although it's not used for logical purposes to reboot
            dry_run: the boolean flag to simulate a dry run or not. Setting to True will only run read only operations and not make changes to resources. (Accepted values: true | false)

        Optional:
            broker_ids: list of brokers to reboot
            tags: a list of key/value pair to identify broker(s) by (Default: [{'AZ_FAILURE': 'True'}] )

    `tags` are expected as a list of dictionary objects:
    [
        {'TagKey1': 'TagValue1'},
        {'TagKey2': 'TagValue2'},
        ...
    ]

    Output Structure:
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
        raise FailedActivity('To simulate AZ failure, you must specify'
                             'a dry_run boolean parameter to indicate if you want to run read-only operations (Accepted values: true | false)')

    if not az:
        raise FailedActivity('To simulate AZ failure, you must specify '
                             'an Availability Zone')

    logger.info("[MQ] Tags to scan ({})...".format(tags))

    mq_client = client('mq', configuration)
    fail_az_state = {"AvailabilityZone": az, "DryRun": dry_run, "Brokers": {
        "Success": {"BrokerIds": []}, "Failed": {"BrokerIds": []}}}

    logger.info("[MQ] Fetching tagged MQ brokers...")
    # Tags serve as a layer of protection for resources.
    tagged_brokers = get_brokers_by_tags(tags=tags, client=mq_client)
    logger.info("[MQ] Tagged brokers: {}".format(tagged_brokers))

    brokers = []

    if broker_ids:  # If broker_ids provided, get intersection of provided brokers and tagged brokers
        logger.info(
            "[MQ] Filtering brokers from provided broker ids ({}) and tagged brokers...".format(broker_ids))
        brokers = [tb for tb in tagged_brokers if tb["BrokerId"] in broker_ids]
    else:  # Otherwise, use tagged brokers
        logger.info("[MQ] Using tagged brokers as no broker ids provided...")
        brokers = tagged_brokers[:]

    if not brokers:
        raise FailedActivity(
            "No broker(s) with the provided tags and broker ids found...")

    # Filter only ActiveMQ brokers with ACTIVE_STANDBY_MULTI_AZ
    target_broker_ids = []
    target_broker_ids = [b["BrokerId"] for b in brokers if b["EngineType"]
                         == "ActiveMQ" and b["DeploymentMode"] == "ACTIVE_STANDBY_MULTI_AZ"]
    logger.info('[MQ] Target broker(s) for reboot ({})'.format(
        str(target_broker_ids)))
    if not target_broker_ids:
        raise FailedActivity(
            "No ActiveMQ broker(s) with ACTIVE_STANDBY_MULTI_AZ found...")

    success_brokers, failed_brokers = [], []

    for b in target_broker_ids:
        try:
            logger.warning('[MQ] Based on config provided, BROKER ({}) will reboot...'.format(
                b))
            if not dry_run:
                # [WOP]
                mq_client.reboot_broker(
                    BrokerId=b
                )
                
            success_brokers.append(b)
        except Exception as e:
            logger.error("[MQ] Failed rebooting broker ({}): {}".format(
                b, str(e)))
            failed_brokers.append(b)

    if not success_brokers:
        logger.warning(
            "[MQ] No broker(s) rebooted... Ensure that the brokers you specified are tagged with the tags you provided or tagged with the default value. Alternatively, if you did not provide broker ids, ensure you have brokers tagged.")
    else:
        logger.info("[MQ] Broker(s) that were rebooted: {}".format(
            success_brokers))

    # Add to state
    fail_az_state["Brokers"]["Success"]["BrokerIds"] = success_brokers
    fail_az_state["Brokers"]["Failed"]["BrokerIds"] = failed_brokers

    return fail_az_state


def get_brokers_by_tags(tags: List[Dict[str, str]],
                        client: boto3.client) -> List[Dict[str, any]]:
    """Fetch list of brokers that has the specified tags

    Args:
        tags (List[Dict[str, str]]): tags to cross check
        client (boto3.client): MQ client

    Raises:
        FailedActivity: [description]

    Returns:
        List[Dict[str, any]]: list of broker_ids that have the specified tags

    Returns:
        [
            {
                'BrokerArn': str,
                'BrokerId': str,
                'BrokerName': str,
                'BrokerState': 'CREATION_IN_PROGRESS'|'CREATION_FAILED'|'DELETION_IN_PROGRESS'|'RUNNING'|'REBOOT_IN_PROGRESS',
                'Created': datetime(2015, 1, 1),
                'DeploymentMode': 'SINGLE_INSTANCE'|'ACTIVE_STANDBY_MULTI_AZ'|'CLUSTER_MULTI_AZ',
                'EngineType': 'ACTIVEMQ'|'RABBITMQ',
                'HostInstanceType': str
            },
            ....
        ]
    """

    paginator = client.get_paginator('list_brokers')
    brokers = []
    for p in paginator.paginate():
        brokers = [bs for bs in p['BrokerSummaries']]

    filtered_brokers = []
    for b in brokers:
        response = client.list_tags(
            ResourceArn=b["BrokerArn"]
        )

        if response["Tags"]:
            if all(response["Tags"].get(k, None) == v for t in tags for k, v in t.items()):
                filtered_brokers.append(b)

    if not filtered_brokers:
        raise FailedActivity(
            'No broker(s) found with matching tag(s): {}.'.format(tags))

    return filtered_brokers
