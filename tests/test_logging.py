import logging

from core.logging import NOISY_LOGGERS, setup_logging


def test_noisy_library_loggers_are_warning_or_higher():
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG)

    setup_logging()

    for name in NOISY_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING
