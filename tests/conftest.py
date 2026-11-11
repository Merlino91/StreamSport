import atexit
import os
import sys
import tempfile

# Configure isolated temporary data storage before any app module imports app.config.
# Ensures pytest runs never mutate production data/events.db or leave artifacts in data/posters/.
_TEST_DATA_TEMPDIR = tempfile.TemporaryDirectory(
    prefix="streamsport_pytest_data_", ignore_cleanup_errors=True
)
os.environ["STREAMSPORT_DATA_DIR"] = _TEST_DATA_TEMPDIR.name
os.environ.setdefault("STREAMSPORT_TESTING", "1")


def _cleanup_test_data() -> None:
    db_mod = sys.modules.get("app.services.db_service")
    if db_mod is not None and hasattr(db_mod, "db_service"):
        try:
            db_mod.db_service.close()
        except Exception:
            pass
    _TEST_DATA_TEMPDIR.cleanup()


atexit.register(_cleanup_test_data)


def pytest_sessionfinish(session, exitstatus) -> None:
    _cleanup_test_data()
