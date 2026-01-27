import os
import sys
import time
import signal
import logging
import traceback

# Add project root to sys.path
# We are in d:\THE-Seed-OpenRA\agents\economy
# Root is ../../
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

try:
    from agents.economy.api.game_api import GameAPI
    from agents.economy.agent import EconomyAgent
except ImportError as e:
    
    try:
        # Local import fallback for D:\economy structure
        sys.path.append(os.path.dirname(__file__)) # Add current dir
        from api.game_api import GameAPI
        from agent import EconomyAgent
        
    except ImportError as e2:
        print(f"Import Error: {e}")
        print(f"Fallback Import Error: {e2}")
        traceback.print_exc()
        sys.exit(1)

def main():
    # Standard logging setup (similar to openra_state/intel/intelligence_service.py)
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            # logging.FileHandler("economy_agent.log") # Optional file logging
        ]
    )
    logger = logging.getLogger("EconomyStandalone")

    # 1. Initialize API
    api = GameAPI(host="localhost", port=7445, language="zh")
    if not api.is_server_running():
        logger.error("Game Server not running!")
        sys.exit(1)
        
    # 2. Initialize Agent (No GlobalBlackboard needed)
    agent = EconomyAgent("Economy-Standalone", api)
    
    # 3. Run Loop
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
