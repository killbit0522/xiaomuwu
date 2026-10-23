"""Small registry for server-side book source adapters.

Adapters are deliberately Python callables rather than imported JavaScript rules.  This
keeps untrusted source files away from the server while giving the online library a
single place to order, report and dispatch its configured sources.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class BookSourceAdapter:
    key: str
    name: str
    searcher: object
    reader: object = None

    @property
    def prefix(self):
        return self.key + ":"

    def accepts(self, identifier):
        return bool(self.reader) and str(identifier or "").startswith(self.prefix)

    def native_id(self, identifier):
        return str(identifier).split(":", 1)[1]


class BookSourceRegistry:
    def __init__(self, adapters=()):
        self._adapters = []
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter):
        if not isinstance(adapter, BookSourceAdapter):
            raise TypeError("adapter must be a BookSourceAdapter")
        if not adapter.key or any(existing.key == adapter.key for existing in self._adapters):
            raise ValueError("book source keys must be non-empty and unique")
        self._adapters.append(adapter)
        return adapter

    def adapters(self):
        return tuple(self._adapters)

    def reader_for(self, identifier):
        for adapter in self._adapters:
            if adapter.accepts(identifier):
                return adapter
        return None
