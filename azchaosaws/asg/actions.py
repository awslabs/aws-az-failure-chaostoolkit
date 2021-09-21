# -*- coding: utf-8 -*-
import os
from typing import Dict, List, Any

import boto3
import json

from azchaosaws import client
from chaoslib.exceptions import FailedActivity
from chaoslib.types import Configuration
from azchaosaws.utils import args_fmt
from azchaosaws.helpers import validate_fail_az_path

from logzero import logger

__all__ = ["fail_az", "recover_az"]


@args_fmt
def fail_az(az: str = None, dry_run: bool = None, tags: List[Dict[str, str]] = [{"Key": "AZ_FAILURE", "Value": "True"}], state_path: str = "fail_az.{}.json".format(__package__.split(".", 1)[1]),
            configuration: Configuration = None) -> Dict[str, Any]:
    """
    This function simulates the lost of an AZ in an AWS Region for AutoScalingGroups by removing the subnets of the AZ in the ASGs. 
    It also suspends the process of AZ Rebalancing of the ASG. I.e. ASG will try to spin up the shortfalll instances in another az if subnets removed and terminate
    the instances in the invalid AZ. If ASG is only configured for one AZ, the min, max and desired will be set to 0 instead.

    Conflicts with:
        eks.fail_az - Ensure that ASGs that belong to EKS clusters are not be tagged as they will be captured by this action, which will cause eks.fail_az to not identify the instances

    Parameters:
        Required:
            az: an availability zone
            dry_run: the boolean flag to simulate a dry run or not. Setting to True will only run read only operations and not make changes to resources. (Accepted values: true | false)

        Optional:
            tags: a list of key/value pair to identify asg(s) by (Default: {'Key': 'AZ_FAILURE', 'Value': 'True'} )

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
        raise FailedActivity('To simulate AZ failure, you must specify'
                             'a dry_run boolean parameter to indicate if you want to run read-only operations (Accepted values: true | false)')

    if not az:
        raise FailedActivity('To simulate AZ failure, you must specify '
                             'an Availability Zone')

    # Validate state_path
    state_path = validate_fail_az_path(
        fail_if_exists=True, path=state_path, service=__package__.split(".", 1)[1])

    logger.warning("[ASG] Executing fail_az action with dry_run ({}) ({}).".format(
        "enabled" if dry_run else "disabled", dry_run))

    asg_client = client('autoscaling', configuration)
    fail_az_state = {"AutoScalingGroups": []}
    asgs_state = []

    tagged_asgs = set()
    tagged_asgs_response = get_asg_by_tags(tags, asg_client)
    for a in tagged_asgs_response["AutoScalingGroups"]:
        tagged_asgs.add(a["AutoScalingGroupName"])
    logger.info('[ASG] Tagged ASGs ({})'.format(str(tagged_asgs)))

    target_az_asgs = set(get_asgs_by_az(az, asg_client))
    logger.info('[ASG] Target AZ ASGs ({})'.format(str(target_az_asgs)))

    # List of ASGs to be updated
    asgs = list(tagged_asgs.intersection(target_az_asgs))
    logger.info('[ASG] Target ASGs ({})'.format(str(asgs)))

    if not asgs:
        raise FailedActivity(
            "No ASGs with the provided tags and the target AZ found...")

    logger.warning(
        '[ASG] Based on config provided, AZ failure simulation will happen in ({}) for ASG(s) ({})'.format(az, asgs))

    # For every ASG change subnets to non target AZ subnets
    for asg in asgs:
        results = {}

        if asg_in_single_az(client=asg_client, asg=asg):
            # If ASG is for single AZ
            results = modify_capacity(client=asg_client, asg=asg, dry_run=dry_run)
        else:
            # If ASG is across multiple AZs
            results = remove_az_subnets(client=asg_client, az=az, asg=asg, dry_run=dry_run,
                                        configuration=configuration)
        # Add to state
        asgs_state.append(results)

    # Add to state
    fail_az_state["AvailabilityZone"] = az
    fail_az_state["DryRun"] = dry_run
    fail_az_state["AutoScalingGroups"] = asgs_state

    json.dump(fail_az_state, open(state_path, 'w'))

    return fail_az_state


def recover_az(state_path: str = "fail_az.{}.json".format(__package__.split(".", 1)[1]),
               configuration: Configuration = None) -> bool:
    """
    This function rolls back the ASGs that were affected by the fail_az action to its previous state. This function is dependent on the persisted data from fail_az

    Parameters:
        Optional:
            state_path: path to the persisted data from fail_az (Default: fail_az.asg.json)

    """

    # Validate state_path
    state_path = validate_fail_az_path(
        fail_if_exists=False, path=state_path, service=__package__.split(".", 1)[1])

    fail_az_state = json.load(open(state_path))

    # Check if data was for dry run
    if fail_az_state["DryRun"]:
        raise FailedActivity(
            'State file was generated from a dry run...')

    asg_client = client('autoscaling', configuration)

    for asg in fail_az_state["AutoScalingGroups"]:
        logger.warning("[ASG] ({}) Based on the state file found, AZ failure rollback will happen for ASG ({})".format(
            asg["AutoScalingGroupName"], asg["AutoScalingGroupName"]))

        if all(k in asg["Before"] for k in ("AZRebalance", "SubnetIds")):
            if asg["Before"]["AZRebalance"]:
                logger.warning("[ASG] ({}) AZRebalance process will be resumed.".format(
                    asg["AutoScalingGroupName"]))
            logger.warning("[ASG] ({}) Subnets will be updated back to {}".format(
                asg["AutoScalingGroupName"], asg["Before"]["SubnetIds"]))

            # Resume AZRebalance process if not suspended before fail_az
            if asg["Before"]["AZRebalance"]:
                resume_processes(asg_names=[asg["AutoScalingGroupName"]],
                                 process_names=["AZRebalance"], configuration=configuration)

            # Change subnets of ASG to the subnets before fail_az
            change_subnets(subnets=asg["Before"]["SubnetIds"],
                           asg_names=[asg["AutoScalingGroupName"]],
                           configuration=configuration)

        if all(k in asg["Before"] for k in ("MinSize", "MaxSize", "DesiredCapacity")):
            logger.warning("[ASG] ({}) MinSize, MaxSize and DesiredCapacity will be updated back to {}, {} and {}".format(
                asg["AutoScalingGroupName"], asg["Before"]["MinSize"], asg["Before"]["MaxSize"], asg["Before"]["DesiredCapacity"]))

            modify_capacity(client=asg_client, asg=asg["AutoScalingGroupName"], min_size=asg["Before"]["MinSize"],
                            max_size=asg["Before"]["MaxSize"], desired_cap=asg["Before"]["DesiredCapacity"])

    # Remove state file upon completion
    try:
        logger.warning(
            "[ASG] Completed rollback, removing file ({}) from disk...".format(state_path))
        os.remove(state_path)
    except Exception as e:
        logger.error("[ASG] Error removing file: %s", str(e), exc_info=1)

    return True


def suspend_processes(asg_names: List[str] = None,
                      tags: List[Dict[str, str]] = None,
                      process_names: List[str] = None,
                      configuration: Configuration = None) -> Dict[str, Any]:
    validate_asgs(asg_names, tags)

    if process_names:
        validate_processes(process_names)

    asg_client = client('autoscaling', configuration)

    if asg_names:
        asgs = get_asg_by_name(asg_names, asg_client)
    else:
        asgs = get_asg_by_tags(tags, asg_client)

    for a in asgs['AutoScalingGroups']:
        params = dict(AutoScalingGroupName=a['AutoScalingGroupName'])
        if process_names:
            params['ScalingProcesses'] = process_names

        logger.debug('[ASG] Suspending process(es) on {}'.format(
            a["AutoScalingGroupName"]))
        asg_client.suspend_processes(**params)

    return get_asg_by_name(
        [a['AutoScalingGroupName'] for a in asgs['AutoScalingGroups']], asg_client)


def resume_processes(asg_names: List[str] = None,
                     tags: List[Dict[str, str]] = None,
                     process_names: List[str] = None,
                     configuration: Configuration = None) -> Dict[str, Any]:
    validate_asgs(asg_names, tags)

    if process_names:
        validate_processes(process_names)

    asg_client = client('autoscaling', configuration)

    if asg_names:
        asgs = get_asg_by_name(asg_names, asg_client)
    else:
        asgs = get_asg_by_tags(tags, asg_client)

    for a in asgs['AutoScalingGroups']:
        params = dict(AutoScalingGroupName=a['AutoScalingGroupName'])
        if process_names:
            params['ScalingProcesses'] = process_names

        logger.debug('[ASG] Resuming process(es) {} on {}'.format(
            process_names, a['AutoScalingGroupName']))
        asg_client.resume_processes(**params)

    return get_asg_by_name(
        [a['AutoScalingGroupName'] for a in asgs['AutoScalingGroups']], asg_client)


def change_subnets(subnets: List[str],
                   asg_names: List[str] = None,
                   tags: List[dict] = None,
                   configuration: Configuration = None):
    validate_asgs(asg_names, tags)
    asg_client = client('autoscaling', configuration)

    if asg_names:
        asgs = get_asg_by_name(asg_names, asg_client)
    else:
        asgs = get_asg_by_tags(tags, asg_client)

    for a in asgs['AutoScalingGroups']:
        asg_client.update_auto_scaling_group(
            AutoScalingGroupName=a['AutoScalingGroupName'],
            VPCZoneIdentifier=','.join(subnets))


def validate_asgs(asg_names: List[str] = None,
                  tags: List[Dict[str, str]] = None):
    if not any([asg_names, tags]):
        raise FailedActivity(
            'one of the following arguments are required: asg_names or tags')

    if all([asg_names, tags]):
        raise FailedActivity(
            'only one of the following arguments are allowed: asg_names/tags')


def get_asg_by_name(asg_names: List[str],
                    client: boto3.client) -> Dict[str, Any]:
    logger.debug('[ASG] Searching for ASG(s): {}.'.format(asg_names))

    asgs = client.describe_auto_scaling_groups(AutoScalingGroupNames=asg_names)

    if not asgs.get('AutoScalingGroups', []):
        logger.warning('[ASG] Unable to locate ASG(s): {}'.format(asg_names))

    found_asgs = [a['AutoScalingGroupName'] for a in asgs['AutoScalingGroups']]
    invalid_asgs = [a for a in asg_names if a not in found_asgs]
    if invalid_asgs:
        raise FailedActivity('[ASG] No ASG(s) found with name(s): {}'.format(
            invalid_asgs))
    return asgs


def get_asg_by_tags(tags: List[Dict[str, str]],
                    client: boto3.client) -> Dict[str, Any]:
    params = []

    for t in tags:
        params.extend([
            {'Name': 'key', 'Values': [t['Key']]},
            {'Name': 'value', 'Values': [t['Value']]}])

    paginator = client.get_paginator('describe_tags')
    results = set()
    for p in paginator.paginate(Filters=params):
        for a in p['Tags']:
            if a['ResourceType'] != 'auto-scaling-group':
                continue
            results.add(a['ResourceId'])

    if not results:
        raise FailedActivity(
            'No ASG(s) found with matching tag(s): {}.'.format(tags))
    return get_asg_by_name(list(results), client)


def validate_processes(process_names: List[str]):
    valid_processes = ['Launch', 'Terminate', 'HealthCheck', 'AZRebalance',
                       'AlarmNotification', 'ScheduledActions',
                       'AddToLoadBalancer', 'ReplaceUnhealthy']

    invalid_processes = [p for p in process_names if p not in valid_processes]
    if invalid_processes:
        raise FailedActivity('invalid process(es): {} not in {}.'.format(
            invalid_processes, valid_processes))


def get_asgs_by_az(az: str,
                   client: boto3.client) -> List[str]:
    logger.debug('[ASG] Searching for ASG(s) in AZ ({}).'.format(az))

    paginator = client.get_paginator('describe_auto_scaling_groups')
    results = set()
    for p in paginator.paginate():
        for a in p['AutoScalingGroups']:
            if az in a['AvailabilityZones']:
                results.add(a['AutoScalingGroupName'])

    if not results:
        logger.info('[ASG] No ASG(s) found in AZ ({}).'.format(az))
    return list(results)


def describe_subnets(client: boto3.client, subnet_ids: List[str], filters: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Describes your subnets.
    """

    params = {}
    if filters:
        params["Filters"] = filters
    if subnet_ids:
        params["SubnetIds"] = subnet_ids

    results = []
    paginator = client.get_paginator('describe_subnets')
    for p in paginator.paginate(**params):
        for s in p['Subnets']:
            results.append(s)

    if not results:
        logger.info("[ASG] No subnets found.")

    return results


