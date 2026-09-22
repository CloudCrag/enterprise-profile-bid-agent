"""Project-specific exceptions for the enterprise tag prototype."""

class EnterpriseProfileError(Exception):
    """Base class for expected command-line errors."""

class InputDataError(EnterpriseProfileError):
    """Raised when an input JSON object has an invalid structure."""

class TagCatalogError(EnterpriseProfileError):
    """Raised when the 60-tag catalog is missing or inconsistent."""

class MissingWorksheetError(EnterpriseProfileError):
    """Raised when a required worksheet is absent."""

class MissingColumnError(EnterpriseProfileError):
    """Raised when a required source column is absent."""

class CompanyDataError(EnterpriseProfileError):
    """Raised when the company Excel sample cannot be adapted safely."""
