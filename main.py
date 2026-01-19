# main.py

import importlib
import os
from dotenv import load_dotenv
from config import create_bot_client

# load environment variables from .env
load_dotenv()

def load_handlers():
    handlers_dir = "handlers"
    for filename in os.listdir(handlers_dir):
        if filename.endswith(".py"):
            module_name = f"{handlers_dir}.{filename[:-3]}"
            try:
                importlib.import_module(module_name)
                print(f"✅ Loaded: {filename}")
            except Exception as e:
                print(f"❌ Failed to load {filename}: {e}")

if __name__ == "__main__":
    load_handlers()
    print("📦 Handlers loaded successfully!")
    print("🚀 Bot is running...")

    app = create_bot_client()
    app.run()
