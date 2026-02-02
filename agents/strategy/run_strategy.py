
import sys
import os
import logging
import time
import signal

# Ensure project root is in path
sys.path.append(os.getcwd())

from agents.strategy.strategic_agent import StrategicAgent

# Configure root logger to output to console

logger = logging.getLogger("Launcher")

def main():
    logger.info("==========================================")
    logger.info("   OpenRA Strategic Commander - Launching ")
    logger.info("==========================================")
    
    # Check for .env
    env_path = os.path.join("agents", "strategy", ".env")
    if not os.path.exists(env_path):
        logger.warning(f"Config file not found at {env_path}!")
        logger.warning("Please create it with LLM_API_KEY, GAME_HOST, etc.")
        return

    agent = None
    
    def signal_handler(sig, frame):
        logger.info("Interrupt received, shutting down...")
        if agent:
            agent.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    try:
        agent = StrategicAgent()
        logger.info("Strategic Agent initialized successfully.")
        logger.info("Starting main loop... (Press Ctrl+C to stop)")
        agent.start()
    except Exception as e:
        logger.critical(f"Failed to start Strategic Agent: {e}", exc_info=True)
    finally:
        if agent and agent.running:
            agent.stop()

if __name__ == "__main__":
    main()
