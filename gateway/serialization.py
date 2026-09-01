from __future__ import annotations

import dataclasses
import datetime as dt
import math
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any


def to_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Decimal):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")

    module = type(value).__module__.split(".", 1)[0]
    if module == "numpy":
        if hasattr(value, "item"):
            return to_jsonable(value.item())
        if hasattr(value, "tolist"):
            return to_jsonable(value.tolist())
    if module == "pandas":
        if hasattr(value, "to_dict"):
            try:
                return to_jsonable(value.to_dict(orient="records"))
            except TypeError:
                return to_jsonable(value.to_dict())

    if dataclasses.is_dataclass(value):
        return to_jsonable(dataclasses.asdict(value))
    if hasattr(value, "model_dump"):
        return to_jsonable(value.model_dump())
    if hasattr(value, "dict") and callable(value.dict):
        return to_jsonable(value.dict())
    if isinstance(value, Mapping):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [to_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return to_jsonable(
            {key: item for key, item in vars(value).items() if not key.startswith("_")}
        )
    return str(value)


def count_rows(value: Any) -> int:
    if value is None:
        return 0
    if hasattr(value, "shape"):
        try:
            return int(value.shape[0])
        except (TypeError, ValueError, IndexError):
            pass
    if isinstance(value, Mapping):
        nested = sum(count_rows(item) for item in value.values())
        return nested or len(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return len(value)
    return 1
