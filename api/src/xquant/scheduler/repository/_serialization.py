from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from types import UnionType
from typing import Any, TypeVar, Union, get_args, get_origin, get_type_hints
from zoneinfo import ZoneInfo

T = TypeVar("T")


def enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def to_jsonable(value: Any) -> Any:
    from xquant.scheduler.domain import Trigger

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_jsonable(to_dict())
    if isinstance(value, Trigger):
        return trigger_to_payload(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: to_jsonable(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, ZoneInfo):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [to_jsonable(item) for item in value]
    return value


def dumps(value: Any) -> str:
    return json.dumps(
        to_jsonable(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def loads(payload: str | bytes | None) -> Any:
    if payload is None:
        return None
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    return json.loads(payload)


def to_domain(value: Any, domain_type: type[T]) -> T:
    from xquant.scheduler.domain import Trigger

    origin = get_origin(domain_type)
    args = get_args(domain_type)
    if origin in (Union, UnionType):
        candidates = [arg for arg in args if arg is not type(None)]
        if value is None and len(candidates) != len(args):
            return value
        if candidates:
            return to_domain(value, candidates[0])
        return value
    if origin in (list, set, frozenset, tuple, Sequence):
        item_type = args[0] if args else Any
        items = [to_domain(item, item_type) for item in value]
        if origin in (list, Sequence):
            return items
        if origin is set:
            return set(items)
        if origin is frozenset:
            return frozenset(items)
        return tuple(items)
    if origin is dict:
        key_type, value_type = args if len(args) == 2 else (Any, Any)
        return {
            to_domain(key, key_type): to_domain(item, value_type)
            for key, item in value.items()
        }
    if domain_type in (Any, object):
        return value
    if domain_type is type(None):
        return value
    if isinstance(domain_type, type) and isinstance(value, domain_type):
        return value

    from_dict = getattr(domain_type, "from_dict", None)
    if callable(from_dict):
        return from_dict(value)

    if isinstance(domain_type, type) and issubclass(domain_type, Enum):
        return domain_type(value)
    if domain_type is datetime:
        return datetime.fromisoformat(value)
    if domain_type is date:
        return date.fromisoformat(value)
    if isinstance(domain_type, type) and issubclass(domain_type, Trigger):
        return trigger_from_payload(value)
    if is_dataclass(domain_type):
        type_hints = get_type_hints(domain_type)
        kwargs: dict[str, Any] = {}
        for field in fields(domain_type):
            if field.name not in value:
                continue
            field_type = type_hints.get(field.name, field.type)
            kwargs[field.name] = to_domain(value[field.name], field_type)
        return domain_type(**kwargs)
    return value


def decode_json(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (bytes, str)):
        if not value:
            return default
        return json.loads(value)
    return value


def as_sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return value
    return ()


def trigger_to_payload(trigger: Any) -> dict[str, Any]:
    to_dict = getattr(trigger, "to_dict", None)
    if not callable(to_dict):
        raise TypeError(
            f"trigger {type(trigger).__name__} does not implement to_dict()"
        )
    payload = to_dict()
    if not isinstance(payload, Mapping):
        raise TypeError("trigger to_dict() must return a mapping")
    return dict(payload)


def trigger_from_payload(payload: Any) -> Any:
    from xquant.scheduler import domain

    if payload is None:
        raise ValueError("trigger payload is required")
    if not isinstance(payload, Mapping):
        raise TypeError("trigger payload must be a mapping")
    load_trigger = getattr(domain, "load_trigger", None)
    if not callable(load_trigger):
        raise TypeError("xquant.scheduler.domain does not expose load_trigger()")
    return load_trigger(dict(payload))
