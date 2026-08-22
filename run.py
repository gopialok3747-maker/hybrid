#!/usr/bin/env python3
import os
import sys
import subprocess
import time
import socket
from pathlib import Path

def is_node_running():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = sock.connect_ex(('127.0.0.1', 3000))
        sock.close()
        return result == 0
    except:
        return False

def start_node():
    print("🚀 Starting Node.js monitor...")
    node_file = Path(__file__).parent / "node" / "monitor.js"
    if node_file.exists():
        subprocess.Popen(
            ["node", str(node_file)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        time.sleep(2)
        return True
    return False

def main():
    if not is_node_running():
        start_node()
    sys.path.insert(0, str(Path(__file__).parent / "python"))
    import bot
    bot.main()

if __name__ == "__main__":
    main()
