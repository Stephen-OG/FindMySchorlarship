import logging
import sys

LOGGER_NAME = "scholarship_finder"


def get_logger():
    logger = logging.getLogger(LOGGER_NAME)

    if not logger.handlers:
        logger.setLevel(logging.INFO)

        handler = logging.StreamHandler(sys.stdout)
        formatter = logging.Formatter(
            "[%(asctime)s] %(levelname)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


logger = get_logger()

# logger.info(f"New chat message: {message}")
