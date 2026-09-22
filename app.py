import os

from waitress import serve

from multithreader import create_app

app = create_app()

if __name__ == '__main__':
    host = os.getenv('HOST', '127.0.0.1')
    port = int(os.getenv('PORT', '18473'))
    print(f'MultiThreader [{app.config["MODE"]}] http://{host}:{port}', flush=True)
    serve(app, host=host, port=port, threads=4)

