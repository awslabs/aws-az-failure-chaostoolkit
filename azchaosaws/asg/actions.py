# -*- coding: utf-8 -*-
import os
from typing import Any, Dict, List

import boto3
from chaoslib.exceptions import FailedActivity
from chaoslib.types import Configuration
from logzero import logger

from azchaosaws import client
from azchaosaws.helpers import read_state, validate_fail_az_path, write_state
from azchaosaws.utils import args_fmt

__all__ = ["fail_az", "recover_az"]


@args_fmt
def fail_az(
    az: str = None,
    dry_run: bool = None,
    tags: List[Dict[str, str]] = [{"Key": "AZ_FAILURE", "Value": "True"}],
    state_path: str = "fail_az.{}.json".format(__package__.split(".", 1)[1]),
    configuration: Configuration = None,
) -> Dict[str, Any]:
    """
    This function simulates the lost of an AZ in an AWS Region for AutoScalingGroups by removing subnets of the AZ in the ASGs or update its min, max and desired
    capacity to 0 if it's only configured for scaling in a single AZ. It also suspends the process of AZ Rebalancing of the ASG.

    Conflicts with:
        eks.fail_az: Ensure that ASGs that belong to EKS clusters are not be tagged as they will be captured by this action, which will cause eks.fail_az to not be able
                     identify the worker nodes.

    Parameters:
        Required:
            az (str): An availability zone
            dry_run (bool): The boolean flag to simulate a dry run or not. Setting to True will only run read-only operations and not make changes to resources. (Accepted values: True | False)

        Optional:
            tags (List[Dict[str, str]]): A list of key-value pairs to filter the asg(s) by. (Default: [{'Key': 'AZ_FAILURE', 'Value': 'True'}])
            state_path (str): Path to generate the state data (Default: fail_az.asg.json). This file is used for recover_az (rollback).

    Return structure:
        {
            "AvailabilityZone": str,
            "DryRun": bool,
            "AutoScalingGroups": [
                {
                    "AutoScalingGroupName": str,
                    "Before": {
                        "SubnetIds": List[str],
                        "AZRebalance": bool,
                        "MinSize": int,
                        "MaxSize": int,
                        "DesiredCapacity": int
                        },
                    "After": {
                        "SubnetIds": List[str],
                        "AZRebalance": bool,
                        "MinSize": int,
                        "MaxSize": int,
                        "DesiredCapacity": int
                        }
                }
                ....
            ]
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

    # Validate state_path
    state_path = validate_fail_az_path(
        fail_if_exists=True, path=state_path, service=__package__.split(".", 1)[1]
    )

    logger.warning(
        "[ASG] Executing fail_az action with dry_run ({}) ({}).".format(
            "enabled" if dry_run else "disabled", dry_run
        )
    )

    asg_client = client("autoscaling", configuration)
    ec2_client = client("ec2", configuration)
    fail_az_state = {"AutoScalingGroups": []}
    asgs_state = []

    tagged_asgs = set()
    tagged_asgs_response = get_asg_by_tags(asg_client, tags)
    for a in tagged_asgs_response["AutoScalingGroups"]:
        tagged_asgs.add(a["AutoScalingGroupName"])
    logger.info("[ASG] Tagged ASGs ({})".format(str(tagged_asgs)))

    target_az_asgs = set(get_asgs_by_az(az, asg_client))
    logger.info("[ASG] Target AZ ASGs ({})".format(str(target_az_asgs)))

    # List of ASGs to be updated
    asgs = list(tagged_asgs.intersection(target_az_asgs))
    logger.info("[ASG] Target ASGs ({})".format(str(asgs)))

    if not asgs:
        raise FailedActivity(
            "No ASGs with the provided tags and the target AZ found..."
        )

    logger.warning(
        "[ASG] Based on config provided, AZ failure simulation will happen in ({}) for ASG(s) ({})".format(
            az, asgs
        )
    )

    # For every ASG change subnets to non target AZ subnets
    for asg in asgs:
        results = {}

        if asg_in_single_az(client=asg_client, asg=asg):
            # If ASG is for single AZ
            results = modify_capacity(client=asg_client, asg=asg, dry_run=dry_run)
        else:
            # If ASG is across multiple AZs
            results = remove_az_subnets(
                asg_client=asg_client,
                ec2_client=ec2_client,
                az=az,
                asg=asg,
                dry_run=dry_run,
            )
        # Add to state
        asgs_state.append(results)

    # Add to state
    fail_az_state["AvailabilityZone"] = az
    fail_az_state["DryRun"] = dry_run
    fail_az_state["AutoScalingGroups"] = asgs_state

    write_state(fail_az_state, state_path)

    return fail_az_state


def recover_az(
    state_path: str = "fail_az.{}.json".format(__package__.split(".", 1)[1]),
    configuration: Configuration = None,
) -> bool:
    """
    This function rolls back the ASGs that were affected by the fail_az action to its previous state. This function is dependent on the state data generated from fail_az.

    Parameters:
        Optional:
            state_path (str): Path to the state data from fail_az (Default: fail_az.asg.json)

    """

    # Validate state_path
    state_path = validate_fail_az_path(
        fail_if_exists=False, path=state_path, service=__package__.split(".", 1)[1]
    )

    fail_az_state = read_state(state_path)

    # Check if data was for dry run
    if fail_az_state["DryRun"]:
        raise FailedActivity("State file was generated from a dry run...")

    asg_client = client("autoscaling", configuration)

    for asg in fail_az_state["AutoScalingGroups"]:
        logger.warning(
            "[ASG] ({}) Based on the state file found, AZ failure rollback will happen for ASG ({})".format(
                asg["AutoScalingGroupName"], asg["AutoScalingGroupName"]
            )
        )

        if all(k in asg["Before"] for k in ("AZRebalance", "SubnetIds")):
            if asg["Before"]["AZRebalance"]:
                logger.warning(
                    "[ASG] ({}) AZRebalance process will be resumed.".format(
                        asg["AutoScalingGroupName"]
                    )
                )
            logger.warning(
                "[ASG] ({}) Subnets will be updated back to {}".format(
                    asg["AutoScalingGroupName"], asg["Before"]["SubnetIds"]
                )
            )

            # Resume AZRebalance process if not suspended before fail_az
            if asg["Before"]["AZRebalance"]:
                resume_processes(
                    client=asg_client,
                    asg_names=[asg["AutoScalingGroupName"]],
                    scaling_processes=["AZRebalance"],
                )

            # Change subnets of ASG to the subnets before fail_az
            change_subnets(
                client=asg_client,
                subnets=asg["Before"]["SubnetIds"],
                asg_names=[asg["AutoScalingGroupName"]],
            )

        if all(k in asg["Before"] for k in ("MinSize", "MaxSize", "DesiredCapacity")):
            logger.warning(
                "[ASG] ({}) MinSize, MaxSize and DesiredCapacity will be updated back to {}, {} and {}".format(
                    asg["AutoScalingGroupName"],
                    asg["Before"]["MinSize"],
                    asg["Before"]["MaxSize"],
                    asg["Before"]["DesiredCapacity"],
                )
            )

            modify_capacity(
                client=asg_client,
                asg=asg["AutoScalingGroupName"],
                min_size=asg["Before"]["MinSize"],
                max_size=asg["Before"]["MaxSize"],
                desired_cap=asg["Before"]["DesiredCapacity"],
            )

    # Remove state file upon completion
    try:
        logger.warning(
            "[ASG] Completed rollback, removing file ({}) from disk...".format(
                state_path
            )
        )
        os.remove(state_path)
    except Exception as e:
        logger.error("[ASG] Error removing file: %s", str(e), exc_info=1)

    return True


def suspend_processes(
    client: boto3.client, asg_names: List[str], scaling_processes: List[str]
) -> None:
    asgs = get_asg_by_names(client, asg_names)

    for a in asgs["AutoScalingGroups"]:
        params = dict(
            AutoScalingGroupName=a["AutoScalingGroupName"],
            ScalingProcesses=scaling_processes,
        )

        logger.debug(
            "[ASG] Suspending processes on {}".format(a["AutoScalingGroupName"])
        )
        client.suspend_processes(**params)


def resume_processes(
    client: boto3.client, asg_names: List[str], scaling_processes: List[str]
) -> None:
    asgs = get_asg_by_names(client, asg_names)

    for a in asgs["AutoScalingGroups"]:
        params = dict(
            AutoScalingGroupName=a["AutoScalingGroupName"],
            ScalingProcesses=scaling_processes,
        )

        logger.debug(
            "[ASG] Resuming processes {} on {}".format(
                scaling_processes, a["AutoScalingGroupName"]
            )
        )
        client.resume_processes(**params)


def change_subnets(client: boto3.client, subnets: List[str], asg_names: List[str]):
    asgs = get_asg_by_names(client, asg_names)

    for a in asgs["AutoScalingGroups"]:
        client.update_auto_scaling_group(
            AutoScalingGroupName=a["AutoScalingGroupName"],
            VPCZoneIdentifier=",".join(subnets),
        )


def get_asg_by_names(client: boto3.client, asg_names: List[str]) -> Dict[str, Any]:
    logger.debug("[ASG] Getting ASG(s): {}.".format(asg_names))

    paginator = client.get_paginator("describe_auto_scaling_groups")
    results = dict(AutoScalingGroups=[])
    for p in paginator.paginate(AutoScalingGroupNames=asg_names):
        for a in p["AutoScalingGroups"]:
            results["AutoScalingGroups"].append(a)

    if not results.get("AutoScalingGroups", []):
        logger.warning("[ASG] Unable to find ASG(s): {}".format(asg_names))

    valid_asgs = [a["AutoScalingGroupName"] for a in results["AutoScalingGroups"]]
    invalid_asgs = [a for a in asg_names if a not in valid_asgs]
    if invalid_asgs:
        raise FailedActivity("[ASG] Invalid ASG(s): {}".format(invalid_asgs))

    return results


def get_asg_by_tags(client: boto3.client, tags: List[Dict[str, str]]) -> Dict[str, Any]:
    params = []

    for tag in tags:
        params.extend(
            [
                {"Name": "key", "Values": [tag["Key"]]},
                {"Name": "value", "Values": [tag["Value"]]},
            ]
        )

    paginator = client.get_paginator("describe_tags")
    results = set()
    for p in paginator.paginate(Filters=params):
        for a in p["Tags"]:
            if a["ResourceType"] == "auto-scaling-group":
                results.add(a["ResourceId"])

    if not results:
        raise FailedActivity("No ASG(s) found with tag(s): {}.".format(tags))
    return get_asg_by_names(client, list(results))


def get_asgs_by_az(az: str, client: boto3.client) -> List[str]:
    logger.debug("[ASG] Searching for ASG(s) in AZ ({}).".format(az))

    paginator = client.get_paginator("describe_auto_scaling_groups")
    results = set()
    for p in paginator.paginate():
        for a in p["AutoScalingGroups"]:
            if az in a["AvailabilityZones"]:
                results.add(a["AutoScalingGroupName"])

    if not results:
        logger.info("[ASG] No ASG(s) found in AZ ({}).".format(az))
    return list(results)


def describe_subnets(
    client: boto3.client, subnet_ids: List[str], filters: List[Dict[str, Any]] = None
) -> List[Dict[str, Any]]:
    """Describes your subnets."""

    params = {}
    if filters:
        params["Filters"] = filters
    if subnet_ids:
        params["SubnetIds"] = subnet_ids

    results = []
    paginator = client.get_paginator("describe_subnets")
    for p in paginator.paginate(**params):
        for s in p["Subnets"]:
            results.append(s)

    if not results:
        logger.info("[ASG] No subnets found.")

    return results


def remove_az_subnets(
    asg_client: boto3.client,
    ec2_client: boto3.client,
    az: str,
    asg: str,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Removes az subnets from ASG and suspends AZRebalance process

    Return Structure:
    {
        "AutoScalingGroupName": str,
        "Before": {
            "SubnetIds": List[str],
            "AZRebalance": bool
        },
        "After": {
            "SubnetIds": List[str],
            "AZRebalance": bool
        }
    }
    """

    results = {}
    asg_response = get_asg_by_names(client=asg_client, asg_names=[asg])

    # Suspend AZRebalance process
    suspended_processes = asg_response["AutoScalingGroups"][0]["SuspendedProcesses"]
    logger.info("[ASG] Suspending AZRebalance processes for ({})".format(asg))

    if not dry_run:
        suspend_processes(
            client=asg_client, asg_names=[asg], scaling_processes=["AZRebalance"]
        )

    existing_subnets = asg_response["AutoScalingGroups"][0]["VPCZoneIdentifier"].split(
        ","
    )

    # Filter subnets that are NOT from the AZ of each ASG (from set a) -> list of subnets for every ASG (set B)
    existing_subnets_full = describe_subnets(
        client=ec2_client, subnet_ids=existing_subnets
    )

    non_az_subnets = [
        s["SubnetId"] for s in existing_subnets_full if s["AvailabilityZone"] != az
    ]

    logger.info(
        "[ASG] ASG ({}) will update its subnets from ({}) to ({})".format(
            asg, existing_subnets, non_az_subnets
        )
    )

    if not dry_run:
        # Change subnets of ASG to only non failed AZ subnets
        asg_client.update_auto_scaling_group(
            AutoScalingGroupName=asg, VPCZoneIdentifier=",".join(non_az_subnets)
        )

    # Return list of subnets, ASG and AZRebalance process state
    results["AutoScalingGroupName"] = asg
    results["Before"] = {
        "SubnetIds": existing_subnets,
        "AZRebalance": not any(
            sp.get("ProcessName", None) == "AZRebalance" for sp in suspended_processes
        ),
    }
    results["After"] = {"SubnetIds": non_az_subnets, "AZRebalance": False}

    return results


