import json
import os
import http.server
import socketserver
import sys
from urllib.parse import urlparse

try:
    from openra_state.api_client import GameAPI
    from openra_state.intel.intelligence_service import IntelligenceService
except ModuleNotFoundError:
    sys.path.append(os.path.dirname(os.path.dirname(__file__)))
    from openra_state.api_client import GameAPI
    from openra_state.intel.intelligence_service import IntelligenceService


class DummyBlackboard:
    def __init__(self):
        self.data = {}

    def update_intelligence(self, key, value):
        self.data[key] = value


try:
    print("Connecting to GameAPI...")
    client = GameAPI("localhost", 7445)
    bb = DummyBlackboard()
    intel = IntelligenceService(client, bb)
    print("IntelligenceService initialized.")
except Exception as e:
    print(f"Failed to initialize: {e}")
    raise SystemExit(1)


class Handler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/data":
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            zm = intel.zone_manager
            zones_data = []
            for z in zm.zones.values():
                zones_data.append(
                    {
                        "id": z.id,
                        "center": {"x": z.center.x, "y": z.center.y},
                        "type": z.type,
                        "subtype": z.subtype,
                        "radius": z.radius,
                        "resource_value": z.resource_value,
                        "owner_faction": z.owner_faction,
                        "combat_strength": {
                            "my": z.my_strength,
                            "enemy": z.enemy_strength,
                            "ally": z.ally_strength,
                        },
                        "units": {
                            "my": z.my_units,
                            "enemy": z.enemy_units,
                            "ally": z.ally_units,
                        },
                        "structures": {
                            "my": z.my_structures,
                            "enemy": z.enemy_structures,
                            "ally": z.ally_structures,
                        },
                        "neighbors": z.neighbors,
                    }
                )
            resp = {
                "width": zm.map_width,
                "height": zm.map_height,
                "zones": zones_data,
            }
            try:
                intel.tick()
                log_path = os.path.join(os.path.dirname(__file__), "debug_zone_topology.md")
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write(json.dumps(resp, indent=2, ensure_ascii=False))
            except Exception as e:
                print(f"Tick/Log error: {e}")
            self.wfile.write(json.dumps(resp).encode("utf-8"))
            return
        elif parsed.path == "/" or parsed.path == "/index.html":
            file_path = os.path.join(os.path.dirname(__file__), "static", "visualize.html")
            try:
                with open(file_path, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(content)
                return
            except FileNotFoundError:
                self.send_error(404, "File not found")
                return
        return http.server.SimpleHTTPRequestHandler.do_GET(self)


PORT = 8000
print(f"Serving at http://localhost:{PORT}")
print(f"Open http://localhost:{PORT} in your browser to view the visualization.")

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
