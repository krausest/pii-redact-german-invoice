"""The models a pipeline uses, each built on first use and kept for the process."""

from __future__ import annotations

import threading
from collections.abc import Callable

from backend.classifiers.base import Classifier
from backend.unwarp import DocUnwarper


class ModelRegistry:
    """Classifiers by name and the unwarper, built lazily: a worker never asked
    for a model never pays its gigabyte. ``classifier=`` hands over one built
    classifier (tests, replay), which then answers to every name."""

    def __init__(
        self,
        classifier: Classifier | None = None,
        classifier_factories: dict[str, Callable[[], Classifier]] | None = None,
        default_classifier: str | None = None,
        unwarper: DocUnwarper | None = None,
        unwarper_factory: Callable[[], DocUnwarper] | None = None,
    ) -> None:
        self._factories = dict(classifier_factories or {})
        self._classifiers: dict[str, Classifier] = {}
        self._default = default_classifier or ""
        if classifier is not None:
            self._classifiers[self._default] = classifier
        self._unwarper = unwarper
        self._unwarper_factory = unwarper_factory
        self._lock = threading.Lock()

    def classifier(self, name: str | None = None) -> Classifier:
        """The classifier called ``name`` (``None``: the default)."""
        if not self._factories and len(self._classifiers) == 1:
            return next(iter(self._classifiers.values()))
        key = self._default if name is None else name
        found = self._classifiers.get(key)
        if found is not None:
            return found
        with self._lock:
            if key not in self._classifiers:
                factory = self._factories.get(key)
                if factory is None:
                    raise RuntimeError(f"no classifier configured under {key!r}")
                self._classifiers[key] = factory()
        return self._classifiers[key]

    def unwarper(self) -> DocUnwarper:
        if self._unwarper is None:
            with self._lock:
                if self._unwarper is None:
                    if self._unwarper_factory is None:
                        raise RuntimeError("unwarp requested but no DocUnwarper is configured")
                    self._unwarper = self._unwarper_factory()
        return self._unwarper
