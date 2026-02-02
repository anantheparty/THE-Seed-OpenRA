import os
import sys
import logging
import time
from dotenv import load_dotenv

# Configure logging first
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)

# Load environment variables
current_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(current_dir, '.env')
if os.path.exists(env_path):
    load_dotenv(env_path)
    logger.info(f"Loaded configuration from {env_path}")
else:
    logger.warning(f"No .env file found at {env_path}")

# Ensure local imports work when running as standalone script
if current_dir not in sys.path:
    sys.path.insert(0, current_dir)

try:
    from infra.game_client import GameClient
    from infra.llm_client import LLMClient
except ImportError as e:
    logger.error(f"Failed to import infrastructure modules: {e}")
    logger.error("Ensure you are running this script from the correct directory or package context.")
    sys.exit(1)

def main():
    logger.info("Initializing Combat Agent Standalone Environment...")
    
    # Configuration (Load from env or default)
    GAME_HOST = os.getenv("GAME_HOST", "127.0.0.1")
    GAME_PORT = int(os.getenv("GAME_PORT", "1234"))
    
    LLM_API_KEY = os.getenv("LLM_API_KEY", "your-api-key")
    LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    LLM_MODEL = os.getenv("LLM_MODEL", "doubao-pro-32k")

    # Initialize Clients
    try:
        game_client = GameClient(host=GAME_HOST, port=GAME_PORT)
        # game_client.connect() # Connect when needed
        logger.info(f"Game Client initialized for {GAME_HOST}:{GAME_PORT}")
        
        llm_client = LLMClient(api_key=LLM_API_KEY, base_url=LLM_BASE_URL, model=LLM_MODEL)
        logger.info(f"LLM Client initialized with model: {LLM_MODEL}")
        
    except Exception as e:
        logger.critical(f"Initialization failed: {e}", exc_info=True)
        sys.exit(1)

    logger.info("Combat Agent is ready. Waiting for commands...")
    
    # Placeholder for Main Loop
    try:
        while True:
            time.sleep(1)
            # In standalone mode, we might poll game state or wait for manual input
            pass
    except KeyboardInterrupt:
        logger.info("Shutting down Combat Agent...")
    finally:
        # game_client.close()
        pass

if __name__ == "__main__":
    main()
