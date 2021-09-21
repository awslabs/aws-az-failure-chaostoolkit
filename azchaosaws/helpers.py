import os
import json
from logzero import logger
from chaoslib.exceptions import FailedActivity

def validate_fail_az_path(fail_if_exists: bool, path: str,
                          service: str) -> str:
    # Check if path is a dir, fail activity if it is.
    if os.path.isdir(path):
        raise FailedActivity(
            '[{}] path you provided is a path to a directory. Please provide the file name in your path. ({})'.format(service.upper(), path))

    # If extension not specified, append .<service>.json to path
    root, ext = os.path.splitext(path)
    if not ext.lower() == ".json":
        path = "{}.{}.json".format(path, service.lower())
        logger.warning("[{}] File extension .json not provided in path. Appended .json extension to it... ({})".format(
            service.upper(), path))

    # Check if file exists from path, fail activity if it exists.
    if os.path.isfile(path):
        existing_state = json.load(open(path))
        if fail_if_exists:
            # If state is not a dry run, should fail and run rollback action manually
            if not existing_state["DryRun"]:
                raise FailedActivity(
                    '[{}] Existing state file found in path provided, please check the file, keep a backup of it if needed then delete to run this activity. ({})'.format(service.upper(), path))
    else:
        if not fail_if_exists:
            raise FailedActivity(
                '[{}] To rollback AZ failure, you must specify the path to the file generated from fail_az ({})'.format(service.upper(), path))

    return path