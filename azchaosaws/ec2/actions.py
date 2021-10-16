# -*- coding: utf-8 -*-
import os
import time
from collections import defaultdict
from copy import deepcopy
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
    failure_type: str = "network",
    filters: List[Dict[str, Any]] = None,
    state_path: str = "fail_az.{}.json".format(__package__.split(".", 1)[1]),
    configuration: Configuration = None,
) -> Dict[str, Any]:
    """
    This function simulates the lost of an AZ in an AWS Region for EC2.
    For network failure type, it uses a blackhole network ACL with deny all traffic. For instance failure type, it stops normal
    instances with force; stops persistent spot instances; cancels spot requests and terminate one-time spot instances.
    Ensure your subnets are tagged if failure_type = "network" and ensure your instances are tagged if failure_type = "instance".
    Instances that are not in pending or running state will still be captured and stopped.

    Parameters:
        Required:
            dry_run (bool): The boolean flag to simulate a dry run or not. Setting to True will only run read-only operations and not make changes to resources. (Accepted values: True | False)

        At least one of:
            az (str): An availability zone
            filters (List[Dict[str, Any]]): A list of key/value pair to identify subnets by. To provide availability-zone filter if az not specified.

        Optional:
            failure_type (str): The failure type to simulate. (Accepted values: "network" | "instance") (Default: "network")
            state_path (str): Path to generate the state data (Default: fail_az.ec2.json). This file is used for recover_az (rollback).

    Output Structure:
        {
            "AvailabilityZone": str,
            "DryRun": bool,
            "Subnets":
                    [
                        {
                            "SubnetId": str,
                            "VpcId": str
                            "Before": {
                                "NetworkAclId": str,
                                "NetworkAclAssociationId": str
                            },
                            "After": {
                                "NetworkAclId": str,
                                "NetworkAclAssociationId": str
                            }
                        },
                        ....
                    ],
            "Instances":
                    [
                        {
                            "InstanceId": str,
                            "Before": {
                                "State": 'pending'|'running'
                            }
                            "After": {
                                "State": 'stopping'|'stopped'
                            }
                        },
                        ....
                    ]
        }
    """

    if dry_run is None:
        raise FailedActivity(
            "To simulate AZ failure, you must specify"
            "a dry_run boolean parameter to indicate if you want to run read-only operations. (Accepted values: true | false)"
        )

    if not az and not filters:
        raise FailedActivity(
            "To simulate AZ failure, you must specify "
            "an Availability Zone, or provide a "
            "set of filters"
        )

    if not az and not any(
        "availability-zone" in f.get("Name", None) and f.get("Values", None) is not None
        for f in filters
    ):
        raise FailedActivity(
            "To simulate AZ failure, you must specify "
            "an Availability Zone, or provide a "
            "set of filters with an AZ defined"
        )

    # Validate state_path
    state_path = validate_fail_az_path(
        fail_if_exists=True, path=state_path, service=__package__.split(".", 1)[1]
    )

    logger.warning(
        "[EC2] Executing fail_az action with dry_run ({}) ({}).".format(
            "enabled" if dry_run else "disabled", dry_run
        )
    )

    filters = deepcopy(filters) if filters else []
    default_tag_filter = {"Name": "tag:AZ_FAILURE", "Values": ["True"]}

    if filters:
        if not any(
            "tag:" in f.get("Name", None) and f.get("Values", None) is not None
            for f in filters
        ):
            filters.append(default_tag_filter)

        if (
            not any(
                "availability-zone" in f.get("Name", None)
                and f.get("Values", None) is not None
                for f in filters
            )
            and az
        ):
            filters.append({"Name": "availability-zone", "Values": [az]})
    else:
        if az:
            filters.append({"Name": "availability-zone", "Values": [az]})
        filters.append(default_tag_filter)

    ec2_client = client("ec2", configuration)
    fail_az_state = {"Subnets": [], "Instances": []}
    subnets_state, instances_state = [], []

    if failure_type == "network":
        subnets = describe_subnets(ec2_client, filters)
        if not subnets:
            raise FailedActivity(
                "No subnets found! Ensure that the subnets that are in the AZ you provided are tagged with the filter you provided or with the default value."
            )
        subnet_ids = [
            subnet["SubnetId"] for subnet in subnets
        ]  # List of subnet ids to blackhole

        logger.warning(
            "[EC2] Based on config provided, AZ failure simulation will happen in ({}) for these subnets ({}) count({})".format(
                subnets[0]["AvailabilityZone"], subnet_ids, len(subnet_ids)
            )
        )

        vpc_ids = list(set([subnet["VpcId"] for subnet in subnets]))

        # Create and associate blackhole nacls for every subnet in the vpcs
        network_failure_response = network_failure(
            client=ec2_client, vpc_ids=vpc_ids, subnet_ids=subnet_ids, dry_run=dry_run
        )

        # Add to state
        subnets_state = network_failure_response

    elif failure_type == "instance":
        # If instance-state-name not exists in provided filter, add it for only pending | running instances (except stopping, stopped, terminated and shutting-down)
        instance_state_names = ["pending", "running"]
        if filters:
            if not any(
                "instance-state-name" in f.get("Name", None)
                and f.get("Values", None) is not None
                for f in filters
            ):
                filters.append(
                    {"Name": "instance-state-name", "Values": instance_state_names}
                )

        # Stop normal/spot intances
        # If Force is to be set to True, it forces the instances to stop. The instances do not have an opportunity to flush file system caches or file system metadata.
        # If you use this option, you must perform file system check and repair procedures. This option is not recommended for Windows instances.
        instance_failure_response = instance_failure(
            client=ec2_client, az=az, dry_run=dry_run, filters=filters, force=True
        )

        # Add to state
        instances_state = instance_failure_response

    # Add to state
    fail_az_state["AvailabilityZone"] = az
    fail_az_state["DryRun"] = dry_run
    fail_az_state["Subnets"] = subnets_state
    fail_az_state["Instances"] = instances_state

    write_state(fail_az_state, state_path)

    return fail_az_state


