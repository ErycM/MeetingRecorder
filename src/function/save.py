import sys
import os
import asyncio
import tkinter as tk
from tkinter import filedialog
import time
import aiofiles
from difflib import SequenceMatcher
import re
from function.transformation import word_to_number

file_handle=None
saved_captions: list[tuple[float, str]] = [] # time, caption
save_dir = ""

def normalize_sentence(s: str) -> str:
    s = s.strip()
    # space
    s = re.sub(r'\s+', ' ', s)
    # lower letter
    s = s.lower()
    # "twenty twenty six" -> "2026"
    s = word_to_number(s)
    # symbol
    s = re.sub(r'\s+([.,!?])', r'\1', s)
    return s

def similarity_ratio(s1: str, s2: str) -> float:
    """calculate the similarity"""
    norm1 = normalize_sentence(s1)
    norm2 = normalize_sentence(s2)
    return SequenceMatcher(None, norm1, norm2).ratio()

def choose_save_dir():
    global save_dir
    
    timestamp = time.strftime("%Y-%m-%d_%H-%M-%S", time.localtime())  

    if not save_dir:
        root = tk.Tk()
        root.withdraw()  
        save_dir = filedialog.askdirectory(
            title="choose direction",
            initialdir=os.path.expanduser("~")  
        )
        root.destroy()  

        if not save_dir:
            save_dir = os.path.expanduser("~/Documents/captions")
            os.makedirs(save_dir, exist_ok=True)
    
    filename = os.path.join(save_dir, f"{timestamp}_captions.txt")
    
    return filename

async def save_replace_txt(filename,old_caption: tuple[float, str], new_caption: tuple[float, str]):
    ''' Replace old caption with new caption '''
    t_old, cap_old = old_caption
    _, cap_new = new_caption
    t_formatted = time.strftime("%H:%M:%S", time.localtime(t_old))
    
    # read file and replace line
    async with aiofiles.open(filename, "r", encoding="utf-8") as f:
        lines = await f.readlines()
    
    for idx, line in enumerate(lines):
        if t_formatted in line and cap_old in line:
            lines[idx] = f"[{t_formatted}] {cap_new}\n"
            print(f"[REPLACE] Replaced:\n  OLD: {cap_old}\n  NEW: {cap_new}")
            break
    
    # write back to file
    async with aiofiles.open(filename, "w", encoding="utf-8") as f:
        await f.writelines(lines)

async def save_txt(filename,new_caption: tuple[float, str]):
    ''' Add new caption '''

    global file_handle
    if file_handle is None:
        file_handle = await aiofiles.open(filename, "a+", encoding="utf-8")
    
    t, cap = new_caption
    t_formatted = time.strftime("%H:%M:%S", time.localtime(t))
    
    # write file
    async with aiofiles.open(filename, "a", encoding="utf-8") as f:
        await f.write(f"[{t_formatted}] {cap}\n")

async def close_file():
    global file_handle
    if file_handle is not None:
        await file_handle.close()
        file_handle = None
