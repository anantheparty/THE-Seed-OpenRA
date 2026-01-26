import sys
import os
import json
import http.server
import socketserver
from urllib.parse import urlparse

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from openra_api.game_api import GameAPI
from openra_api.intel.intelligence_service import IntelligenceService
from the_seed.utils import LogManager

# Dummy Blackboard
class DummyBlackboard:
    def __init__(self):
        self.data = {}
    
    def update_intelligence(self, key, value):
        self.data[key] = value

# Initialize Service
try:
    print("Connecting to GameAPI...")
    # Use GameAPI directly
    client = GameAPI("localhost", 7445)
    bb = DummyBlackboard()
    intel = IntelligenceService(client, bb)
    print("IntelligenceService initialized.")
except Exception as e:
    print(f"Failed to initialize: {e}")
    sys.exit(1)

class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        
        if parsed.path == "/api/data":
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            
            # Extract data
            zm = intel.zone_manager
            
            zones_data = []
            for z in zm.zones.values():
                zones_data.append({
                    "id": z.id,
                    "center": {"x": z.center.x, "y": z.center.y},
                    "type": z.type,
                    "subtype": z.subtype,
                    "radius": z.radius,
                    "resource_value": z.resource_value, # Now represents the weighted score
                    "owner_faction": z.owner_faction,
                    "combat_strength": {
                        "my": z.my_strength,
                        "enemy": z.enemy_strength,
                        "ally": z.ally_strength
                    },
                    "units": {
                        "my": z.my_units,
                        "enemy": z.enemy_units,
                        "ally": z.ally_units
                    },
                    "structures": {
                        "my": z.my_structures,
                        "enemy": z.enemy_structures,
                        "ally": z.ally_structures
                    },
                    "neighbors": z.neighbors
                })
            
            resp = {
                "width": zm.map_width,
                "height": zm.map_height,
                "zones": zones_data
            }
            
            # Tick Intel (Update state)
            # Force update if needed, but tick handles intervals
            try:
                intel.tick()
                
                # Dump topology to log as JSON
                log_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "debug_zone_topology.md")
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(json.dumps(resp, indent=2, ensure_ascii=False))
                        
            except Exception as e:
                print(f"Tick/Log error: {e}")

            self.wfile.write(json.dumps(resp).encode('utf-8'))
            return

        elif parsed.path == "/" or parsed.path == "/index.html":
            # Serve the static HTML file
            file_path = os.path.join(os.path.dirname(__file__), "static", "visualize.html")
            try:
                with open(file_path, 'rb') as f:
                    content = f.read()
                self.send_response(200)
                self.send_header('Content-type', 'text/html')
                self.end_headers()
                self.wfile.write(content)
                return
            except FileNotFoundError:
                self.send_error(404, "File not found")
                return

        # Fallback to default serving (useful for other static assets if any)
        return http.server.SimpleHTTPRequestHandler.do_GET(self)

PORT = 8000
print(f"Serving at http://localhost:{PORT}")
print(f"Open http://localhost:{PORT} in your browser to view the visualization.")

# Ensure we are in the right directory for relative imports if needed, 
# but we handled absolute path for static file.
# However, SimpleHTTPRequestHandler serves from CWD. 
# We should override list_directory or just use our custom logic.
# Since we only serve the one file and the API, it's fine.

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
