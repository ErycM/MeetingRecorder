import uiautomation as auto  
import time
import sys
import os

if getattr(sys, 'frozen', False):
    base_path = sys._MEIPASS
else:
    base_path = os.path.dirname(os.path.abspath(__file__))
root_path = base_path
if root_path not in sys.path:
    sys.path.append(root_path)
src_path = os.path.join(root_path, 'src')
if src_path not in sys.path:
    sys.path.append(src_path)

import main

# Launch with “Win + Ctrl + L”
def launch_lc():
    try:
        print("Try to launch Windows Live Captions...")
        auto.SendKeys('{Ctrl}{Win}l')
        time.sleep(1)   

    except Exception as e:
        print(f"Error: {e}")
        
if __name__ == "__main__":
    launch_lc()
    
    print("Launch Main.py...")
    
    main.main()