import logging
import sys
from logging.handlers import MemoryHandler

class CrashOnlyMemoryHandler(MemoryHandler):
    def shouldFlush(self, record):
        return record.levelno >= self.flushLevel

def enable_crash_only_websocket_debug():
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('[%(asctime)s] [%(name)s] %(message)s', datefmt='%H:%M:%S')
    console_handler.setFormatter(formatter)

    crash_dump_handler = CrashOnlyMemoryHandler(
        capacity=100, 
        flushLevel=logging.ERROR, 
        target=console_handler
    )

    for ws_logger_name in ['websockets.client', 'websockets.server']:
        ws_logger = logging.getLogger(ws_logger_name)
        ws_logger.setLevel(logging.DEBUG)
        
        ws_logger.handlers.clear() 
        ws_logger.addHandler(crash_dump_handler)
        ws_logger.propagate = False

def setup_logging(level: str = "INFO") -> None:
    log_format = "[%(asctime)s.%(msecs)03d] [%(levelname)s] [%(name)s:%(lineno)d] %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    root_logger = logging.getLogger()
    root_logger.setLevel(level.upper())

    if root_logger.hasHandlers():
        root_logger.handlers.clear()

    # The StreamHandler outputs to sys.stdout, which our Dashboard server will now intercept
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level.upper())
    formatter = logging.Formatter(fmt=log_format, datefmt=date_format)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    logging.getLogger("websockets").setLevel(logging.WARNING)
    enable_crash_only_websocket_debug()

    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("pymysql").setLevel(logging.WARNING)
    logging.getLogger("asyncpg").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.server").setLevel(logging.WARNING)

    logging.info("Application-wide logging infrastructure initialized successfully.")