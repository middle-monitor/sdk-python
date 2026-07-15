import pytest

from middlemonitor.errors import ConfigError, MiddleMonitorError, NotInitializedError


class TestMiddleMonitorError:
    def test_is_exception(self):
        err = MiddleMonitorError("base error")
        assert isinstance(err, Exception)
        assert str(err) == "base error"


class TestNotInitializedError:
    def test_default_message(self):
        err = NotInitializedError()
        assert str(err) == "client not initialized"

    def test_is_middlemonitor_error(self):
        assert isinstance(NotInitializedError(), MiddleMonitorError)


class TestConfigError:
    def test_default_message(self):
        err = ConfigError()
        assert str(err) == "endpoint and token required"

    def test_custom_message(self):
        err = ConfigError("custom detail")
        assert str(err) == "custom detail"

    def test_is_middlemonitor_error(self):
        assert isinstance(ConfigError(), MiddleMonitorError)
