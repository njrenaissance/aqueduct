"""errors - exception hierarchy for the Graph upload path (ADR-0012).

Every failure the SharePoint upload raises is a subclass of :class:`AqueductError`, so a caller can catch
one failure mode without swallowing every other one. Translate lower-level exceptions with
``raise GraphError(...) from original`` to keep the traceback chain intact.
"""

from __future__ import annotations


class AqueductError(Exception):
    """Base class for errors raised by the Graph upload modules."""


class AuthError(AqueductError):
    """Graph credentials are missing, malformed, or a token could not be acquired."""


class GraphError(AqueductError):
    """A Graph request could not be completed (access, throttling, an unusable destination)."""


class UploadError(GraphError):
    """A Graph lookup or upload call was rejected; worth a retry before it is recorded as a failure."""


class IntegrityError(AqueductError):
    """An uploaded file's size or QuickXorHash did not match what SharePoint reports."""