def recover_az(
    state_path: str = "fail_az.{}.json".format(__package__.split(".", 1)[1]),
    configuration: Configuration = None,
) -> bool:
    """
    This function rolls back the subnet(s) and/or EC2 instances that were affected by the fail_az action to its previous state.
    This function is dependent on the state data generated from fail_az. Note that instances that are in terminated state will not
    be 'rolled' back.

    Parameters:
        Optional:
            state_path (str): Path to the state data from fail_az (Default: fail_az.ec2.json)

    """

    # Validate state_path
    state_path = validate_fail_az_path(
        fail_if_exists=False, path=state_path, service=__package__.split(".", 1)[1]
    )

    fail_az_state = read_state(state_path)

    # Check if data was for dry run
    if fail_az_state["DryRun"]:
        raise FailedActivity("State file was generated from a dry run...")

    ec2_client = client("ec2", configuration)

    # Filter target subnets and instances if they have an after state with no empty values
    target_subnets = fail_az_state["Subnets"]
    target_instances = [
        instance
        for instance in fail_az_state["Instances"]
        if (
            instance["Before"]["State"] == "pending"
            or instance["Before"]["State"] == "running"
        )
        and instance["After"]["State"] == "stopping"
    ]

    # 1. Rollback subnets ACLs
    if target_subnets:
        logger.warning(
            "[EC2] Based on the state file found, AZ failure rollback will happen for subnets ({})".format(
                [s["SubnetId"] for s in target_subnets]
            )
        )
        for subnet in target_subnets:
            logger.warning(
                "[EC2] ({}) Network ACL will be rolled back to ({})".format(
                    subnet["SubnetId"], subnet["Before"]["NetworkAclId"]
                )
            )

            # Replace network ACL association with original network ACL
            replace_network_acl_association(
                ec2_client,
                subnet["Before"]["NetworkAclId"],
                subnet["After"]["NetworkAclAssociationId"],
            )

        # Delete blackhole ACLs
        blackhole_acl_ids = list(
            set([s["After"]["NetworkAclId"] for s in target_subnets])
        )
        logger.warning(
            "[EC2] ({}) Network ACLs will be deleted ({})".format(
                subnet["SubnetId"], blackhole_acl_ids
            )
        )
        for blackhole_acl_id in blackhole_acl_ids:
            delete_network_acl(ec2_client, blackhole_acl_id)
    else:
        logger.info("[EC2] No subnets to rollback...")

    # 2. Rollback instances
    if target_instances:
        target_instances_ids = [instance["InstanceId"] for instance in target_instances]

        # Check if instances are stopped. Otherwise, if they are in stopping state -> fail activity. If terminated/shutting-down/pending/running, ignore them.
        stopped_instances_ids = target_instances_ids[:]
        stopping_instances_ids, ignore_instances_ids = [], []
        for tid in target_instances_ids:
            if not instance_state(
                client=ec2_client, state="stopped", instance_ids=[tid]
            ):
                stopped_instances_ids.remove(tid)
                if instance_state(
                    client=ec2_client, state="stopping", instance_ids=[tid]
                ):
                    stopping_instances_ids.append(tid)
                else:
                    ignore_instances_ids.append(tid)

        if stopping_instances_ids:
            raise FailedActivity(
                "Error rolling back instances as instance state is 'stopping'. Please check ({}) and try again when they are 'stopped'.".format(
                    stopping_instances_ids
                )
            )

        logger.warning(
            "[EC2] Based on the state file found and instance state, AZ failure rollback will happen for instance(s) ({})".format(
                stopped_instances_ids
            )
        )

        logger.warning(
            "[EC2] Skipping instance(s) ({}) as they are either in terminated|shutting-down|pending|running state.".format(
                ignore_instances_ids
            )
        )

        if not stopped_instances_ids:
            raise FailedActivity(
                "Error rolling back instances as instance state is not 'stopped'. Please check ({})".format(
                    target_instances_ids
                )
            )

        start_instances(client=ec2_client, instance_ids=stopped_instances_ids)
    else:
        logger.info("[EC2] No instances to rollback...")

    # Remove state file upon completion
    try:
        logger.warning(
            "[EC2] Completed rollback, removing file ({}) from disk...".format(
                state_path
            )
        )
        os.remove(state_path)
    except Exception as e:
        logger.error("[EC2] Error removing file: %s", str(e), exc_info=1)

    return True


