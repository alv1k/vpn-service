#!/bin/bash
# Alex QA Sentinel Runner
set -uo pipefail

cd /home/alvik/vpn-service
venv/bin/python3 qa_agent/runner.py "$@"
