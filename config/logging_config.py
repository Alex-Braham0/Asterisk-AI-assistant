import logging
import sys
from logging.handlers import MemoryHandler

def enable_crash_only_websocket_debug():
    # 1. Create the standard console output format
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('[%(asctime)s] [%(name)s] %(message)s', datefmt='%H:%M:%S')
    console_handler.setFormatter(formatter)

    # 2. Create the Memory Ring Buffer
    # capacity=50: Keeps the last 50 websocket frames in memory.
    # flushLevel=ERROR: Only dumps the buffer to the console if an ERROR occurs.
    crash_dump_handler = MemoryHandler(
        capacity=50, 
        flushLevel=logging.ERROR, 
        target=console_handler
    )

    # 3. Attach the buffer to the websockets library
    for ws_logger_name in ['websockets.client', 'websockets.server']:
        ws_logger = logging.getLogger(ws_logger_name)
        ws_logger.setLevel(logging.DEBUG)
        
        # Remove any existing handlers to prevent duplicate spam
        ws_logger.handlers.clear() 
        
        ws_logger.addHandler(crash_dump_handler)
        
        # Prevent the logs from bubbling up to your root logger and bypassing the buffer
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

    # Prevent massive debug floods coming from low-level network components
    logging.getLogger("websockets").setLevel(logging.WARNING)

    enable_crash_only_websocket_debug()

    logging.getLogger("asyncio").setLevel(logging.WARNING)
    logging.getLogger("pymysql").setLevel(logging.WARNING)
    logging.getLogger("asyncpg").setLevel(logging.WARNING)

    logging.info("Application-wide logging infrastructure initialized successfully.")