def stop_instances(
    client: boto3.client,
    instance_ids: List[str] = None,
    az: str = None,
    dry_run: bool = False,
    filters: List[Dict[str, Any]] = None,
    force: bool = False,
) -> List[Dict[str, Any]]:
    if not az and not instance_ids and not filters:
        raise FailedActivity(
            "To stop EC2 instances, you must specify either the instance ids,"
            " an AZ to pick random instances from, or a set of filters."
        )

    if az and not instance_ids and not filters:
        logger.warning(
            "[EC2] Based on configuration provided I am going to "
            "stop all instances in AZ %s!" % az
        )

    if not instance_ids:
        filters = deepcopy(filters) if filters else []

        if az:
            filters.append({"Name": "availability-zone", "Values": [az]})
        instance_lifecycles = list_instances_by_lifecycle(client, filters)

        if not instance_lifecycles:
            logger.warning("[EC2] No instances in availability zone: {}".format(az))
            raise FailedActivity("No instances in availability zone: {}".format(az))
    else:
        instance_lifecycles = get_instance_lifecycle_by_ids(client, instance_ids)

    logger.debug(
        "[EC2] Picked EC2 instances ({}) from AZ ({}) to be stopped".format(
            str(instance_lifecycles), az
        )
    )

    for instance_lifecycle in instance_lifecycles.keys():
        instance_ids = [id for id in instance_lifecycles[instance_lifecycle]]
        logger.warning(
            "[EC2] Based on config provided, AZ failure simulation will happen in ({}) for these ({}) instances ({}) count({})".format(
                az, instance_lifecycle, instance_ids, len(instance_ids)
            )
        )

    return (
        stop_instances_any_lifecycle(
            client=client, instance_lifecycles=instance_lifecycles, force=force
        )
        if not dry_run
        else instance_lifecycles
    )


