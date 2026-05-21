#!/usr/bin/env python3
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

db_path = os.path.join(os.path.dirname(__file__), '../backend/agora_marketplace.db')
os.remove(db_path) if os.path.exists(db_path) else None
from backend.database import init_db
init_db()
print("DB init OK")
