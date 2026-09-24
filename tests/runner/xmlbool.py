"""XML Schema boolean lexical space."""


class BooleanError(ValueError):
    pass


def parse_xs_boolean(value: str) -> bool:
    text = (value or "").strip()
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    raise BooleanError(f"invalid XML Schema boolean {value!r}")


def parse_optional_xs_boolean(value: str | None, default: bool) -> tuple[bool, bool]:
    """Return (semantic_value, present). Missing uses default and present=False."""
    if value is None:
        return default, False
    return parse_xs_boolean(value), True
