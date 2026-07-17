---
description: Start the NanZi API Data Platform application service correctly using venv
---

1. Kill any existing uvicorn processes
   ```bash
   pkill -f "uvicorn app.main:app" || true
   ```
2. Activate virtual environment and start the service in background
   ```bash
   source venv/bin/activate && nohup uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload > app.log 2>&1 &
   ```
3. Verify startup by checking logs
   ```bash
   tail -n 20 app.log
   ```
