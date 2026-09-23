import argparse
import time

from multithreader import create_app
from multithreader.publishing import refresh_due_accounts, run_once
from multithreader.control import draining, start_heartbeat


def main():
    parser = argparse.ArgumentParser(description='MultiThreader background publisher')
    parser.add_argument('--once', action='store_true', help='Process one queued item then exit')
    args = parser.parse_args()
    app = create_app()
    print(f'Publisher [{app.config["MODE"]}] ready', flush=True)
    last_refresh = 0
    stop, thread = start_heartbeat(app)
    try:
        while True:
            with app.app_context():
                if draining():
                    break
                if time.time() - last_refresh > 3600:
                    refresh_due_accounts()
                    last_refresh = time.time()
                processed = run_once()
            if args.once:
                break
            if not processed:
                stop.wait(2)
    finally:
        stop.set()
        thread.join(timeout=5)


if __name__ == '__main__':
    main()
