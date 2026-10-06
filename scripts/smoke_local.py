#!/usr/bin/env python3
"""Exercise only an empty temporary library over an ephemeral loopback port."""
import json
from pathlib import Path
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server import Handler, Store


def main():
    with tempfile.TemporaryDirectory(prefix='content-studio-smoke-') as workspace:
        store = Store(workspace)
        # Construct only Store and Handler: no Assistant, source watches,
        # schedulers, generation managers, model configuration or seed import.
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.store = store
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with urlopen(f'http://127.0.0.1:{server.server_port}/api/library', timeout=5) as response:
                assert response.status == 200
                library = json.load(response)
            assert library['articles'] == []
            assert Path(library['workspace']).resolve() == Path(workspace).resolve()
            assert store._knowledge is None and store._events is None
            assert not hasattr(store, '_clue_pipeline')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print('PASS: empty temporary library HTTP smoke; background integrations were never constructed.')


if __name__ == '__main__':
    main()
