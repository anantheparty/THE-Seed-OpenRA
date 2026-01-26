import os
import sys
import time
import signal
import logging

# Add project root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from agents.economy.api.game_api import GameAPI
from agents.global_blackboard import GlobalBlackboard
from agents.economy.agent import EconomyAgent
from the_seed.utils import LogManager

def main():
    LogManager.configure(logfile_level="debug", console_level="debug", debug_mode=True, log_dir="Logs")
    logger = LogManager.get_logger()
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    for handler in logger.handlers:
        if handler not in root_logger.handlers:
            root_logger.addHandler(handler)
    # 1. Initialize API
    api = GameAPI(host="localhost", port=7445, language="zh")
    if not api.is_server_running():
        logger.error("Game Server not running!")
        sys.exit(1)
        
    # 2. Initialize Blackboard
    global_bb = GlobalBlackboard()
    
    # 3. Initialize Agent
    agent = EconomyAgent("Economy-Standalone", global_bb, api)
    
    # 4. Run Loop
    running = True
    tick_count = 0
    def signal_handler(sig, frame):
        nonlocal running
        logger.info("Stopping...")
        running = False
    signal.signal(signal.SIGINT, signal_handler)
    
    logger.info("Starting Standalone Economy Agent...")
    while running:
        try:
            tick_count += 1
            logger.debug(f"Tick start {tick_count}")
            agent.tick()
            logger.debug(f"Tick end {tick_count}")
            time.sleep(1.0) # Tick every second
        except Exception as e:
            logger.error(f"Loop Error: {e}")
            time.sleep(1.0)

if __name__ == "__main__":
    main()
