# -*- coding: utf-8 -*-
import os
from typing import Any, Dict, List

import boto3
from botocore.exceptions import ClientError
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
    This function simulates the lost of an AZ in an AWS Region for LBs by disabling the failed az subnets in the ALB. Does not support NLBs.

    Parameters:
        Required:
            az (str): An availability zone
            dry_run (bool): The boolean flag to simulate a dry run or not. Setting to True will only run read-only operations and not make changes to resources. (Accepted values: True | False)

        Optional:
            tags (List[Dict[str, str]]): A list of key-value pairs to filter the ELBv2(s) by. (Default: [{'Key': 'AZ_FAILURE', 'Value': 'True'}])
            state_path (str): Path to generate the state data (Default: fail_az.elbv2.json). This file is used for recover_az (rollback).

    Return Structure:
        {
            "AvailabilityZone": str,
            "DryRun": bool,
            "LoadBalancers": [
                {
                    "LoadBalancerName": str,
                    "Type: str,
                    "Before": {
                        "SubnetIds": List[str]
                    },
                    "After": {
                        "SubnetIds": List[str]
                    }
                }
            ]
        }
    """

    if dry_run is None:
        raise FailedActivity(
            "To simulate AZ failure, you must specify"
            "a dry_run boolean parameter to indicate if you want to run read-only operations. (Accepted values: true | false)"
        )

    if not az:
        raise FailedActivity(
            "To simulate AZ failure, you must specify an Availability Zone"
        )

    # Validate state_path
    state_path = validate_fail_az_path(
        fail_if_exists=True, path=state_path, service=__package__.split(".", 1)[1]
    )

    elbv2_client = client("elbv2", configuration)
    fail_az_state = {"LoadBalancers": []}
    lbs_state = []

    target_az_lb_arns = get_lbs_by_az(elbv2_client, az)

    if not target_az_lb_arns:
        logger.warning("[ELBV2] No LBs in the target AZ found...")
        raise FailedActivity("[ELBV2] No LBs in the target AZ found...")

    lb_arns = filter_lbs_by_tags(elbv2_client, target_az_lb_arns, tags)

    if not lb_arns:
        logger.warning(
            "[ELBV2] No LBs with the provided tags and the target AZ found..."
        )
        raise FailedActivity(
            "[ELBV2] No LBs with the provided tags and the target AZ found..."
        )

    logger.warning(
        "[ELBV2] Based on config provided, AZ failure simulation will happen in {} for LB(s) {}".format(
            az, lb_arns
        )
    )

    for lb_arn in lb_arns:
        lb_state = {}
        lb_block = get_lb_subnets_by_az(elbv2_client, az, lb_arn)

        if lb_block["Type"] != "application":
            logger.warning(
                "[ELBV2] Skipping ELB | LoadBalancerName: {} | ARN: {} | Type: {} | as it is not an Application Load Balancer".format(
                    lb_block["LoadBalancerName"], lb_arn, lb_block["Type"]
                )
            )
            continue

        if lb_block["TargetAZSubnetIds"]:
            original_subnets = set(lb_block["OriginalSubnetIds"])
            logger.info("[ELBV2] Original subnet(s) {}".format(str(original_subnets)))

            target_az_subnets = set(lb_block["TargetAZSubnetIds"])
            logger.info("[ELBV2] Target subnet(s) {}".format(str(target_az_subnets)))

            # List of subnets to be changed to
            subnets = sorted(list(original_subnets.difference(target_az_subnets)))

            logger.warning(
                "[ELBV2] Based on config provided, ALB will change subnets to {}".format(
                    subnets
                )
            )

            set_subnets(
                elbv2_client=elbv2_client,
                load_balancer_names=[lb_block["LoadBalancerName"]],
                subnet_ids=subnets,
                dry_run=dry_run,
            )

            # Add to state
            lb_state["LoadBalancerName"] = lb_block["LoadBalancerName"]
            lb_state["Type"] = lb_block["Type"]
            lb_state["Before"] = {"SubnetIds": list(original_subnets)}
            lb_state["After"] = {"SubnetIds": list(subnets)}

            # Add to state
            lbs_state.append(lb_state)
        else:
            logger.warning(
                "[ELBV2] Skipping ELB | LoadBalancerName: {} | ARN: {} | as subnets are not in targeted AZ for failure".format(
                    lb_block["LoadBalancerName"], lb_arn
                )
            )

    # Add to state
    fail_az_state["AvailabilityZone"] = az
    fail_az_state["DryRun"] = dry_run
    fail_az_state["LoadBalancers"] = lbs_state

    write_state(fail_az_state, state_path)

    return fail_az_state


def recover_az(
    state_path: str = "fail_az.{}.json".format(__package__.split(".", 1)[1]),
    configuration: Configuration = None,
) -> bool:
    """
    This function rolls back the ELBv2(s) that were affected by the fail_az action to its previous state. This function is dependent on the state data generated from fail_az.

    Parameters:
        Optional:
            state_path (str): Path to the state data from fail_az (Default: fail_az.elbv2.json)

    """

    # Validate state_path
    state_path = validate_fail_az_path(
        fail_if_exists=False, path=state_path, service=__package__.split(".", 1)[1]
    )

    fail_az_state = read_state(state_path)

    # Check if data was for dry run
    if fail_az_state["DryRun"]:
        raise FailedActivity("State file was generated from a dry run...")

    elbv2_client = client("elbv2", configuration)

    for elb in fail_az_state["LoadBalancers"]:
        logger.warning(
            "[ELBV2] Based on the state file found, AZ failure rollback will happen for ELB - {}".format(
                elb["LoadBalancerName"]
            )
        )

        if elb["After"]["SubnetIds"] and elb["Type"] == "application":
            logger.info(
                "[ELBV2] ELB - {} - will update its subnets to {}".format(
                    elb["LoadBalancerName"], elb["Before"]["SubnetIds"]
                )
            )

            # Change subnets of ELB to subnets before fail_az
            set_subnets(
                elbv2_client=elbv2_client,
                load_balancer_names=[elb["LoadBalancerName"]],
                subnet_ids=elb["Before"]["SubnetIds"],
                dry_run=False,
            )

    # Remove state file upon completion
    try:
        logger.warning(
            "[ELBV2] Completed rollback, removing file ({}) from disk...".format(
                state_path
            )
        )
        os.remove(state_path)
    except Exception as e:
        logger.error("[ELBV2] Error removing file: %s", str(e), exc_info=1)

    return True


def set_subnets(
    elbv2_client: boto3.client,
    load_balancer_names: List[str],
    subnet_ids: List[str],
    dry_run: bool = False,
) -> None:

    load_balancers = get_load_balancer_arns(elbv2_client, load_balancer_names)

    if load_balancers.get("network", []):
        raise FailedActivity("Cannot set subnets of network load balancers...")

    if not dry_run:
        for lb_arn in load_balancers["application"]:
            elbv2_client.set_subnets(LoadBalancerArn=lb_arn, Subnets=subnet_ids)


def get_load_balancer_arns(
    client: boto3.client, load_balancer_names: List[str]
) -> Dict[str, List[str]]:
    results = {}
    logger.debug("[ELBV2] Finding for load balancer: {}.".format(load_balancer_names))

    try:
        response = client.describe_load_balancers(Names=load_balancer_names)

        for lb in response["LoadBalancers"]:
            if lb["State"]["Code"] != "active":
                raise FailedActivity(
                    "Invalid state for load balancer {}: "
                    "{} is not active".format(
                        lb["LoadBalancerName"], lb["State"]["Code"]
                    )
                )
            results.setdefault(lb["Type"], []).append(lb["LoadBalancerArn"])
            results.setdefault("Names", []).append(lb["LoadBalancerName"])
    except ClientError as e:
        raise FailedActivity(e.response["Error"]["Message"])

    invalid_lbs = [lb for lb in load_balancer_names if lb not in results["Names"]]
    if invalid_lbs:
        raise FailedActivity("Unable to find load balancer(s): {}".format(invalid_lbs))

    if not results:
        raise FailedActivity(
            "Unable to find any load balancer(s): {}".format(load_balancer_names)
        )

    return results


def get_subnets(client: boto3.client, subnet_ids: List[str]) -> List[str]:
    try:
        results = []
        paginator = client.get_paginator("describe_subnets")
        for p in paginator.paginate(SubnetIds=subnet_ids):
            for s in p["Subnets"]:
                results.append(s)

        subnet_ids_response = [r["SubnetId"] for r in results]
    except ClientError as e:
        raise FailedActivity(e.response["Error"]["Message"])

    invalid_subnets = [s for s in subnet_ids if s not in subnet_ids_response]
    if invalid_subnets:
        raise FailedActivity("Invalid subnet id(s): {}".format(invalid_subnets))
    return subnet_ids_response


def get_lbs_by_az(client: boto3.client, az: str) -> List[str]:
    """Get list of load balancers from an AZ

    Args:
        client (boto3.client): elbv2 aws client
        az (str): availability zone

    Returns:
        List[str]: List of LB arns
    """
    results = set()

    paginator = client.get_paginator("describe_load_balancers")
    for p in paginator.paginate():
        for lb in p["LoadBalancers"]:
            for availability_zone in lb["AvailabilityZones"]:
                if availability_zone["ZoneName"] == az:
                    results.add(lb["LoadBalancerArn"])

    return list(results)


def filter_lbs_by_tags(
    client: boto3.client, resource_arns: List[str], tags: List[Dict[str, str]]
) -> List[str]:
    """Filter a list of LBs from a list of tags and LB arns. All tags provided need to exist in the LB

    Args:
        client (boto3.client): elbv2 aws client
        resource_arns (List[str]): LB arns
        tags (List[Dict[str, str]]): LB tags

    Returns:
        List[str]: List of LB arns
    """
    results = set()
    resource_arn_chunks = [
        resource_arns[i : i + 20] for i in range(0, len(resource_arns), 20)
    ]  # Break down into chunks of size 20 (limitation of describe_tags API)
    for resource_arn_chunk in resource_arn_chunks:
        response = client.describe_tags(ResourceArns=resource_arn_chunk)

        for td in response["TagDescriptions"]:
            if all(t in td["Tags"] for t in tags):
                results.add(td["ResourceArn"])

    return list(results)


def get_lb_subnets_by_az(
    client: boto3.client, az: str, load_balancer_arn: str
) -> Dict[str, Any]:
    """Returns dict of LB name, list of original subnets and target subnets that are in the specified AZ

    Args:
        client (boto3.client): elbv2 aws client
        az (str): availability zone
        load_balancer_arn (str): arn of lb. Defaults to None.

    Returns:
        Dict[str, Any]: Dict of LB name, original subnets and target subnets
    """
    results = {}
    params = {}
    params["LoadBalancerArns"] = [load_balancer_arn]
    paginator = client.get_paginator("describe_load_balancers")
    for p in paginator.paginate(**params):
        for lb in p["LoadBalancers"]:
            original_subnet_ids = []
            target_az_subnet_ids = []
            for availability_zone in lb["AvailabilityZones"]:
                original_subnet_ids.append(availability_zone["SubnetId"])
                if availability_zone["ZoneName"] == az:
                    if len(lb["AvailabilityZones"]) < 3:
                        raise FailedActivity(
                            "[ELBV2] LB requires at least 2 AZs to operate. Please ensure your LB has 3 AZs to support the removal of 1 AZ."
                        )
                    target_az_subnet_ids.append(availability_zone["SubnetId"])

            results["LoadBalancerName"] = lb["LoadBalancerName"]
            results["Type"] = lb["Type"]
            results["OriginalSubnetIds"] = original_subnet_ids
            results["TargetAZSubnetIds"] = target_az_subnet_ids

    return results
