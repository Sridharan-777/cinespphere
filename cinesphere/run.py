import os
import sys

# Ensure the parent directory is in sys.path so 'cinesphere' can be resolved
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cinesphere.app import create_app

app = create_app()

if __name__ == '__main__':
    port = 3000
    print(f'Starting CineSphere on port {port}...')
    app.run(host='0.0.0.0', port=port, debug=False)
