#!/usr/bin/env python
"""Start the VERITRACE server."""
import sys
import os

# Add the project root to the Python path.
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

if __name__ == "__main__":
    import argparse
    import uvicorn
    from veritrace.logging_config import build_log_config

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8002)
    parser.add_argument("--log-file", default=os.getenv("VERITRACE_LOG_FILE", "logs/backend.log"))
    parser.add_argument("--log-level", choices=("debug", "info", "warning", "error"), default="info")
    args = parser.parse_args()

    print(f"Starting VERITRACE server at http://{args.host}:{args.port}")
    uvicorn.run("veritrace.app:app", host=args.host, port=args.port, reload=True, log_level=args.log_level,
                log_config=build_log_config(args.log_file, args.log_level))
