"""Configuration errors."""


class ConfigError(Exception):
    """Raised when a project definition is invalid. Carries every problem found."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("\n".join(errors))
