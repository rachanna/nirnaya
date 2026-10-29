"""Run the web demo: uv run python -m nirnaya.web [--port 8000]"""

import argparse

import uvicorn

from nirnaya.config.env import load_dotenv
from nirnaya.web.api import WEB_DIST, create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="nirnaya.web")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    load_dotenv()
    if not WEB_DIST.is_dir():
        print("The React app is not built yet: cd web && npm install && npm run build")
    print(f"Nirnaya web demo on http://localhost:{args.port}")
    uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
