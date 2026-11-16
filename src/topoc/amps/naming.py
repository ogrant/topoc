"""Environment-variable naming for generated endpoint placeholders."""


def env_var_name(instance: str) -> str:
    """Environment-variable stem for an instance: upper-cased, with every
    non-alphanumeric character mapped to `_` (`Quoting.LN` -> `QUOTING_LN`)."""
    return "".join(c.upper() if c.isascii() and c.isalnum() else "_" for c in instance)


def endpoint_placeholder(instance: str, suffix: str) -> str:
    """`${<STEM>_<SUFFIX>}`: an endpoint placeholder AMPS expands at startup, e.g.
    `${QUOTING_LN_REPLICATION_INETADDR}`. The deployment layer supplies the
    variable; the generated configuration stays host- and port-agnostic."""
    return f"${{{env_var_name(instance)}_{suffix}}}"
