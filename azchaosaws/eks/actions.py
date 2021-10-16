# -*- coding: utf-8 -*-
import os
from typing import Any, Dict, List

import boto3
from chaoslib.exceptions import FailedActivity
from chaoslib.types import Configuration
from logzero import logger

from azchaosaws import client
from azchaosaws.asg.actions import (
    asg_in_single_az,
    change_subnets,
    describe_subnets,
    get_asg_by_names,
    modify_capacity,
    remove_az_subnets,
    resume_processes,
)
from azchaosaws.ec2.actions import (
    delete_network_acl,
    instance_failure,
    instance_state,
    network_failure,
    replace_network_acl_association,
    start_instances,
    validate_fail_az_path,
)
from azchaosaws.helpers import read_state, write_state
from azchaosaws.utils import args_fmt

__all__ = ["fail_az", "recover_az"]


@args_fmt
def fail_az(
    az: str = None,
    dry_run: bool = None,
    failure_type: str = "network",
    tags: List[Dict[str, str]] = [{"AZ_FAILURE": "True"}],
    state_path: str = "fail_az.{}.json".format(__package__.split(".", 1)[1]),
    configuration: Configuration = None,
) -> Dict[str, Any]:
    """
    This function simulates the lost of an AZ in an AWS Region for EKS clusters with managed nodegroups. All nodegroups within
    the tagged clusters will be affected.
    For network failure type, it uses a blackhole network ACL with deny all traffic. For instance failure type, it stops normal
    instances with force; stops persistent spot instances; cancels spot requests and terminate one-time spot instances.
    Ensure your target clusters are tagged. ASG(s) that are part of the managed node groups
    will also be impacted.

    Parameters:
        Required:
            az (str): An availability zone
            dry_run (bool): The boolean flag to simulate a dry run or not. Setting to True will only run read-only operations and not make changes to resources. (Accepted values: True | False)

        Optional:
            dry_run: the boolean flag to simulate a dry run or not. Setting to True will only run read only operations and not make changes to resources. (Accepted values: true | false)
            failure_type: The failure type to simulate. (Accepted values: "network" | "instance") (Default: "network")
            tags: A list of key/value pair to identify the cluster(s) by. (Default: [{'AZ_FAILURE': 'True'}])
            state_path (str): Path to generate the state data (Default: fail_az.eks.json). This file is used for recover_az (rollback).

    Output Structure:
        {
            "AvailabilityZone": str,
            "DryRun": bool,
            "Clusters": [
                {
                    "ClusterName": str,
                    "NodeGroups": [
                        {
                            "NodeGroupName: str,
                            "AutoScalingGroups":
                                [
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
                                ]
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
                                    ]
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
        "[EKS] Executing fail_az action with dry_run ({}) ({}).".format(
            "enabled" if dry_run else "disabled", dry_run
        )
    )

    eks_client = client("eks", configuration)
    asg_client = client("autoscaling", configuration)
    ec2_client = client("ec2", configuration)

    fail_az_state = []
    fail_az_state = {"Clusters": []}
    clusters_state = []

    tagged_clusters = set()
    tagged_clusters_response = get_clusters_by_tags(tags, eks_client)
    for c in tagged_clusters_response:
        tagged_clusters.add(c["name"])
    tagged_clusters = list(tagged_clusters)
    logger.debug("[EKS] Tagged cluster(s) ({})".format(str(tagged_clusters)))

    # For every cluster, get nodegroups that have target az
    for c in tagged_clusters:
        target_nodegroups_asgs = get_asgs_of_nodegroups_by_az(
            az=az, cluster_name=c, eks_client=eks_client, asg_client=asg_client
        )

        if not target_nodegroups_asgs:
            raise FailedActivity(
                "No ASG(s) for target cluster nodegroups found in AZ ({}).".format(az)
            )

        # Initialize states
        cluster_state = {"ClusterName": str(), "NodeGroups": []}
        nodegroups_state = []

        # For every nodegroup in cluster, get asgs
        for ng in target_nodegroups_asgs["NodeGroups"]:
            asgs_state, subnets_state, instances_state = [], [], []
            asgs = ng["AutoScalingGroups"]

            for asg in asgs:
                results = {}

                if asg_in_single_az(client=asg_client, asg=asg):
                    # If ASG is for single AZ
                    # UPDATE ASG CAPACITY TO 0
                    results = modify_capacity(
                        client=asg_client, asg=asg, dry_run=dry_run
                    )
                else:
                    # If ASG is across multiple AZs
                    # CHANGE SUBNETS TO NON TARGET AZ OF ASG FOR EVERY ASG
                    results = remove_az_subnets(
                        asg_client=asg_client,
                        ec2_client=ec2_client,
                        az=az,
                        asg=asg,
                        dry_run=dry_run,
                    )

                # Add to state
                asgs_state.append(results)

            if failure_type == "network":
                # Get list of subnets of nodegroup
                ng_subnets = eks_client.describe_nodegroup(
                    clusterName=c, nodegroupName=ng["NodeGroupName"]
                )["nodegroup"]["subnets"]

                # Filter subnets that are from target az to blackhole
                az_subnets_response = describe_subnets(
                    client=ec2_client,
                    subnet_ids=ng_subnets,
                    filters=[
                        {
                            "Name": "availability-zone",
                            "Values": [
                                az,
                            ],
                        }
                    ],
                )
                subnet_ids = [s["SubnetId"] for s in az_subnets_response]
                vpc_ids = list(set([subnet["VpcId"] for subnet in az_subnets_response]))

                logger.warning("[EKS] Subnets to be blackholed ({})".format(subnet_ids))

                # CREATE AND ASSOCIATE BLACKHOLE ACLS FOR SUBNETS IN EVERY VPC
                network_failure_response = network_failure(
                    client=ec2_client,
                    vpc_ids=vpc_ids,
                    subnet_ids=subnet_ids,
                    dry_run=dry_run,
                )

                # Add to state
                subnets_state = network_failure_response

            elif failure_type == "instance":
                # Get list of instance IDs attached to ASGs.
                instance_ids = []
                asg_response = get_asg_by_names(client=asg_client, asg_names=asgs)
                for asg in asg_response["AutoScalingGroups"]:
                    instance_ids.extend(
                        [instance["InstanceId"] for instance in asg["Instances"]]
                    )

                # Filter by instance state by adding instance-state-name in filter, for only pending | running instances (except stopping, stopped, terminated and
                # shutting-down)
                instance_state_names = ["pending", "running"]
                filters = []
                filters.append(
                    {"Name": "instance-state-name", "Values": instance_state_names}
                )

                # Add instance ids to filter
                filters.append({"Name": "instance-id", "Values": instance_ids})

                # Add AZ to filter
                filters.append({"Name": "availability-zone", "Values": [az]})

                # STOP NORMAL/SPOT INSTANCES
                # Forces the instances to stop. The instances do not have an opportunity to flush file system caches or file system metadata. If you use this option,
                # you must perform file system check and repair procedures. This option is not recommended for Windows instances.
                instance_failure_response = instance_failure(
                    client=ec2_client,
                    az=az,
                    dry_run=dry_run,
                    filters=filters,
                    force=True,
                )

                # Add to state
                instances_state = instance_failure_response

            # Add to state
            nodegroups_state.append(
                {
                    "NodeGroupName": ng["NodeGroupName"],
                    "AutoScalingGroups": asgs_state,
                    "Subnets": subnets_state,
                    "Instances": instances_state,
                }
            )

        # Add to state
        cluster_state["ClusterName"] = c
        cluster_state["NodeGroups"] = nodegroups_state

        # Add to state
        clusters_state.append(cluster_state)

    # Add to state
    fail_az_state["AvailabilityZone"] = az
    fail_az_state["DryRun"] = dry_run
    fail_az_state["Clusters"] = clusters_state

    write_state(fail_az_state, state_path)

    return fail_az_state


def recover_az(
    state_path: str = "fail_az.{}.json".format(__package__.split(".", 1)[1]),
    configuration: Configuration = None,
) -> bool:
    """
    This function rolls back the subnet(s), EC2 instance(s), ASG(s) that were affected by the fail_az action to its previous state.
    This function is dependent on the state data generated from fail_az. Note that instances that are in terminated state will not
    be 'rolled' back.

    Parameters:
        Optional:
            state_path (str): Path to the state data from fail_az (Default: fail_az.eks.json)

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
    asg_client = client("autoscaling", configuration)

    # For every cluster, iterate through nodegroups and rollback for every affected ASG, subnet and instance.
    for cluster in fail_az_state["Clusters"]:
        for ng in cluster["NodeGroups"]:
            # Filter target subnets and instances and ASGs if they have an after state with no empty values
            target_asgs = ng["AutoScalingGroups"]
            target_subnets = ng["Subnets"]
            target_instances = [
                instance
                for instance in ng["Instances"]
                if (
                    instance["Before"]["State"] == "pending"
                    or instance["Before"]["State"] == "running"
                )
                and instance["After"]["State"] == "stopping"
            ]  # To refactor and compare diff logic with before and after, might be more optimal

            # Rollback ASGs
            if target_asgs:
                for asg in target_asgs:
                    logger.warning(
                        "[EKS] ({}) Based on the state file found, AZ failure rollback will happen for ASG ({})".format(
                            ng["NodeGroupName"], asg["AutoScalingGroupName"]
                        )
                    )

                    if all(k in asg["Before"] for k in ("AZRebalance", "SubnetIds")):
                        if asg["Before"]["AZRebalance"]:
                            logger.warning(
                                "[EKS] ({}) AZRebalance process will be resumed.".format(
                                    ng["NodeGroupName"]
                                )
                            )
                        logger.warning(
                            "[EKS] ({}) Subnets will be changed back to {}".format(
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

                    if all(
                        k in asg["Before"]
                        for k in ("MinSize", "MaxSize", "DesiredCapacity")
                    ):
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

            else:
                logger.info(
                    "[EKS] ({}) No ASGs to rollback...".format(ng["NodeGroupName"])
                )

            # Rollback subnets ACLs
            if target_subnets:
                logger.warning(
                    "[EKS] ({}) Based on the state file found, AZ failure rollback will happen for subnets ({})".format(
                        ng["NodeGroupName"], [s["SubnetId"] for s in target_subnets]
                    )
                )
                for subnet in target_subnets:
                    logger.warning(
                        "[EKS] ({}) Network ACL will be rolled back to ({})".format(
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
                    "[EKS] ({}) Network ACLs will be deleted ({})".format(
                        subnet["SubnetId"], blackhole_acl_ids
                    )
                )
                for blackhole_acl_id in blackhole_acl_ids:
                    delete_network_acl(ec2_client, blackhole_acl_id)
            else:
                logger.info(
                    "[EKS] ({}) No subnets to rollback...".format(ng["NodeGroupName"])
                )

            # Rollback instances
            if target_instances:
                target_instances_ids = [
                    instance["InstanceId"] for instance in target_instances
                ]

                # Check if instances are stopped. If not, check if they are stopping state, if they are stopping, fail activity. If terminated/shutting-down/pending/running, ignore them.
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
                        "({}) Error rolling back instances as instance state is 'stopping'. Please check ({}) and try again when they are 'stopped'.".format(
                            ng["NodeGroupName"], stopping_instances_ids
                        )
                    )

                logger.warning(
                    "[EKS] ({}) Based on the state file found and instance state, AZ failure rollback will happen for instance(s) ({})".format(
                        ng["NodeGroupName"], stopped_instances_ids
                    )
                )

                logger.warning(
                    "[EKS] ({}) Skipping instance(s) ({}) as they are either in terminated|shutting-down|pending|running state.".format(
                        ng["NodeGroupName"], ignore_instances_ids
                    )
                )

                if not stopped_instances_ids:
                    raise FailedActivity(
                        "[EKS] ({}) Error rolling back instances as instance state is not 'stopped'. Please check ({})".format(
                            ng["NodeGroupName"], target_instances_ids
                        )
                    )

                start_instances(client=ec2_client, instance_ids=stopped_instances_ids)
            else:
                logger.info(
                    "[EKS] ({}) No instances to rollback...".format(ng["NodeGroupName"])
                )

    # Remove state file upon completion
    try:
        logger.warning(
            "[EKS] Completed rollback, removing file ({}) from disk...".format(
                state_path
            )
        )
        os.remove(state_path)
    except Exception as e:
        logger.error("[EKS] Error removing file: %s", str(e), exc_info=1)

    return True


def get_clusters_by_tags(
    tags: List[Dict[str, str]], client: boto3.client
) -> List[Dict[str, Any]]:
    """
    Returns list of cluster names
    """

    paginator = client.get_paginator("list_clusters")
    results = []
    for p in paginator.paginate():
        for cluster in p["clusters"]:
            response = client.describe_cluster(name=cluster)

            # Filter only clusters that has the provided tags
            if all(
                response["cluster"]["tags"].get(k, None) == v
                for t in tags
                for k, v in t.items()
            ):
                results.append(response["cluster"])

    if not results:
        logger.warning(
            "[EKS] No cluster(s) found with matching tag(s): {}".format(tags)
        )

    return results


def get_asgs_of_nodegroups_by_az(
    az: str, cluster_name: str, eks_client: boto3.client, asg_client: boto3.client
) -> Dict[str, Any]:
    """
    Return cluster with nodegroups and autoscaling groups that has the target AZ. Returns None if no asgs with target AZ found.

    Return Structure:
        {
            "ClusterName": str
            "NodeGroups": [
                {
                    "NodeGroupName: str,
                    "AutoScalingGroups": List[str]
                }
                ....
            ]
        }
    """
    logger.info(
        "[EKS] Searching for nodegroup(s) for cluster ({}) in AZ ({}).".format(
            cluster_name, az
        )
    )

    results = {}
    nodegroups = []

    paginator = eks_client.get_paginator("list_nodegroups")
    for p in paginator.paginate(clusterName=cluster_name):
        for nodegroup in p["nodegroups"]:
            response = eks_client.describe_nodegroup(
                clusterName=cluster_name, nodegroupName=nodegroup
            )

            # For every ASG in nodegroup
            asgs = []
            for asg in response["nodegroup"]["resources"]["autoScalingGroups"]:
                logger.info("[EKS] Checking ASG ({})".format(asg["name"]))

                # Check if ASG contains target AZ
                asg_response = get_asg_by_names(
                    client=asg_client, asg_names=[asg["name"]]
                )
                if az in asg_response["AutoScalingGroups"][0]["AvailabilityZones"]:
                    asgs.append(asg["name"])
            if asgs:
                nodegroups.append(
                    {"NodeGroupName": nodegroup, "AutoScalingGroups": asgs}
                )

    if not nodegroups:
        logger.warning(
            "[EKS] No ASG(s) for target cluster nodegroups found in AZ ({}).".format(az)
        )
    else:
        results = {"ClusterName": cluster_name, "NodeGroups": nodegroups}

    return results
