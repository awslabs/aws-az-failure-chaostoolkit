import functools

def args_fmt(fn):
    @functools.wraps(fn)
    def wrapper(**kwargs):
        if type(kwargs["dry_run"]) == str:
            kwargs["dry_run"] = kwargs["dry_run"].lower() == "true" if (kwargs["dry_run"].lower() == "true" or kwargs["dry_run"].lower() == "false") else None
        return fn(**kwargs)
    return wrapper