def remove_az_subnets(client: boto3.client, az: str, asg: str, dry_run: bool = False,
                      configuration: Configuration = None) -> Dict[str, any]:
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
    asg_response = get_asg_by_name(asg_names=[asg], client=client)

    # Suspend AZRebalance process
    suspended_processes = asg_response["AutoScalingGroups"][0]["SuspendedProcesses"]
    logger.info(
        '[ASG] Suspending AZRebalance processes for ({})'.format(asg))

    if not dry_run:
        # WOP
        suspend_processes(asg_names=[asg],
                          process_names=["AZRebalance"], configuration=configuration)

    existing_subnets = asg_response["AutoScalingGroups"][0]["VPCZoneIdentifier"].split(
        ",")  # List of existing subnets
    # Filter subnets that are NOT from the AZ of each ASG (from set a) -> list of subnets for every ASG (set B)
    existing_subnets_full = describe_subnets(client=client(
        'ec2', configuration), subnet_ids=existing_subnets)
    non_az_subnets = [s["SubnetId"]
                      for s in existing_subnets_full if s["AvailabilityZone"] != az]

    logger.info(
        '[ASG] ASG ({}) will update its subnets from ({}) to ({})'.format(asg, existing_subnets, non_az_subnets))

    if not dry_run:
        # WOP
        # Change subnets of ASG to only non failed AZ subnets
        client.update_auto_scaling_group(
            AutoScalingGroupName=asg,
            VPCZoneIdentifier=','.join(non_az_subnets))

    # Return list of subnets, ASG and AZRebalance process state
    results["AutoScalingGroupName"] = asg
    results["Before"] = {
        "SubnetIds": existing_subnets,
        "AZRebalance": not any(sp.get('ProcessName', None) == 'AZRebalance' for sp in suspended_processes)
    }
    results["After"] = {
        "SubnetIds": non_az_subnets,
        "AZRebalance": False
    }

    return results


