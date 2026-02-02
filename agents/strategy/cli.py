
import sys
import os
import threading
import time

# Ensure project root is in path (Robust against CWD)
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, "../../"))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agents.strategy.strategic_agent import StrategicAgent

def strategy_cli():
    print("==========================================")
    print("   OpenRA Strategic Commander - CLI ")
    print("==========================================")
    print("Commands:")
    print("  start     - Start the strategic agent")
    print("  stop      - Stop the strategic agent")
    print("  cmd <msg> - Send a user command (e.g., 'cmd attack enemy base')")
    print("  status    - Show current squad status")
    print("  eco start - Enable Economy Agent")
    print("  eco stop  - Disable Economy Agent")
    print("  tac start - Enable Tactical Core (Biods)")
    print("  tac stop  - Disable Tactical Core")
    print("  tac show  - Show Tactical Log Window")
    print("  tac hide  - Hide Tactical Log Window")
    print("  exit      - Exit CLI")
    print("==========================================")

    agent = StrategicAgent()
    agent_thread = None

    # Use a separate thread to read input so we don't block logging? 
    # Or just rely on the fact that agent runs in background thread.
    # The issue might be logging interfering with input prompt.
    # But basic input() should block until user enters something.
    
    # Wait a bit for initialization logs to settle
    time.sleep(0.5)

    while True:
        try:
            # Check if thread died
            if agent_thread and not agent_thread.is_alive() and agent.running:
                print("Agent thread died unexpectedly!")
                agent.running = False
                agent_thread = None

            if sys.stdin.isatty():
                print("StrategyCLI> ", end="", flush=True)
                user_input = sys.stdin.readline().strip()
            else:
                # Fallback for non-interactive environments
                user_input = input("StrategyCLI> ").strip()
                
            if not user_input:
                continue
            
            parts = user_input.split(" ", 1)
            command = parts[0].lower()
            args = parts[1] if len(parts) > 1 else ""

            if command == "exit":
                if agent.running:
                    print("Stopping agent before exit...")
                    agent.stop()
                    if agent_thread:
                        agent_thread.join()
                break
            
            elif command == "start":
                if agent.running:
                    print("Agent is already running.")
                else:
                    print("Starting agent...")
                    agent_thread = threading.Thread(target=agent.start, daemon=True)
                    agent_thread.start()
                    print("Agent started in background.")

            elif command == "stop":
                if not agent.running:
                    print("Agent is not running.")
                else:
                    print("Stopping agent...")
                    agent.stop()
                    if agent_thread:
                        agent_thread.join()
                    print("Agent stopped.")

            elif command == "cmd":
                if not args:
                    print("Usage: cmd <message>")
                    continue
                
                cmd_file = "user_command.txt"
                with open(cmd_file, "w", encoding="utf-8") as f:
                    f.write(args)
                print(f"Command written to {cmd_file}: {args}")

            elif command == "status":
                if not agent.running:
                    print("Agent is not running, cannot fetch status.")
                    continue
                
                # Fetch status directly from squad manager
                status = agent.squad_manager.get_status()
                print("Squad Status:", status)
                
                # Also show economy status
                if agent.economy_agent:
                     eco_status = "ACTIVE" if agent.economy_agent.is_active else "INACTIVE"
                     print(f"Economy Status: {eco_status}")

            elif command == "eco":
                if not args:
                    print("Usage: eco <start|stop>")
                    continue
                
                sub_cmd = args.lower()
                if sub_cmd == "start":
                    agent.enable_economy()
                    print("Economy Agent ENABLED.")
                elif sub_cmd == "stop":
                    agent.disable_economy()
                    print("Economy Agent DISABLED.")
                else:
                    print(f"Unknown eco command: {sub_cmd}")

            elif command == "tac":
                if not args:
                    print("Usage: tac <start|stop|show|hide>")
                    continue

                sub_cmd = args.lower()
                enhancer = agent.tactical_enhancer
                
                if not enhancer:
                    print("Tactical Core not initialized.")
                    continue

                if sub_cmd == "start":
                    enhancer.enabled = True
                    print("Tactical Core ENABLED (Interception Active).")
                elif sub_cmd == "stop":
                    enhancer.enabled = False
                    print("Tactical Core DISABLED (Pass-through Mode).")
                elif sub_cmd == "show":
                    enhancer.show_log_window()
                    print("Tactical Log Window SHOWN.")
                elif sub_cmd == "hide":
                    enhancer.hide_log_window()
                    print("Tactical Log Window HIDDEN.")
                else:
                    print(f"Unknown tac command: {sub_cmd}")

            else:
                print(f"Unknown command: {command}")

        except KeyboardInterrupt:
            print("\nExiting...")
            if agent.running:
                agent.stop()
            break
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    strategy_cli()
