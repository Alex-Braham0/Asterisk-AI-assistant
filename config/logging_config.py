import logging
import sys
from logging.handlers import MemoryHandler
from dashboard.server import DashboardLogHandler

# 1. Create a custom handler that ruthlessly ignores buffer capacity
class CrashOnlyMemoryHandler(MemoryHandler):
    def shouldFlush(self, record):
        # ONLY flush if the log severity is ERROR or CRITICAL. 
        # By removing the capacity check, it will seamlessly overwrite old frames in RAM 
        # and stay completely invisible until an actual crash happens.
        return record.levelno >= self.flushLevel

def enable_crash_only_websocket_debug():
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('[%(asctime)s] [%(name)s] %(message)s', datefmt='%H:%M:%S')
    console_handler.setFormatter(formatter)

    # 2. Use the new custom class
    crash_dump_handler = CrashOnlyMemoryHandler(
        capacity=100, # Keeps the last 100 frames in memory (about 2 seconds of history)
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

    dash_handler = DashboardLogHandler()
    dash_handler.setFormatter(formatter)
    dash_handler.setLevel(level.upper())
    root_logger.addHandler(dash_handler)

    # Prevent massive debug floods coming from low-level network components
    logging.getLogger("websockets").setLevel(logging.WARNING)

    enable_crash_only_websocket_debug()

    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("pymysql").setLevel(logging.WARNING)
    logging.getLogger("asyncpg").setLevel(logging.WARNING)

    logging.getLogger("aiohttp.access").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.server").setLevel(logging.WARNING)

    logging.info("Application-wide logging infrastructure initialized successfully.")