# -*- coding: utf-8 -*-
import json
import os
from typing import Any, Dict, List

import boto3
from logzero import logger
from chaoslib.exceptions import FailedActivity
from chaoslib.types import Configuration
from azchaosaws import client
from azchaosaws.utils import args_fmt
from azchaosaws.helpers import validate_fail_az_path

__all__ = ["fail_az", "recover_az"]


@args_fmt
def fail_az(az: str = None, dry_run: bool = None, lb_names: List[str] = None, tags: List[Dict[str, any]] = [{"Key": "AZ_FAILURE", "Value": "True"}], state_path: str = "fail_az.{}.json".format(__package__.split(".", 1)[1]),
            configuration: Configuration = None) -> Dict[str, Any]:
    """
    This function simulates the lost of an AZ in an AWS Region for classic LBs by detaching the LB from subnets of the 'failed' az. If LB is in a default VPC, 
    disables the 'failed' az for the LB.

    Notes:
        Detaching lb from subnets:
            After a subnet is removed, all EC2 instances registered with the load balancer in the removed subnet go into the OutOfService state. 
            Then, the load balancer balances the traffic among the remaining routable subnets.

        Disabling az for lb:
            There must be at least one Availability Zone registered with a load balancer at all times. After an Availability Zone is removed, 
            all instances registered with the load balancer that are in the removed Availability Zone go into the OutOfService state. Then, the 
            load balancer attempts to equally balance the traffic among its remaining Availability Zones.

    Parameters:
        Required:
            az: an availability zone

        Optional:
            tags: a list of key/value pair to identify asg(s) by (Default: [{'Key': 'AZ_FAILURE', 'Value': 'True'}] )

    `tags` are expected as a list of dictionary objects:
    [
        {'Key': 'TagKey1', 'Value': 'TagValue1'},
        {'Key': 'TagKey2', 'Value': 'TagValue2'},
        ...
    ]

    Output Structure:
    {
        "AvailabilityZone": str,
        "DryRun": bool,
        "LoadBalancers": [
            {
                "LoadBalancerName": str,
                "Type: "Classic",
                "Before": {
                    "SubnetIds": List[str]
                    "AvailabilityZones": List[str]
                },
                "After": {
                    "SubnetIds": List[str]
                    "AvailabilityZones": List[str]
                }
            }
        ]
    }
    """

    if dry_run is None:
        raise FailedActivity('To simulate AZ failure, you must specify'
                             'a dry_run boolean parameter to indicate if you want to run read-only operations. (Accepted values: true | false)')

    if not az:
        raise FailedActivity('To simulate AZ failure, you must specify '
                             'an Availability Zone')

    # Validate state_path
    state_path = validate_fail_az_path(
        fail_if_exists=True, path=state_path, service=__package__.split(".", 1)[1])

    elb_client = client('elb', configuration)
    ec2_client = client('ec2', configuration)
    fail_az_state = {"AvailabilityZone": az,
                     "DryRun": dry_run, "LoadBalancers": []}
    lbs_state = []

    target_az_lb_names = get_lbs_by_az(elb_client, az)
    logger.info("[ELB] LBs found in target AZ ({})...".format(
        target_az_lb_names))
    if not target_az_lb_names:
        logger.warning(
            "[ELB] No LBs in the target AZ found...")
        raise FailedActivity('[ELB] No LBs in the target AZ found...')

    if lb_names:
        tagged_lb_names = filter_lbs_by_tags(elb_client, lb_names, tags)
    else:
        tagged_lb_names = filter_lbs_by_tags(elb_client, target_az_lb_names, tags)

    logger.info("[ELB] Filtered LBs by tags and az ({})...".format(tagged_lb_names))
    if not tagged_lb_names:
        logger.warning(
            "[ELB] No LBs with the provided tags and the target AZ found...")
        raise FailedActivity(
            '[ELB] No LBs with the provided tags and the target AZ found...')

    logger.warning(
        '[ELB] Based on config provided, AZ failure simulation will happen in {} for LB(s) {}'.format(az, tagged_lb_names))

    default_vpc_lbs = filter_lbs_in_default_vpc(
        elb_client=elb_client, ec2_client=ec2_client, lb_names=tagged_lb_names)
    non_default_vpc_lbs = list(
        set(tagged_lb_names).difference(set(default_vpc_lbs)))
    logger.info(
        '[ELB] LBs in default VPC ({})'.format(default_vpc_lbs))
    logger.info(
        '[ELB] LBs in non-default VPC ({})'.format(non_default_vpc_lbs))

    # Detach target AZ subnets of LBs that are not in default VPC
    if non_default_vpc_lbs:
        for lb in non_default_vpc_lbs:
            lb_state = {}
            original_subnets, remaining_subnets = [], []
            lb_block = get_lb_subnets_by_az(elb_client, ec2_client, az, lb)

            if lb_block["TargetAZSubnetIds"]:
                original_subnets = lb_block["OriginalSubnetIds"]
                logger.info('[ELB] Original subnet(s) {}'.format(
                    original_subnets))

                target_az_subnets = lb_block["TargetAZSubnetIds"]
                logger.info('[ELB] Target subnet(s) {}'.format(
                    target_az_subnets))

                logger.warning(
                    '[ELB] Detaching subnets ({}) from LB ({})...'.format(target_az_subnets, lb))

                if not dry_run:
                    # [WOP]
                    remaining_subnets = elb_client.detach_load_balancer_from_subnets(
                        LoadBalancerName=lb,
                        Subnets=target_az_subnets
                    )["Subnets"]
                else:
                    remaining_subnets = list(
                        set(original_subnets).difference(set(target_az_subnets)))

                # Add to state
                lb_state["LoadBalancerName"] = lb_block["LoadBalancerName"]
                lb_state["Before"] = {"SubnetIds": original_subnets}
                lb_state["After"] = {"SubnetIds": remaining_subnets}

                # Add to state
                lbs_state.append(lb_state)
            else:
                logger.warning("[ELB] Skipping classic ELB | LoadBalancerName: ({}) | as subnets are not in targeted AZ for failure".format(
                    lb_block["LoadBalancerName"]))

    # Disable target AZ of LBs that are in default VPC
    if default_vpc_lbs:
        lbs = elb_client.describe_load_balancers(
            LoadBalancerNames=default_vpc_lbs
        )["LoadBalancerDescriptions"]
        for lb in lbs:
            lb_state = {}
            original_azs, remaining_azs = lb["AvailabilityZones"], []

            logger.warning(
                '[ELB] Disabling AZ ({}) for ({})...'.format(az, lb["LoadBalancerName"]))
                
            if not dry_run:
                # [WOP]
                remaining_azs = elb_client.disable_availability_zones_for_load_balancer(
                    LoadBalancerName=lb["LoadBalancerName"],
                    AvailabilityZones=[az]
                )["AvailabilityZones"]
            else:
                remaining_azs = list(
                    set(lb["AvailabilityZones"]).difference(set([az])))

            # Add to state
            lb_state["LoadBalancerName"] = lb["LoadBalancerName"]
            lb_state["Before"] = {"AvailabilityZones": original_azs}
            lb_state["After"] = {"AvailabilityZones": remaining_azs}

            # Add to state
            lbs_state.append(lb_state)

    # Add to state
    fail_az_state["LoadBalancers"] = lbs_state

    json.dump(fail_az_state, open(state_path, 'w'))

    return fail_az_state


