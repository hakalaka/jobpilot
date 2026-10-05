"""Tiny helpers for building Spark SQL safely."""


def sql_str(s: str) -> str:
    """Quote a Python string as a Spark SQL string literal."""
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"
