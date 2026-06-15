import logging
import sys

def setup_logging(level: str = "INFO") -> None:
    """
    Configures and establishes application-wide structured log parsing outputs.
    Routes clean execution tracing parameters out directly to standard stdout
    streams while suppressing third-party infrastructure framework polling noise.
    """
    log_format = "[%(asctime)s.%(msecs)03d] [%(levelname)s] [%(name)s:%(lineno)d] %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    root_logger = logging.getLogger()
    root_logger.setLevel(level.upper())

    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level.upper())

    formatter = logging.Formatter(fmt=log_format, datefmt=date_format)
    console_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)

    # Prevent massive debug floods coming from low-level network components
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("pymysql").setLevel(logging.WARNING)
    logging.getLogger("asyncpg").setLevel(logging.WARNING)

    logging.info("Application-wide logging infrastructure initialized successfully.")