def recover_az(state_path: str = "fail_az.{}.json".format(__package__.split(".", 1)[1]),
               configuration: Configuration = None) -> bool:
    """
    This function rolls back the ELBs that were affected by the fail_az action to its previous state. This function is dependent on the persisted data from fail_az

    Parameters:
        Optional:
            state_path: path to the persisted data from fail_az (Default: fail_az.elb.json)

    """

    # Validate state_path
    state_path = validate_fail_az_path(
        fail_if_exists=False, path=state_path, service=__package__.split(".", 1)[1])

    fail_az_state = json.load(open(state_path))

    # Check if data was for dry run
    if fail_az_state["DryRun"]:
        raise FailedActivity(
            'State file was generated from a dry run...')

    elb_client = client('elb', configuration)

    for elb in fail_az_state["LoadBalancers"]:
        logger.warning(
            '[ELB] Based on the state file found, AZ failure rollback will happen for classic ELB ({})'.format(elb["LoadBalancerName"]))

        # Check if SubnetIds are present. If present, rollback to original subnet ids
        if elb["After"].get("SubnetIds", []):
            logger.info(
                '[ELB] ELB ({}) will be attached to subnets ({})'.format(elb["LoadBalancerName"], elb["Before"]["SubnetIds"]))

            elb_client.attach_load_balancer_to_subnets(
                LoadBalancerName=elb["LoadBalancerName"],
                Subnets=elb["Before"]["SubnetIds"]
            )

        # Check if AvailabilityZones are present. If present, rollback to original AZs
        if elb["After"].get("AvailabilityZones", []):
            logger.info(
                '[ELB] AZ(s) ({}) will be enabled for ELB ({})'.format(elb["Before"]["AvailabilityZones"], elb["LoadBalancerName"]))
            
            elb_client.enable_availability_zones_for_load_balancer(
                LoadBalancerName=elb["LoadBalancerName"],
                AvailabilityZones=elb["Before"]["AvailabilityZones"]
            )

    # Remove state file upon completion
    try:
        logger.warning(
            "[ELB] Completed rollback, removing file ({}) from disk...".format(state_path))
        os.remove(state_path)
    except Exception as e:
        logger.error("[ELB] Error removing file: %s", str(e), exc_info=1)

    return True


