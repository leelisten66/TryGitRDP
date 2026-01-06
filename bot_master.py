import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
import os
import subprocess
import time
import threading
import psutil
import pyautogui
import sys
import requests 
import platform

try: sys.stdout.reconfigure(encoding='utf-8')
except: pass

# --- CONFIG ---
TOKEN = os.getenv('TG_TOKEN')
CHAT_ID = os.getenv('TG_CHATID')
WORKER_URL = os.getenv('WORKER_URL')
USER_LANG = os.getenv('USER_LANG', 'en').lower()
SYSTEM_OS = platform.system()
RUN_ID = os.getenv('GITHUB_RUN_ID') # Ambil ID Workflow dari GitHub Actions

bot = telebot.TeleBot(TOKEN)

TEXTS = {
    'en': {
        'start': f"👋 **{SYSTEM_OS} RDP Controller**\n\nPaste **CRD Command** now.\n(Linux/Windows)",
        'cmd_received': "✅ Command OK.\nInput **PIN (6 Digits)**:",
        'pin_ok': "✅ PIN Saved.\n👉 **Select Duration (Hours):**",
        'starting': "🚀 **Starting RDP...**\nWait for screenshot...",
        'active': f"🖥️ **RDP ACTIVE!**\nLogin now.",
        'timeout': "🛑 Duration Limit Reached.",
        'max_limit': "⚠️ **Max Limit!** Cannot exceed 6 Hours."
    },
    'id': {
        'start': f"👋 **Controller RDP {SYSTEM_OS}**\n\nPaste **Command CRD** sekarang.\n(Linux/Windows)",
        'cmd_received': "✅ Command Diterima.\nMasukkan **PIN (6 Angka)**:",
        'pin_ok': "✅ PIN Disimpan.\n👉 **Pilih Durasi (Jam):**",
        'starting': "🚀 **Menyalakan RDP...**\nTunggu screenshot...",
        'active': f"🖥️ **RDP AKTIF!**\nLogin sekarang.",
        'timeout': "🛑 Batas Waktu Habis.",
        'max_limit': "⚠️ **Batas Max!** Tidak bisa lebih dari 6 Jam."
    }
}
def t(key): return TEXTS.get(USER_LANG, TEXTS['en']).get(key, key)

state = {"crd_cmd": None, "pin": None, "duration": 0, "start_time": None, "active": True}

# --- REGISTER SESSION KE CLOUDFLARE ---
def register_session():
    try:
        if RUN_ID and WORKER_URL:
            payload = {"chat_id": CHAT_ID, "run_id": RUN_ID, "secret": TOKEN}
            requests.post(f"{WORKER_URL}/register-session", json=payload, timeout=10)
            print(f"✅ Session Registered: RunID {RUN_ID}")
    except Exception as e:
        print(f"⚠️ Reg Error: {e}")

# --- POLLING ---
def poll_cloudflare():
    register_session() # Lapor diri dulu saat nyala
    
    while state["active"]:
        try:
            headers = {"X-Bot-Secret": TOKEN}
            resp = requests.get(f"{WORKER_URL}/get-updates?chat_id={CHAT_ID}", headers=headers, timeout=10)
            data = resp.json()
            if data and "payload" in data:
                ctype = data.get("command_type")
                payload = data.get("payload")
                
                # Masking Log
                log_p = payload
                if "--code=" in payload or (payload.isdigit() and len(payload)>=6): log_p = "***"
                print(f"📩 Recv: {ctype} -> {log_p}")
                
                if ctype == "text": process_text(payload)
                elif ctype == "callback": process_callback(payload)
        except: pass
        time.sleep(2)

def process_text(text):
    text = text.strip()
    if state["crd_cmd"] is None:
        if "--code=" in text:
            state["crd_cmd"] = text
            bot.send_message(CHAT_ID, t('cmd_received'))
        else:
            bot.send_message(CHAT_ID, "❌ Format CRD Salah.")
    elif state["pin"] is None:
        if text.isdigit() and len(text) >= 6:
            state["pin"] = text
            # MENU DURASI BARU (1-6 JAM)
            mk = InlineKeyboardMarkup(row_width=3)
            mk.add(
                InlineKeyboardButton("1 Jam", callback_data="time_60"),
                InlineKeyboardButton("2 Jam", callback_data="time_120"),
                InlineKeyboardButton("3 Jam", callback_data="time_180"),
                InlineKeyboardButton("4 Jam", callback_data="time_240"),
                InlineKeyboardButton("5 Jam", callback_data="time_300"),
                InlineKeyboardButton("6 Jam", callback_data="time_360")
            )
            bot.send_message(CHAT_ID, t('pin_ok'), reply_markup=mk)
        else:
            bot.send_message(CHAT_ID, "❌ PIN harus 6 angka.")

def process_callback(data):
    if data.startswith("time_"):
        mins = int(data.split("_")[1])
        state["duration"] = mins
        state["start_time"] = time.time()
        bot.send_message(CHAT_ID, t('starting'))
        threading.Thread(target=run_rdp_process).start()
        
    elif data == "extend":
        # Logic Max 6 Jam (360 Menit)
        if state["duration"] + 30 > 360:
            bot.send_message(CHAT_ID, t('max_limit'))
        else:
            state["duration"] += 30
            bot.send_message(CHAT_ID, "✅ +30 Mins")
            
    elif data == "screen": send_screenshot()
    elif data == "kill": 
        bot.send_message(CHAT_ID, "💀 Shutdown...")
        state["active"] = False
        if SYSTEM_OS == "Windows": os.system("shutdown /s /t 0")
        else: os.system("sudo shutdown now")

def run_rdp_process():
    try:
        cmd = state["crd_cmd"]
        pin = state["pin"]
        if SYSTEM_OS == "Windows":
            if 'remoting_start_host.exe"' in cmd:
                final = cmd.replace('remoting_start_host.exe"', f'remoting_start_host.exe" --pin="{pin}"')
            else: final = f'{cmd} --pin="{pin}"'
            subprocess.Popen(["powershell", "-Command", final], shell=True)
        else:
            final = f'{cmd} --pin="{pin}"'
            subprocess.Popen(final, shell=True, executable='/bin/bash')

        time.sleep(10)
        bot.send_message(CHAT_ID, t('active'))
        send_screenshot() # AUTO SCREENSHOT
        monitor_loop()
    except Exception as e:
        bot.send_message(CHAT_ID, f"Error: {e}")

def monitor_loop():
    while state["active"]:
        elapsed = (time.time() - state["start_time"]) / 60
        if (state["duration"] - elapsed) <= 0:
            bot.send_message(CHAT_ID, t('timeout'))
            if SYSTEM_OS == "Windows": os.system("shutdown /s /t 0")
            else: os.system("sudo shutdown now")
            break
        time.sleep(30)

def send_screenshot():
    try:
        f = "s.png"
        pyautogui.screenshot(f)
        with open(f, "rb") as p: bot.send_photo(CHAT_ID, p)
        os.remove(f)
    except: pass

if __name__ == "__main__":
    poll_cloudflare()
