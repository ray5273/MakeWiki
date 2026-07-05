class MakeWikiError(Exception):
    """Base exception for user-facing MakeWiki failures."""


class ConfigError(MakeWikiError):
    """Raised when .makewiki/config.json is invalid."""


class BuildDatabaseError(MakeWikiError):
    """Raised when compile_commands.json cannot be found safely."""


class AnalyzerUnavailableError(MakeWikiError):
    """Raised when a requested analyzer binary is not available."""


class GraphError(MakeWikiError):
    """Raised when graph data is invalid or incomplete."""


class WikiValidationError(MakeWikiError):
    """Raised when generated wiki evidence is missing or invalid."""


class LLMError(MakeWikiError):
    """Raised when an LLM provider request fails."""
