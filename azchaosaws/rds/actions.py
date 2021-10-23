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
    tags: List[Dict[str, str]] = [{"Key": "AZ_FAILURE", "Value": "True"}],
    configuration: Configuration = None,
) -> Dict[str, Any]:
    """
    Reboots and forces a failover of your RDS instances (including Aurora single-master clusters) to another AZ. Only RDS instances
    and/or DB clusters with the corresponding tags and is in the target AZ
    with Multi-AZ enabled will be impacted.

    Parameters:
        Required:
            az (str): An availability zone
            dry_run (bool): The boolean flag to simulate a dry run or not. Setting to True will only run read-only operations and not make changes to resources. (Accepted values: True | False)

        Optional:
            tags (List[Dict[str, str]]): A list of key-value pairs to filter the RDS instance(s) and/or DB cluster(s) by. (Default: [{'Key': 'AZ_FAILURE', 'Value': 'True'}])

    Return Structure:
        {
            "AvailabilityZone": str,
            "DryRun": bool,
            "DBInstances":
                    {
                        "Success": {
                            "DBInstanceIdentifiers": List[str],
                            "DBClusterIdentifiers": List[str]
                        },
                        "Failed": {
                            "DBInstanceIdentifiers": List[str],
                            "DBClusterIdentifiers": List[str]
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
        raise FailedActivity("you must specify the az")

    rds_client = client("rds", configuration)
    fail_az_state = {
        "AvailabilityZone": az,
        "DryRun": dry_run,
        "DBInstances": {
            "Success": {"DBInstanceIdentifiers": [], "DBClusterIdentifiers": []},
            "Failed": {"DBInstanceIdentifiers": [], "DBClusterIdentifiers": []},
        },
    }

    logger.info("[RDS] Fetching DB instances...")

    db_instances, db_clusters = [], []
    success_failover_dbs, failed_dbs = [], []
    success_failover_clusters, failed_clusters = [], []

    paginator = rds_client.get_paginator("describe_db_instances")

    for p in paginator.paginate():
        for db in p["DBInstances"]:
            if all(t in db["TagList"] for t in tags):
                if db["AvailabilityZone"] == az and db["MultiAZ"]:
                    logger.info(
                        "[RDS] Database %s found in %s",
                        db["DBInstanceIdentifier"],
                        db["AvailabilityZone"],
                    )
                    db_instances.append(db["DBInstanceIdentifier"])

    logger.info("[RDS] Fetching DB clusters...")

    paginator = rds_client.get_paginator("describe_db_clusters")

    for p in paginator.paginate():
        for cluster in p["DBClusters"]:
            writer_az, reader_azs = str(), []
            if all(t in cluster["TagList"] for t in tags):
                if cluster["MultiAZ"]:
                    for member in cluster["DBClusterMembers"]:
                        resp = rds_client.describe_db_instances(
                            DBInstanceIdentifier=member["DBInstanceIdentifier"]
                        )
                        if resp["DBInstances"] and member["IsClusterWriter"]:
                            writer_az = resp["DBInstances"][0]["AvailabilityZone"]
                            if writer_az != az:
                                logger.warning(
                                    "[RDS] DB cluster {} writer not in target AZ".format(
                                        cluster["DBClusterIdentifier"]
                                    )
                                )
                                break
                        else:
                            reader_azs.append(
                                resp["DBInstances"][0]["AvailabilityZone"]
                            )
                    if not any(writer_az == reader_az for reader_az in reader_azs):
                        logger.debug(
                            "[RDS] Database cluster %s found with primary in %s",
                            cluster["DBClusterIdentifier"],
                            writer_az,
                        )
                        db_clusters.append(cluster["DBClusterIdentifier"])

    logger.warning(
        "[RDS] Based on config provided, RDS db instance(s) {} will reboot with a force failover".format(
            db_instances
        )
    )

    logger.warning(
        "[RDS] Based on config provided, DB cluster(s) {} will failover".format(
            db_clusters
        )
    )

    if not dry_run:
        executor = ThreadPoolExecutor()
        reboot_futures = [
            executor.submit(
                reboot_db_instance,
                rds_client,
                i,
                True,
                success_failover_dbs,
                failed_dbs,
            )
            for i in db_instances
        ]
        failover_futures = [
            executor.submit(
                failover_db_cluster,
                rds_client,
                i,
                success_failover_clusters,
                failed_clusters,
            )
            for i in db_clusters
        ]
        futures = [*reboot_futures, *failover_futures]
        wait(futures)

        if not success_failover_dbs:
            logger.warning(
                "[RDS] No DB instances to failover... Ensure that the DBs in the AZ you specified are tagged with the tag filter you provided or tagged with the default value."
            )
        else:
            logger.info(
                "[RDS] DB instances that was forced to failover: {} count({})".format(
                    success_failover_dbs, len(success_failover_dbs)
                )
            )

        if not success_failover_clusters:
            logger.warning(
                """[RDS] No DB clusters to failover... Ensure that the DB cluster(s) in with primary in the AZ you specified are tagged with the tag
                 filter you provided or tagged with the default value."""
            )
        else:
            logger.info(
                "[RDS] DB clusters that was forced to failover: {} count({})".format(
                    success_failover_clusters, len(success_failover_clusters)
                )
            )

    # Add to state
    fail_az_state["DBInstances"]["Success"][
        "DBInstanceIdentifiers"
    ] = success_failover_dbs
    fail_az_state["DBInstances"]["Failed"]["DBInstanceIdentifiers"] = failed_dbs
    fail_az_state["DBInstances"]["Success"][
        "DBClusterIdentifiers"
    ] = success_failover_clusters
    fail_az_state["DBInstances"]["Failed"]["DBClusterIdentifiers"] = failed_clusters

    return fail_az_state


def reboot_db_instance(
    client: boto3.client,
    db_instance_identifier: str,
    force_failover: bool,
    success_results: List[str],
    failed_results: List[str],
) -> None:
    try:
        logger.warning(
            "[RDS] Rebooting RDS db instance {}".format(db_instance_identifier)
        )
        response = client.reboot_db_instance(
            DBInstanceIdentifier=db_instance_identifier,
            ForceFailover=force_failover,
        )
        logger.debug(response)
        success_results.append(db_instance_identifier)
    except Exception as e:
        logger.error(
            "failed issuing a reboot of db instance '{}': '{}'".format(
                db_instance_identifier, str(e)
            )
        )
        failed_results.append(db_instance_identifier)


def failover_db_cluster(
    client: boto3.client,
    db_cluster_identifier: str,
    success_results: List[str],
    failed_results: List[str],
) -> None:
    try:
        logger.warning("[RDS] Failing over db cluster {}".format(db_cluster_identifier))
        response = client.failover_db_cluster(DBClusterIdentifier=db_cluster_identifier)
        logger.debug(response)
        success_results.append(db_cluster_identifier)
    except Exception as e:
        logger.error(
            "failed trying to failover for db cluster '{}': '{}'".format(
                db_cluster_identifier, str(e)
            )
        )
        failed_results.append(db_cluster_identifier)
