import uiautomation as auto  
import time
import subprocess

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
    
    subprocess.run(["python", "src/main.py"])   