def get_lbs_by_az(client: boto3.client, az: str) -> List[str]:
    """ Get list of load balancers from an AZ

    Args:
        client (boto3.client): [description]
        az (str): [description]

    Returns:
        List[str]: List of LB names
    """
    results = set()

    paginator = client.get_paginator('describe_load_balancers')
    for p in paginator.paginate():
        for lb in p['LoadBalancerDescriptions']:
            for availability_zone in lb["AvailabilityZones"]:
                if availability_zone == az:
                    results.add(lb["LoadBalancerName"])

    return list(results)


def filter_lbs_by_tags(client: boto3.client, lb_names: List[str], tags: List[Dict[str, str]]) -> List[str]:
    """ Filter a list of LBs from a list of tags and LB names. All tags provided need to exist in the LB

    Args:
        client (boto3.client): [description]
        lb_names (List[str]): [description]
        tags (List[Dict[str, str]]): [description]

    Returns:
        List[str]: List of LB names
    """
    results = set()
    lb_names_chunks = [lb_names[i:i + 20] for i in range(0, len(lb_names), 20)] # Break down into chunks of size 20 (limitation of describe_tags API)
    for lb_names_chunk in lb_names_chunks:
        response = client.describe_tags(
            LoadBalancerNames=lb_names_chunk
        )

        for td in response["TagDescriptions"]:
            if all(t in td["Tags"] for t in tags):
                results.add(td["LoadBalancerName"])

    return list(results)


def get_lb_subnets_by_az(client: boto3.client, ec2_client: boto3.client, az: str, lb_name: str) -> Dict[str, Any]:
    """ Returns dict of LB name, list of original subnets and target subnets that are in the specified AZ

    Args:
        client (boto3.client): [description]
        az (str): [description]
        lb_name (str): [description].

    Returns:
        Dict[str, any]: Dict of LB name, original subnets and target subnets

    return structure:
    {
        'LoadBalancerName': str,
        'OriginalSubnetIds': List[str],
        'TargetAZSubnetIds': List[str]
    }
    """
    results = {}
    lb_response = client.describe_load_balancers(
        LoadBalancerNames=[
            lb_name
        ]
    )["LoadBalancerDescriptions"]

    if not lb_response:
        raise FailedActivity('[ELB] LB ({}) not found...'.format(
            lb_name))

    lb = lb_response[0]
    original_subnet_ids, target_az_subnet_ids = [], []
    original_subnet_ids = lb["Subnets"]

    target_az_subnets = ec2_client.describe_subnets(
        Filters=[
            {
                'Name': 'availability-zone',
                'Values': [
                    az,
                ]
            },
        ],
        SubnetIds=lb["Subnets"]
    )["Subnets"]

    if target_az_subnets:
        target_az_subnet_ids = [s["SubnetId"] for s in target_az_subnets]

    results["LoadBalancerName"] = lb["LoadBalancerName"]
    results["OriginalSubnetIds"] = original_subnet_ids
    results["TargetAZSubnetIds"] = target_az_subnet_ids

    return results


def filter_lbs_in_default_vpc(elb_client: boto3.client, ec2_client: boto3.client, lb_names: List[str]) -> List[str]:
    """ Filter a list of LBs from the default VPC

    Args:
        client (boto3.client): [description]
        lb_names (List[str]): [description]
        tags (List[Dict[str, str]]): [description]

    Returns:
        List[str]: List of LB names or empty list if no default VPC in region or empty list if no lbs with default vpc found
    """

    vpcs = ec2_client.describe_vpcs(Filters=[
        {
            'Name': 'is-default',
            'Values': [
                "true",
            ]
        },
    ])["Vpcs"]

    logger.debug("[ELB] VPCS: {}".format(vpcs))

    if not vpcs:
        logger.info("[ELB] Did not find any default VPC...")
        return []

    default_vpc_id = vpcs[0]["VpcId"]
    logger.info("[ELB] Found default VPC ({})".format(default_vpc_id))
    lbs = elb_client.describe_load_balancers(
        LoadBalancerNames=lb_names
    )["LoadBalancerDescriptions"]
    lbs_in_default_vpc = [lb["LoadBalancerName"]
                          for lb in lbs if lb["VPCId"] == default_vpc_id]

    return lbs_in_default_vpc