def pytest_addoption(parser):
    parser.addoption("--run-slow", action="store_true", default=False,
                     help="Run slow tests (Bedrock managed eval job, ~15 min)")


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow (managed eval job)")


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--run-slow"):
        skip = __import__("pytest").mark.skip(reason="Pass --run-slow to run managed eval job tests")
        for item in items:
            if "slow" in item.keywords:
                item.add_marker(skip)