def start_instances(
    client: boto3.client,
    instance_ids: List[str] = None,
    az: str = None,
    filters: List[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    if not any([instance_ids, az, filters]):
        raise FailedActivity(
            "To start instances, you must specify the "
            "instance-id, an Availability Zone, or provide a "
            "set of filters"
        )

    if az and not any([instance_ids, filters]):
        logger.warning(
            "[EC2] Based on configuration provided I am going to "
            "start all instances in AZ %s!" % az
        )

    if not instance_ids:
        filters = deepcopy(filters) or []

        if az:
            filters.append({"Name": "availability-zone", "Values": [az]})
            logger.debug("[EC2] Looking for instances in AZ: %s" % az)

        # Select instances based on filters
        instance_types = list_instances_by_lifecycle(client, filters)

        if not instance_types:
            raise FailedActivity(
                "[EC2] No instances found matching filters: %s" % str(filters)
            )

        logger.debug(
            "[EC2] Instances in AZ %s selected: %s}." % (az, str(instance_types))
        )
    else:
        instance_types = get_instance_lifecycle_by_ids(client, instance_ids)
    return start_instances_any_lifecycle(client, instance_types)


def list_instances_by_lifecycle(
    client: boto3.client, filters: List[Dict[str, Any]]
) -> Dict[str, Any]:
    logger.debug("[EC2] EC2 instances filter: ({})".format(str(filters)))
    response = client.describe_instances(Filters=filters)
    logger.debug("[EC2] Filtered instances: ({})".format(str(response)))

    instances_lifecycle = defaultdict(List)
    for reservation in response["Reservations"]:
        for instance in reservation["Instances"]:
            lifecycle = instance.get("InstanceLifecycle", "normal")
            if lifecycle not in instances_lifecycle.keys():
                instances_lifecycle[lifecycle] = []
            instances_lifecycle[lifecycle].append(instance["InstanceId"])

    return instances_lifecycle


def get_instance_lifecycle_by_ids(
    client: boto3.client, instance_ids: List[str]
) -> Dict:
    res = client.describe_instances(InstanceIds=instance_ids)

    instances_lifecycle = defaultdict(List)
    for reservation in res["Reservations"]:
        for instance in reservation["Instances"]:
            lifecycle = instance.get("InstanceLifecycle", "normal")
            if lifecycle not in instances_lifecycle.keys():
                instances_lifecycle[lifecycle] = []
            instances_lifecycle[lifecycle].append(instance["InstanceId"])

    return instances_lifecycle


def stop_instances_any_lifecycle(
    client: boto3.client, instance_lifecycles: dict = None, force: bool = False
) -> List[Dict[str, Any]]:

    results = []
    if "normal" in instance_lifecycles:
        logger.warning(
            "[EC2] Stopping normal instances: {}".format(instance_lifecycles["normal"])
        )

        results.append(
            client.stop_instances(
                InstanceIds=instance_lifecycles["normal"], Force=force
            )
        )

    if "spot" in instance_lifecycles:
        spot_request_ids = []

        response = client.describe_instances(InstanceIds=instance_lifecycles["spot"])

        for reservation in response["Reservations"]:
            for inst in reservation["Instances"]:
                lifecycle = inst.get("InstanceLifecycle", "normal")

                if lifecycle == "spot":
                    spot_request_ids.append(inst["SpotInstanceRequestId"])

        logger.info("[EC2] Spot request IDs: {}".format(spot_request_ids))

        spot_instance_requests = []
        paginator = client.get_paginator("describe_spot_instance_requests")
        for p in paginator.paginate(SpotInstanceRequestIds=spot_request_ids):
            for s in p["SpotInstanceRequests"]:
                spot_instance_requests.append(s)

        (
            persistent_spot_request_ids,
            persistent_spot_instance_ids,
            one_time_spot_request_ids,
            one_time_spot_instance_ids,
        ) = ([], [], [], [])
        for request in spot_instance_requests:
            if request["Type"] == "persistent":
                persistent_spot_request_ids.append(request["SpotInstanceRequestId"])
                persistent_spot_instance_ids.append(request["InstanceId"])
            elif request["Type"] == "one-time":
                one_time_spot_request_ids.append(request["SpotInstanceRequestId"])
                one_time_spot_instance_ids.append(request["InstanceId"])

        # Handle persistent spots
        if persistent_spot_instance_ids:
            logger.warning(
                "[EC2] Stopping persistent spot instances: {}".format(
                    persistent_spot_instance_ids
                )
            )
            results.append(
                client.stop_instances(
                    InstanceIds=persistent_spot_instance_ids, Force=force
                )
            )

        # Handle one-time spots
        if one_time_spot_request_ids and one_time_spot_instance_ids:
            logger.warning(
                "[EC2] Canceling one-time spot requests: {}".format(
                    one_time_spot_request_ids
                )
            )
            client.cancel_spot_instance_requests(
                SpotInstanceRequestIds=one_time_spot_request_ids
            )

            logger.warning(
                "[EC2] Terminating one-time spot instances: {}".format(
                    one_time_spot_instance_ids
                )
            )
            results.append(
                client.terminate_instances(InstanceIds=one_time_spot_instance_ids)
            )

    if "scheduled" in instance_lifecycles:
        raise FailedActivity("[EC2] Scheduled instances not supported...")
    return results


def start_instances_any_lifecycle(
    client: boto3.client, instance_lifecycles: dict
) -> List[Dict[str, Any]]:
    results = []
    for k, v in instance_lifecycles.items():
        logger.debug("[EC2] Starting %s instance(s): %s" % (k, v))
        response = client.start_instances(InstanceIds=v)
        results.extend(response.get("StartingInstances", []))
    return results


def describe_network_acls(
    client: boto3.client,
    filters: List[Dict[str, Any]] = None,
    network_acl_ids: List[str] = [],
) -> Dict[str, Any]:
    """Describes one or more of your network ACLs."""
    if filters:
        params = dict(Filters=filters)
    else:
        params = dict(NetworkAclIds=network_acl_ids)

    return client.describe_network_acls(**params)


def create_network_acl(
    client: boto3.client, vpc_id: str, tag_name_value: str
) -> Dict[str, Any]:
    """Creates a network ACL in a VPC."""

    params = {}
    params["VpcId"] = vpc_id
    params["TagSpecifications"] = [
        {
            "ResourceType": "network-acl",
            "Tags": [{"Key": "Name", "Value": tag_name_value}],
        }
    ]
    return client.create_network_acl(**params)


def create_network_acl_entry(
    client: boto3.client,
    acl_id: str,
    rule_num: int,
    protocol: str,
    cidr_block: str,
    egress: bool,
    from_port: int,
    to_port: int,
    rule_action: str,
) -> Dict[str, Any]:
    """Creates an entry (a rule) in a network ACL with the specified rule number."""

    params = {}
    params["NetworkAclId"] = acl_id
    params["RuleNumber"] = rule_num
    params["Protocol"] = protocol
    params["CidrBlock"] = cidr_block
    params["Egress"] = egress
    params["RuleAction"] = rule_action
    params["PortRange"] = {"From": from_port, "To": to_port}

    while True:
        try:
            resp = client.create_network_acl_entry(**params)
            break
        except ClientError as e:
            if e.response["Error"]["Code"] == "NetworkAclEntryAlreadyExists":
                logger.warning(
                    "[EC2] Network ACL entry already exists, decrementing rule number by 1 and creating acl entry again..."
                )
                params["RuleNumber"] -= 1
                time.sleep(1)
            else:
                raise FailedActivity(
                    "[EC2] Unexpected error occurred while creating NACL entry: {}".format(
                        str(e)
                    )
                )
    return resp


def delete_network_acl_entry(
    client: boto3.client, acl_id: str, rule_num: int, egress: bool
) -> Dict[str, Any]:
    """Deletes the specified ingress or egress entry (rule) from the specified network ACL."""

    params = {}
    params["NetworkAclId"] = acl_id
    params["RuleNumber"] = rule_num
    params["Egress"] = egress
    return client.delete_network_acl_entry(**params)


def delete_network_acl(client: boto3.client, acl_id: str) -> Dict[str, Any]:
    """Deletes the specified network ACL.
    You can't delete the ACL if it's associated with any subnets.
    You can't delete the default network ACL."""

    params = {}
    params["NetworkAclId"] = acl_id
    return client.delete_network_acl(**params)


def replace_network_acl_association(
    client: boto3.client, acl_id: str, association_id: str
) -> Dict[str, Any]:
    """Changes which network ACL a subnet is associated with."""

    params = {}
    params["AssociationId"] = association_id
    params["NetworkAclId"] = acl_id
    return client.replace_network_acl_association(**params)


def describe_subnets(
    client: boto3.client, filters: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Describes your subnets."""

    logger.debug("[EC2] Subnets query: {}".format(str(filters)))

    results = []
    paginator = client.get_paginator("describe_subnets")
    for p in paginator.paginate(Filters=filters):
        for s in p["Subnets"]:
            results.append(s)
    return results


def acl_rule_entry_not_exists(
    client: boto3.client, nacl_id: str, rule_number: int
) -> bool:
    entries = client.describe_network_acls(NetworkAclIds=[nacl_id])["NetworkAcls"][0][
        "Entries"
    ]

    for e in entries:
        if e["RuleNumber"] == rule_number:
            return False
    return True


def network_failure(
    client: boto3.client,
    vpc_ids: List[str],
    subnet_ids: List[str],
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """This function simulates network failure by creating blackhole ACLs and associating them for every subnet


    Return Structure:
        [
            {
                "SubnetId": str,
                "VpcId": str
                "Before": {
                    "NetworkAclId": str,
                    "NetworkAclAssociationId": str
                },
                "After": {
                    "NetworkAclId": str,
                    "NetworkAclAssociationId": str
                }
            },
            ....
        ]
    """
    results = []

    for vpc_id in vpc_ids:
        logger.info("[EC2] Initiating network failure for VPC ({})...".format(vpc_id))
        blackhole_acl_id = str()

        logger.info("[EC2] Getting NACL associations of target subnets for the VPC...")
        network_acls_response = describe_network_acls(
            client,
            filters=[
                {"Name": "association.subnet-id", "Values": subnet_ids},
                {"Name": "vpc-id", "Values": [vpc_id]},
            ],
            network_acl_ids=[],
        )

        # For every NACL association, replace association with blackhole NACL and persist new association id along with other fields to file
        for network_acl in network_acls_response["NetworkAcls"]:
            # Check if nacl is already between a blackhole ACL
            if not any(
                t.get("Key", None) == "Name"
                and t.get("Value", None) == "blackhole_nacl"
                for t in network_acl["Tags"]
            ):
                for association in network_acl["Associations"]:
                    state_block = {}
                    new_association_id = str()

                    if (
                        association["SubnetId"] in subnet_ids
                        and network_acl["VpcId"] == vpc_id
                    ):
                        if not dry_run:
                            if not blackhole_acl_id:
                                acl_response = create_network_acl(
                                    client, vpc_id, "blackhole_nacl"
                                )
                                logger.debug(
                                    "[EC2] Created blackhole network ACL ({}) for VPC ({})".format(
                                        str(acl_response), vpc_id
                                    )
                                )
                                blackhole_acl_id = acl_response["NetworkAcl"][
                                    "NetworkAclId"
                                ]

                                logger.info("[EC2] Creating blackhole ACL entries...")
                                create_network_acl_entry(
                                    client,
                                    blackhole_acl_id,
                                    rule_num=1,
                                    protocol="-1",
                                    cidr_block="0.0.0.0/0",
                                    egress=False,
                                    from_port=0,
                                    to_port=65535,
                                    rule_action="DENY",
                                )
                                create_network_acl_entry(
                                    client,
                                    blackhole_acl_id,
                                    rule_num=1,
                                    protocol="-1",
                                    cidr_block="0.0.0.0/0",
                                    egress=True,
                                    from_port=0,
                                    to_port=65535,
                                    rule_action="DENY",
                                )

                            new_association_id = replace_network_acl_association(
                                client,
                                blackhole_acl_id,
                                association["NetworkAclAssociationId"],
                            )["NewAssociationId"]

                            logger.info(
                                "[EC2] Replaced original ACL ({}) with blackhole ACL ({}) for subnet ({})".format(
                                    association["NetworkAclId"],
                                    blackhole_acl_id,
                                    association["SubnetId"],
                                )
                            )

                        state_block["SubnetId"] = association["SubnetId"]
                        state_block["VpcId"] = network_acl["VpcId"]
                        state_block["Before"] = {
                            "NetworkAclAssociationId": association[
                                "NetworkAclAssociationId"
                            ],
                            "NetworkAclId": association["NetworkAclId"],
                        }
                        state_block["After"] = {
                            "NetworkAclAssociationId": new_association_id,
                            "NetworkAclId": blackhole_acl_id,
                        }

                        results.append(state_block)
            else:
                logger.info(
                    "[EC2] Skipping existing blackhole NACL ({})".format(
                        network_acl["NetworkAclId"]
                    )
                )

    return results


def instance_state(client: boto3.client, state: str, instance_ids: List[str]) -> bool:

    instances = client.describe_instances(InstanceIds=instance_ids)
    logger.debug("[EC2] instances ({})".format(str(instances)))

    if instances["Reservations"]:
        for i in instances["Reservations"][0]["Instances"]:
            if i["State"]["Name"] != state:
                return False
    else:
        return False

    return True


def instance_failure(
    client: boto3.client,
    az: str,
    dry_run: bool = False,
    filters: List[Dict[str, Any]] = None,
    force: bool = False,
) -> List[Dict[str, Any]]:
    """This function simulates instance failure by stopping normal/spot instances

    Return Structure:
    [
        {
            "InstanceId": str,
            "Before": {
                "State": 'pending'|'running'|'shutting-down'|'terminated'|'stopping'|'stopped'
            }
            "After": {
                "State": 'stopping'|'stopped'
            }
        },
        ....
    ]
    """
    results = []
    non_dry_run_keys = {"StoppingInstances", "TerminatingInstances"}
    dry_run_keys = {"normal", "spot"}

    stop_instances_response = stop_instances(
        client=client, az=az, dry_run=dry_run, filters=filters, force=force
    )

    for instance_response in stop_instances_response:
        instance_state = {}
        if type(instance_response) == str:
            if instance_response in dry_run_keys:
                for id in stop_instances_response[instance_response]:
                    instance_state = {
                        "InstanceId": id,
                        "Before": {"State": str()},
                        "After": {"State": str()},
                    }
                    results.append(instance_state)
        elif type(instance_response) == dict:
            for k in instance_response.keys():
                if k in non_dry_run_keys:
                    for instance in instance_response[k]:
                        instance_state = {
                            "InstanceId": instance["InstanceId"],
                            "Before": {"State": instance["PreviousState"]["Name"]},
                            "After": {"State": instance["CurrentState"]["Name"]},
                        }
                        results.append(instance_state)

    return results
