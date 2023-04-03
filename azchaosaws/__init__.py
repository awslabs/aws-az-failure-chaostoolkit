# -*- coding: utf-8 -*-

"""Top-level package for aws-az-failure-chaostoolkit."""

import os
from typing import List

import boto3
from chaoslib.discovery.discover import discover_actions, initialize_discovery_result
from chaoslib.exceptions import InterruptExecution
from chaoslib.types import Configuration, DiscoveredActivities, Discovery
from logzero import logger

__all__ = ["discover", "__version__", "client"]
__version__ = "0.1.9"


def discover(discover_system: bool = True) -> Discovery:
    """
    Discover capabilities offered by this extension.
    """
    logger.info("Discovering capabilities from aws-az-failure-chaostoolkit")

    discovery = initialize_discovery_result(
        "aws-az-failure-chaostoolkit", __version__, "aws"
    )
    discovery["activities"].extend(__load_exported_activities())

    return discovery


def client(resource_name: str, configuration: Configuration = None):
    """
    Creates a low-level AWS service client.
    """
    configuration = configuration or {}
    params = dict()

    region = configuration.get("aws_region")
    if not region:
        region = os.getenv("AWS_REGION", os.getenv("AWS_DEFAULT_REGION"))
        if not region:
            raise InterruptExecution("AWS requires a region to be set...")

    if region:
        logger.debug("Using AWS region '{}'".format(region))
        params["region_name"] = region

    session = boto3.Session(**params)

    return session.client(resource_name, **params)


###############################################################################
# Private functions
###############################################################################
def __load_exported_activities() -> List[DiscoveredActivities]:
    """
    Extract metadata from actions exposed by this extension.
    """
    activities = []
    activities.extend(discover_actions("azchaosaws.ec2.actions"))
    activities.extend(discover_actions("azchaosaws.eks.actions"))
    activities.extend(discover_actions("azchaosaws.elbv2.actions"))
    activities.extend(discover_actions("azchaosaws.asg.actions"))
    activities.extend(discover_actions("azchaosaws.rds.actions"))
    activities.extend(discover_actions("azchaosaws.elasticache.actions"))
    activities.extend(discover_actions("azchaosaws.mq.actions"))
    return activities
