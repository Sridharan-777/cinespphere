import os
import sys

# Ensure import path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from cinesphere.app import create_app

app = create_app()

if __name__ == "__main__":
    port = int(os.getenv("APP_PORT", 3000))
    print(f"Starting CineSphere Cinema Management & Ticket Booking Web Application on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