def asg_in_single_az(client: boto3.client, asg: str) -> bool:
    """Checks if ASG is only for single AZ. Returns True if it is, and False if it's for multiple AZs.
    """

    response = client.describe_auto_scaling_groups(
        AutoScalingGroupNames=[asg]
    )

    return len(response["AutoScalingGroups"][0]["AvailabilityZones"]) == 1


def modify_capacity(client: boto3.client, asg: str, dry_run: bool = False, min_size=0, max_size=0, desired_cap=0,
                    configuration: Configuration = None) -> Dict[str, any]:
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
    asg_response = get_asg_by_name(asg_names=[asg], client=client)

    orig_min_size = asg_response["AutoScalingGroups"][0]["MinSize"]
    orig_max_size = asg_response["AutoScalingGroups"][0]["MaxSize"]
    orig_desired_cap = asg_response["AutoScalingGroups"][0]["DesiredCapacity"]

    logger.info(
        '[ASG] Setting min, max and desired capacity for ({}) to {}, {} and {}'.format(asg, min_size, max_size, desired_cap))

    if not dry_run:
        # WOP
        # Update ASG min, max and desired to 0
        client.update_auto_scaling_group(
            AutoScalingGroupName=asg,
            MinSize=min_size,
            MaxSize=max_size,
            DesiredCapacity=desired_cap)

    # Return list of ASG, min, max and desired cap
    results["AutoScalingGroupName"] = asg
    results["Before"] = {
        "MinSize": orig_min_size,
        "MaxSize": orig_max_size,
        "DesiredCapacity": orig_desired_cap
    }
    results["After"] = {
        "MinSize": min_size,
        "MaxSize": max_size,
        "DesiredCapacity": desired_cap
    }

    return results
