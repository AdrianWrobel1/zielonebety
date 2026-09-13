import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
if "SQLITE_PATH" not in os.environ and "DATABASE_URL" not in os.environ:
    os.environ["SQLITE_PATH"] = "zielonebety.db"
    os.environ["DATABASE_URL"] = "sqlite:///zielonebety.db"

if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

import uvicorn
from api.fastapi_app import app


if __name__ == "__main__":
    print("Starting Uvicorn Server on 127.0.0.1:8000...", flush=True)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    config = uvicorn.Config(app, host="127.0.0.1", port=8000, log_level="warning", loop="asyncio")
    server = uvicorn.Server(config)
    loop.run_until_complete(server.serve())
