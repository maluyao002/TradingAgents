"""Run-local cache for routed data requests.

The cache lives in a :class:`contextvars.ContextVar`, so concurrent analyses do
not share provider responses.  Callers opt in by wrapping one complete analysis
with :func:`data_request_scope` or the :func:`run_data_scope` decorator.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
R = TypeVar("R")

_REQUEST_CACHE: ContextVar[dict[tuple[Any, ...], Any] | None] = ContextVar(
    "tradingagents_request_cache", default=None
)


def _freeze(value: Any) -> Any:
    """Convert common argument values into a stable, hashable representation."""
    if isinstance(value, Mapping):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return tuple(sorted((_freeze(item) for item in value), key=repr))
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def canonical_request_key(
    method: str,
    vendor: str,
    implementation: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> tuple[Any, ...]:
    """Build a key from bound arguments, including implementation defaults."""
    ignored = set(getattr(implementation, "__request_cache_ignore__", ()))
    try:
        bound = inspect.signature(implementation).bind(*args, **kwargs)
        bound.apply_defaults()
        values = []
        for name, value in bound.arguments.items():
            if name in ignored:
                continue
            if name.lower() in {"ticker", "symbol"} and isinstance(value, str):
                value = value.strip().upper()
            elif name.lower() in {"freq", "frequency"} and isinstance(value, str):
                value = value.strip().lower()
            values.append((name, _freeze(value)))
        canonical_args: Any = tuple(values)
    except (TypeError, ValueError):
        canonical_args = (_freeze(args), _freeze(kwargs))
    return (method, vendor, canonical_args)


def get_cached_request(key: tuple[Any, ...]) -> tuple[bool, Any]:
    """Return ``(found, value)`` without confusing a cached ``None`` for a miss."""
    cache = _REQUEST_CACHE.get()
    if cache is None or key not in cache:
        return False, None
    return True, cache[key]


def cache_request(key: tuple[Any, ...], value: Any) -> None:
    """Store a successful result when a run-local scope is active."""
    cache = _REQUEST_CACHE.get()
    if cache is not None:
        cache[key] = value


@contextmanager
def data_request_scope() -> Iterator[None]:
    """Create an isolated cache for one complete analysis run."""
    token = _REQUEST_CACHE.set({})
    try:
        yield
    finally:
        _REQUEST_CACHE.reset(token)


def run_data_scope(func: Callable[P, R]) -> Callable[P, R]:
    """Run a synchronous or asynchronous callable in a fresh data cache scope."""
    if inspect.iscoroutinefunction(func):
        @wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs):
            with data_request_scope():
                return await func(*args, **kwargs)

        return async_wrapper  # type: ignore[return-value]

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs):
        with data_request_scope():
            return func(*args, **kwargs)

    return wrapper
