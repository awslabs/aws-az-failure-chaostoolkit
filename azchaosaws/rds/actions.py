# -*- coding: utf-8 -*-
from typing import Any, Dict, List

from chaoslib.exceptions import FailedActivity
from chaoslib.types import Configuration

from azchaosaws import client
from azchaosaws.utils import args_fmt
from logzero import logger

__all__ = ["fail_az"]


@args_fmt
def fail_az(az: str = None, dry_run: bool = None, tags: List[Dict[str, str]] = [{"Key": "AZ_FAILURE", "Value": "True"}],
            configuration: Configuration = None) -> Dict[str, Any]:
    """
    Reboots and forces a failover of your RDS instance to another AZ. Only RDS instances with the corresponding tags and is in the target AZ,
    with Multi-AZ enabled will be impacted.

    Parameters:
        Required:
            az: an availability zone
            dry_run: the boolean flag to simulate a dry run or not. Setting to True will only run read only operations and not make changes to resources. (Accepted values: true | false)

        Optional:
            tags: a list of key/value pair to identify rds(s) by (Default: [{'Key': 'AZ_FAILURE', 'Value': 'True'}])

    `tags` are expected as a list of dict:
    [
        {'Key': 'TagKey1', 'Value': 'TagValue1'},
        {'Key': 'TagKey2', 'Value': 'TagValue2'},
        ...
    ]

    Note: This function is stateless as compared to EC2, ASG, EKS. It does not produce state files for rollback.

    Output Structure:
    {
        "AvailabilityZone": str,
        "DryRun": bool,
        "DBInstances": 
                {
                    "Success": {
                        "DBInstanceIdentifiers": List[str]
                    },
                    "Failed": {
                        "DBInstanceIdentifiers": List[str]
                    }
                }
    }
    """

    if dry_run is None:
        raise FailedActivity('To simulate AZ failure, you must specify'
                             'a dry_run boolean parameter to indicate if you want to run read-only operations (Accepted values: true | false)')

    if not az:
        raise FailedActivity(
            "you must specify the az"
        )

    rds_client = client("rds", configuration)
    fail_az_state = {"AvailabilityZone": az, "DryRun": dry_run, "DBInstances": {
        "Success": {"DBInstanceIdentifiers": []}, "Failed": {"DBInstanceIdentifiers": []}}}

    logger.info("[RDS] Fetching DB instances...")

    success_failover_dbs, failed_dbs = [], []

    paginator = rds_client.get_paginator('describe_db_instances')

    for p in paginator.paginate():
        for db in p['DBInstances']:
            if all(t in db['TagList'] for t in tags):
                if db['AvailabilityZone'] == az and db['MultiAZ']:
                    logger.info(
                        '[RDS] Database %s found in %s', db[
                            'DBInstanceIdentifier'], db['AvailabilityZone']
                    )

                    try:
                        logger.warning('[RDS] Based on config provided, RDS {} will reboot with a force failover'.format(
                            db['DBInstanceIdentifier']))
                        if not dry_run:
                            # [WOP]
                            reboot_db_instance_response = rds_client.reboot_db_instance(
                                DBInstanceIdentifier=db['DBInstanceIdentifier'],
                                ForceFailover=True
                            )
                            logger.debug(reboot_db_instance_response)
                            
                        success_failover_dbs.append(db['DBInstanceIdentifier'])
                    except Exception as e:
                        logger.error("failed issuing a reboot of db instance '{}': '{}'".format(
                            db['DBInstanceIdentifier'], str(e)))
                        failed_dbs.append(db['DBInstanceIdentifier'])

    if not success_failover_dbs:
        logger.warning(
            "[RDS] No DB instances to failover... Ensure that the DBs in the AZ you specified are tagged with the tag filter you provided or tagged with the default value.")
    else:
        logger.info("[RDS] DB instances that was forced to failover: {} count({})".format(
            success_failover_dbs, len(success_failover_dbs)))

    # Add to state
    fail_az_state["DBInstances"]["Success"]["DBInstanceIdentifiers"] = success_failover_dbs
    fail_az_state["DBInstances"]["Failed"]["DBInstanceIdentifiers"] = failed_dbs

    return fail_az_state