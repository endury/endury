"""Custom exception hierarchy for Endury."""


class EnduryError(Exception):
    """Base exception for all Endury errors."""


class RepositoryError(EnduryError):
    """Database operation failed."""