def asg_in_single_az(client: boto3.client, asg: str) -> bool:
    """Checks if ASG is only for single AZ. Returns True if it is, and False if it's for multiple AZs."""

    response = client.describe_auto_scaling_groups(AutoScalingGroupNames=[asg])

    return len(response["AutoScalingGroups"][0]["AvailabilityZones"]) == 1


def modify_capacity(
    client: boto3.client,
    asg: str,
    dry_run: bool = False,
    min_size=0,
    max_size=0,
    desired_cap=0,
) -> Dict[str, Any]:
    """Modify min, max and desired capacity to 0

    Return Structure:
        {
            "AutoScalingGroupName": str,
            "Before": {
                "MinSize": int,
                "MaxSize": int,
                "DesiredCapacity": int
            },
            "After": {
                "MinSize": int,
                "MaxSize": int,
                "DesiredCapacity": int
            }
        }
    """

    results = {}
    asg_response = get_asg_by_names(client, asg_names=[asg])

    orig_min_size = asg_response["AutoScalingGroups"][0]["MinSize"]
    orig_max_size = asg_response["AutoScalingGroups"][0]["MaxSize"]
    orig_desired_cap = asg_response["AutoScalingGroups"][0]["DesiredCapacity"]

    logger.info(
        "[ASG] Setting min, max and desired capacity for ({}) to {}, {} and {}".format(
            asg, min_size, max_size, desired_cap
        )
    )

    if not dry_run:
        # Update ASG min, max and desired to 0
        client.update_auto_scaling_group(
            AutoScalingGroupName=asg,
            MinSize=min_size,
            MaxSize=max_size,
            DesiredCapacity=desired_cap,
        )

    # Return list of ASG, min, max and desired cap
    results["AutoScalingGroupName"] = asg
    results["Before"] = {
        "MinSize": orig_min_size,
        "MaxSize": orig_max_size,
        "DesiredCapacity": orig_desired_cap,
    }
    results["After"] = {
        "MinSize": min_size,
        "MaxSize": max_size,
        "DesiredCapacity": desired_cap,
    }

    return results
