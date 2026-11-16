"""Loads a topic catalog from its JSON form: an array of entries

    { "definition": "...", "name_pattern": "...",
      "codec": "...",                         (required)
      "routed": bool, "wire_keys": [...],     (optional: false, [])
      "fields": [ { "name": "...", "wire_name": "...",
                    "children": [...] } ] }   (optional: [])

Unknown members are ignored, so producers may carry extra information. A
`name_pattern` may use braces only as whole-segment `{}` holes.
"""

import json

from topoc.catalog import Catalog, CatalogEntry, CatalogField
from topoc.errors import CatalogError

# `context` arguments name the value being decoded in error messages, e.g.
# `entry 2 field 0`.


def parse_catalog_json(text: str) -> Catalog:
    """Raises `CatalogError` on malformed input."""
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise CatalogError(f"invalid JSON: {error}") from error
    entries = _as_list(document, "catalog")
    return Catalog(_parse_entry(entry, index) for index, entry in enumerate(entries))


def _parse_entry(value: object, index: int) -> CatalogEntry:
    context = f"entry {index}"
    obj = _as_dict(value, context)
    return CatalogEntry(
        definition=_required_string(obj, "definition", context),
        name_pattern=_name_pattern(obj, context),
        codec=_required_string(obj, "codec", context),
        routed=_optional_bool(obj, "routed", context),
        wire_keys=_strings(_optional_list(obj, "wire_keys", context), f"{context} 'wire_keys'"),
        fields=_parse_fields(_optional_list(obj, "fields", context), context),
    )


def _name_pattern(obj: dict[str, object], context: str) -> str:
    """A topic name or name pattern: braces may only form whole-segment `{}` holes."""
    pattern = _required_string(obj, "name_pattern", context)
    for segment in pattern.split("/"):
        if segment != "{}" and ("{" in segment or "}" in segment):
            raise CatalogError(
                f"{context}: invalid 'name_pattern' '{pattern}': braces may only form "
                "whole-segment '{}' holes"
            )
    return pattern


def _parse_fields(fields: list[object], context: str) -> list[CatalogField]:
    parsed: list[CatalogField] = []
    for index, value in enumerate(fields):
        field_context = f"{context} field {index}"
        obj = _as_dict(value, field_context)
        parsed.append(
            CatalogField(
                name=_required_string(obj, "name", field_context),
                wire_name=_required_string(obj, "wire_name", field_context),
                children=_parse_fields(
                    _optional_list(obj, "children", field_context), field_context
                ),
            )
        )
    return parsed


def _as_dict(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise CatalogError(f"{context}: expected an object")
    return value


def _as_list(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise CatalogError(f"{context}: expected an array")
    return value


def _required_string(obj: dict[str, object], key: str, context: str) -> str:
    if key not in obj:
        raise CatalogError(f"{context}: missing '{key}'")
    value = obj[key]
    if not isinstance(value, str):
        raise CatalogError(f"{context}: '{key}' must be a string")
    return value


def _optional_bool(obj: dict[str, object], key: str, context: str) -> bool:
    value = obj.get(key, False)
    if not isinstance(value, bool):
        raise CatalogError(f"{context}: '{key}' must be a boolean")
    return value


def _optional_list(obj: dict[str, object], key: str, context: str) -> list[object]:
    return _as_list(obj[key], f"{context} '{key}'") if key in obj else []


def _strings(values: list[object], context: str) -> list[str]:
    strings: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise CatalogError(f"{context}: expected an array of strings")
        strings.append(value)
    return strings
