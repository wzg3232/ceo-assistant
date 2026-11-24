#!/bin/bash
# one-shot launcher: 清掉会话继承的 PYTHONPATH/VIRTUAL_ENV(hermes venv pydantic 冲突), 用 Python3.14 起 8002
unset PYTHONPATH PYTHONHOME VIRTUAL_ENV
cd /d/program/ceo-assistant/backend
exec /c/Users/admin/AppData/Local/Programs/Python/Python314/python.exe -m uvicorn app.main_v3:app --host 0.0.0.0 --port 8002
