import logging
import os
import sys

os.environ["OPENROUTER_API_KEY"] = ""
logging.basicConfig(level=logging.INFO)

from instance_config import ADMIN_USER_ID
from jung_core import Config, HybridDatabaseManager, JungianEngine

Config.INTERNAL_MODEL = "claude-3-haiku-20240307"

db = HybridDatabaseManager()
engine = JungianEngine(db)

print("\n\n--- Admin Test With Append ---")
result_admin = engine.process_message(ADMIN_USER_ID, "O que voce achou dos meus sentimentos?")
sys.stdout.buffer.write(result_admin["response"].encode("utf-8"))

db.close()
