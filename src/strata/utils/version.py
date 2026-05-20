from importlib.metadata import version


def get_version() -> str:
    """This function gets the version as defined in the `VERSION.txt` file.

    Returns:
        str: The version as a string.
    """
    return version("xyz-strata")
