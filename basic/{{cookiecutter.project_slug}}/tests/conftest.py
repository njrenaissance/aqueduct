"""Shared pytest configuration."""


def pytest_configure(config):
    """Show live log output at INFO under `pytest -v`, unless a log-cli level was set explicitly."""
    if config.option.verbose > 0 and config.option.log_cli_level is None:
        config.option.log_cli_level = "INFO"
