"""Lightweight in-process notifications for effective configuration changes."""

from __future__ import annotations

from collections.abc import Callable, Iterable

Subscriber = Callable[[Iterable[str]], None]

_subscribers: list[Subscriber] = []


def subscribe(callback: Subscriber) -> None:
    if callback not in _subscribers:
        _subscribers.append(callback)


def notify_changed(keys: Iterable[str]) -> None:
    key_list = tuple(str(key) for key in keys)
    if not key_list:
        return
    for callback in tuple(_subscribers):
        callback(key_list)

