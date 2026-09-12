# -*- coding: utf-8 -*-
"""
KM WhatsApp Manager V3 - ملف واحد
========================================
ربط غير رسمي عبر WhatsApp Web باستخدام WAeys + لوحة Flask.

المتطلبات:
    pip install flask qrcode pillow waeys

يتطلب Python 3.10+.
تشغيل:
    python km_whatsapp_manager.py

ثم افتح:
    http://127.0.0.1:5000

مهم:
- هذا عميل غير رسمي وليس تابعًا لـ WhatsApp/Meta.
- قد يؤدي استخدام عميل غير رسمي إلى تقييد الحساب.
- الجلسة تُحفظ بواسطة WAeys بعد تسجيل الدخول.
- كل واجهة المشروع موجودة في هذا الملف؛ قد تنشئ المكتبات ملفات جلسة محلية
  عند التشغيل لحفظ تسجيل الدخول، وهذا ضروري لكي لا تعيد مسح QR كل مرة.
"""

import asyncio
import base64
import io
import json
import os
import sqlite3
import threading
import time
import traceback
import urllib.request
import re
from datetime import datetime

from flask import Flask, jsonify, request, render_template_string

# ---------- Optional imports with friendly error ----------
try:
    import qrcode
except Exception:
    qrcode = None

try:
    from WAeys.Defaults.index import default_connection_config
    from WAeys.Utils.auth_utils import init_auth_creds
    from WAeys.Utils.browser_utils import Browsers
    from WAeys.Socket.socket import make_socket
    WAEYS_OK = True
    WAEYS_ERROR = ""
except Exception as e:
    WAEYS_OK = False
    WAEYS_ERROR = str(e)

SESSION_DIR = os.environ.get("WA_SESSION_DIR", os.path.join(os.getcwd(), "wa_session"))
CREDS_FILE = os.path.join(SESSION_DIR, "creds.json")
KEYS_FILE = os.path.join(SESSION_DIR, "keys.json")

def _encode_auth(v):
    if isinstance(v, bytes): return {"__bytes__": base64.b64encode(v).decode("ascii")}
    if isinstance(v, str): return {"__str__": v}
    if isinstance(v, dict): return {k: _encode_auth(x) for k, x in v.items()}
    if isinstance(v, list): return [_encode_auth(x) for x in v]
    return v

def _decode_auth(v):
    if isinstance(v, dict):
        if "__bytes__" in v: return base64.b64decode(v["__bytes__"])
        if "__str__" in v: return v["__str__"]
        return {k: _decode_auth(x) for k, x in v.items()}
    if isinstance(v, list): return [_decode_auth(x) for x in v]
    return v

def save_creds(creds):
    os.makedirs(SESSION_DIR, exist_ok=True)
    tmp = CREDS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_encode_auth(creds), f, ensure_ascii=False, indent=2, default=str)
    os.replace(tmp, CREDS_FILE)

def load_creds():
    if not os.path.exists(CREDS_FILE): return None
    try:
        with open(CREDS_FILE, "r", encoding="utf-8") as f: return _decode_auth(json.load(f))
    except Exception: return None

def make_file_key_store():
    async def get(type_, ids):
        all_keys = {}
        if os.path.exists(KEYS_FILE):
            try:
                with open(KEYS_FILE, "r", encoding="utf-8") as f: all_keys = _decode_auth(json.load(f))
            except Exception: all_keys = {}
        bucket = all_keys.get(type_, {}) if isinstance(all_keys, dict) else {}
        return {i: bucket.get(i) for i in ids if bucket.get(i) is not None}
    async def set_keys(data):
        existing = {}
        if os.path.exists(KEYS_FILE):
            try:
                with open(KEYS_FILE, "r", encoding="utf-8") as f: existing = _decode_auth(json.load(f))
            except Exception: existing = {}
        for type_, entries in data.items():
            existing.setdefault(type_, {})
            for id_, value in entries.items():
                if value is None: existing[type_].pop(id_, None)
                else: existing[type_][id_] = value
        os.makedirs(SESSION_DIR, exist_ok=True)
        tmp = KEYS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f: json.dump(_encode_auth(existing), f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp, KEYS_FILE)
    async def clear():
        if os.path.exists(KEYS_FILE): os.remove(KEYS_FILE)
    return {"get": get, "set": set_keys, "clear": clear}

APP_NAME = "KM WhatsApp Manager"
DB_FILE = "km_whatsapp.db"

app = Flask(__name__)
db_lock = threading.Lock()

wa = {
    "socket": None,
    "status": "غير متصل",
    "qr": None,
    "last_error": "",
    "started": False,
    "wa_version": None,
    "version_error": "",
}

def db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db_lock:
        c = db()
        c.execute("""CREATE TABLE IF NOT EXISTS replies(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keywords TEXT NOT NULL,
            response TEXT NOT NULL,
            enabled INTEGER DEFAULT 1,
            created_at TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS chats(
            chat_id TEXT PRIMARY KEY,
            name TEXT,
            last_text TEXT,
            updated_at TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            chat_id TEXT,
            direction TEXT,
            text TEXT,
            created_at TEXT
        )""")
        c.commit()
        c.close()

def save_chat(chat_id, name, text):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with db_lock:
        c = db()
        c.execute("""INSERT INTO chats(chat_id,name,last_text,updated_at)
                     VALUES(?,?,?,?)
                     ON CONFLICT(chat_id) DO UPDATE SET
                     name=excluded.name,last_text=excluded.last_text,
                     updated_at=excluded.updated_at""",
                  (chat_id, name or chat_id, text or "", now))
        c.commit()
        c.close()

def save_message(chat_id, direction, text):
    with db_lock:
        c = db()
        c.execute("INSERT INTO messages(chat_id,direction,text,created_at) VALUES(?,?,?,?)",
                  (chat_id, direction, text or "",
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        c.commit()
        c.close()

def normalize(s):
    s = str(s or "").strip().casefold()
    # توحيد بعض أشكال العربية حتى تعمل المطابقة بصورة أفضل.
    s = s.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    s = s.replace("ى", "ي").replace("ة", "ه")
    return " ".join(s.split())

def find_auto_reply(text):
    t = normalize(text)
    if not t:
        return None

    with db_lock:
        c = db()
        rows = c.execute(
            "SELECT * FROM replies WHERE enabled=1 ORDER BY id DESC"
        ).fetchall()
        c.close()

    for row in rows:
        for raw_key in str(row["keywords"] or "").split(","):
            key = normalize(raw_key)
            if not key:
                continue
            # يدعم العبارة داخل الرسالة: "وينك" -> "وينك يا رجل"
            if key in t:
                return row["response"]
    return None

def qr_to_data_uri(qr_text):
    if not qr_text:
        return None
    if qrcode is None:
        return None
    try:
        img = qrcode.make(qr_text)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return None

async def send_text(chat_id, text):
    sock = wa.get("socket")
    if not sock:
        return False, "واتساب غير متصل"
    try:
        await sock["sendMessage"](chat_id, {"text": text})
        return True, ""
    except Exception as e:
        return False, str(e)

def send_text_sync(chat_id, text):
    try:
        return asyncio.run(send_text(chat_id, text))
    except Exception as e:
        return False, str(e)

def fetch_latest_wa_web_version():
    """
    Fetch WhatsApp's current client_revision from web.whatsapp.com/sw.js.
    Falls back to the current Baileys revision if WhatsApp cannot be reached.
    """
    fallback = [2, 3000, 1043857760]
    try:
        req = urllib.request.Request(
            "https://web.whatsapp.com/sw.js",
            headers={
                "sec-fetch-site": "none",
                "user-agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            },
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            data = response.read().decode("utf-8", errors="ignore")
        match = re.search(r'\\"?client_revision\\"?\s*:\s*(\d+)', data)
        if match:
            return [2, 3000, int(match.group(1))]
    except Exception as e:
        wa["version_error"] = str(e)
    return fallback

async def whatsapp_loop():
    if not WAEYS_OK:
        wa["last_error"] = "WAeys غير مثبت: " + WAEYS_ERROR
        return

    try:
        config = default_connection_config()
        creds = load_creds() or init_auth_creds()
        auth = {"creds": creds, "keys": make_file_key_store()}
        config["auth"] = auth
        # WhatsApp changes client_revision frequently. Do not rely only on
        # the pinned revision inside the Python port; resolve the live revision
        # from web.whatsapp.com/sw.js and fall back if unavailable.
        live_version = fetch_latest_wa_web_version()
        config["version"] = live_version
        wa["wa_version"] = ".".join(map(str, live_version))

        # Use a normal web-browser fingerprint. Avoid desktop/DARWIN/WIN32
        # sub-platforms because those have recently caused immediate 428 closes.
        try:
            config["browser"] = Browsers.ubuntu("Chrome")
        except Exception:
            pass

        config["keepAliveIntervalMs"] = 30000
        config["connectTimeoutMs"] = 60000
        config["defaultQueryTimeoutMs"] = 60000
        config["syncFullHistory"] = False
        config["markOnlineOnConnect"] = False
        config["enableAutoSessionRecreation"] = True

        try:
            config["logger"].level = "info"
        except Exception:
            pass

        sock = make_socket(config)
        wa["socket"] = sock
        wa["started"] = True
        ev = sock["ev"]

        async def on_connection(update):
            try:
                if update.get("qr"):
                    wa["qr"] = qr_to_data_uri(update["qr"])
                    wa["status"] = "بانتظار مسح QR"

                connection = update.get("connection")
                if connection == "open":
                    wa["status"] = "متصل"
                    wa["qr"] = None
                    wa["last_error"] = ""
                elif connection in ("close", "closed"):
                    wa["status"] = "إعادة الاتصال..."
                    detail = update.get("lastDisconnect") or update.get("error")
                    if detail:
                        wa["last_error"] = "انقطع اتصال WhatsApp: " + str(detail)
                    elif update:
                        wa["last_error"] = "انقطع اتصال WhatsApp: " + repr(update)
            except Exception as e:
                wa["last_error"] = "connection.update: " + str(e)

        def _get(obj, key, default=None):
            if isinstance(obj, dict):
                return obj.get(key, default)
            try:
                return getattr(obj, key)
            except Exception:
                return default

        def _as_dict(obj):
            if isinstance(obj, dict):
                return obj
            try:
                return vars(obj)
            except Exception:
                return {}

        async def on_message(update):
            """
            استقبال الرسائل الجديدة ثم مطابقة قواعد الرد التلقائي.
            يدعم أكثر من شكل للرسالة حتى لا يتوقف الرد بسبب اختلاف
            تمثيل protobuf بين إصدارات WAeys.
            """
            try:
                event = _as_dict(update)
                items = event.get("messages") or []
                if isinstance(items, dict):
                    items = [items]

                for raw_msg in items:
                    msg = _as_dict(raw_msg)
                    key = _as_dict(_get(msg, "key", {}))

                    # لا ترد على الرسائل التي أرسلها الحساب نفسه.
                    if _get(key, "fromMe", False):
                        continue

                    chat_id = (
                        _get(key, "remoteJid")
                        or _get(msg, "chatId")
                        or _get(msg, "from")
                        or ""
                    )
                    if not chat_id:
                        continue

                    content = _as_dict(_get(msg, "message", {})) or {}

                    # أشكال النص الشائعة في WhatsApp Web.
                    ext = _as_dict(content.get("extendedTextMessage", {}))
                    text_in = (
                        content.get("conversation")
                        or ext.get("text")
                        or _as_dict(content.get("editedMessage", {})).get("message", {}).get("conversation")
                        or _get(msg, "body", "")
                        or _get(msg, "text", "")
                        or ""
                    )

                    # بعض الإصدارات قد تعطي النص مباشرة داخل message كـ string.
                    if not text_in and isinstance(_get(msg, "message", None), str):
                        text_in = _get(msg, "message", "")

                    text_in = str(text_in or "").strip()
                    if not text_in:
                        continue

                    name = (
                        _get(msg, "pushName")
                        or _get(msg, "name")
                        or chat_id
                    )

                    save_chat(chat_id, name, text_in)
                    save_message(chat_id, "in", text_in)

                    # ابحث عن أول قاعدة مطابقة.
                    reply = find_auto_reply(text_in)
                    if not reply:
                        continue

                    # أرسل حتى لو تغير نص الحالة لحظيًا؛ وجود socket هو
                    # الاختبار العملي لجاهزية الإرسال.
                    if not wa.get("socket"):
                        wa["last_error"] = "وصلت رسالة لكن جلسة WhatsApp غير جاهزة للإرسال."
                        continue

                    ok, err = await send_text(chat_id, reply)
                    if ok:
                        save_message(chat_id, "out", reply)
                        save_chat(chat_id, name, reply)
                        wa["last_error"] = ""
                    else:
                        wa["last_error"] = "فشل الرد التلقائي: " + str(err)

            except Exception as e:
                wa["last_error"] = "الرد التلقائي: " + str(e) + "\n" + traceback.format_exc()

        async def on_creds(update):
            try:
                auth["creds"].update(update or {})
                save_creds(auth["creds"])
            except Exception as e:
                wa["last_error"] = "حفظ الجلسة: " + str(e)

        ev.on("creds.update", lambda u: asyncio.ensure_future(on_creds(u)))
        ev.on("connection.update",
              lambda u: asyncio.ensure_future(on_connection(u)))

        # اسم الحدث المعتاد في بروتوكول Baileys/WAeys
        try:
            ev.on("messages.upsert",
                  lambda u: asyncio.ensure_future(on_message(u)))
        except Exception:
            pass

        # Keep the process alive. If WAeys destroys the event buffer after a
        # failed registration/connection, rebuild the socket after a short delay.
        while True:
            await asyncio.sleep(5)
            if wa.get("socket") is not sock:
                return
            if wa.get("status") in ("منقطع", "إعادة الاتصال..."):
                break

    except Exception as e:
        wa["status"] = "إعادة الاتصال..."
        wa["last_error"] = str(e) + "\n" + traceback.format_exc()

def start_whatsapp():
    if wa["started"]:
        return

    def worker():
        # A failed QR registration must not kill the Gunicorn worker.
        while True:
            try:
                asyncio.run(whatsapp_loop())
            except Exception as e:
                wa["last_error"] = "محرك WhatsApp: " + str(e) + "\n" + traceback.format_exc()
            wa["socket"] = None
            wa["qr"] = None
            wa["status"] = "إعادة الاتصال..."
            time.sleep(5)

    threading.Thread(target=worker, daemon=True, name="waeys-thread").start()

# ---------------- UI ----------------

HTML = r"""
<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{ title }}</title>
<style>
*{box-sizing:border-box}
body{margin:0;background:#08131a;color:#eaf4f5;font-family:system-ui,-apple-system,"Segoe UI",Tahoma,Arial}
.app{max-width:1100px;margin:auto;padding:18px}
header{background:linear-gradient(135deg,#0c242b,#102e35);border:1px solid #1c444b;border-radius:24px;padding:22px;display:flex;justify-content:space-between;gap:15px;align-items:center;box-shadow:0 15px 45px #0005}
h1{margin:0;font-size:23px}
.sub{color:#94b5ba;margin-top:6px;font-size:13px}
.badge{padding:8px 13px;border-radius:30px;background:#142e33;color:#b9dadd;border:1px solid #245057}
nav{display:flex;gap:8px;margin:16px 0;overflow:auto}
nav button{border:0;border-radius:14px;padding:12px 16px;background:#10262d;color:#c9e1e4;white-space:nowrap;cursor:pointer}
nav button.active{background:#16a37b;color:#fff}
.panel{display:none;background:#0c1d24;border:1px solid #1a3940;border-radius:22px;padding:18px;box-shadow:0 10px 35px #0003}
.panel.active{display:block}
.card{background:#10262d;border:1px solid #1e4249;border-radius:18px;padding:16px;margin-bottom:12px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
input,textarea{width:100%;background:#07161c;border:1px solid #285058;color:#fff;border-radius:13px;padding:12px;outline:none}
textarea{min-height:100px;resize:vertical}
button.primary{background:#16a37b;color:white;border:0;border-radius:13px;padding:12px 18px;cursor:pointer}
button.danger{background:#7e2730;color:white;border:0;border-radius:12px;padding:9px 13px}
button.small{background:#17353d;color:#cbe8eb;border:0;border-radius:10px;padding:8px 11px}
.qr{display:block;width:min(300px,80vw);margin:14px auto;background:#fff;border-radius:20px;padding:12px}
.center{text-align:center}
.muted{color:#89a9ae;font-size:13px}
.chat-layout{display:grid;grid-template-columns:290px 1fr;gap:12px;min-height:480px}
.chat-list{max-height:600px;overflow:auto}
.chat-item{padding:13px;border-bottom:1px solid #19383f;cursor:pointer}
.chat-item:hover,.chat-item.sel{background:#15323a}
.messages{height:390px;overflow:auto;background:#07161c;border-radius:16px;padding:15px}
.msg{max-width:80%;padding:10px 12px;margin:7px 0;border-radius:15px;white-space:pre-wrap}
.msg.in{background:#17343b;margin-left:auto}
.msg.out{background:#126a56;margin-right:auto}
.row{display:flex;gap:8px;margin-top:10px}
.row input{flex:1}
.reply{display:flex;gap:10px;align-items:flex-start}
.reply .body{flex:1}
@media(max-width:700px){.chat-layout{grid-template-columns:1fr}.chat-list{max-height:220px}.messages{height:340px}header{align-items:flex-start;flex-direction:column}}
</style>
</head>
<body>
<div class="app">
<header>
  <div>
    <h1>💬 KM WhatsApp Manager</h1>
    <div class="sub">إدارة المحادثات والردود التلقائية — إصدار V4</div>
  </div>
  <div id="status" class="badge">جارٍ التحضير...</div>
</header>

<nav>
 <button class="active" onclick="tab('connect',this)">🔗 الربط</button>
 <button onclick="tab('chats',this)">💬 الدردشات</button>
 <button onclick="tab('replies',this)">🤖 الردود التلقائية</button>
 <button onclick="tab('about',this)">ℹ️ حول</button>
</nav>

<section id="connect" class="panel active">
 <div class="card center">
   <h2>ربط WhatsApp</h2>
   <p class="muted">افتح WhatsApp ← الأجهزة المرتبطة ← ربط جهاز، ثم امسح QR الظاهر هنا.</p>
   <div id="qrbox"><div class="muted">جاري تشغيل محرك الاتصال...</div></div>
   <div id="err" class="muted"></div>
 </div>
</section>

<section id="chats" class="panel">
 <div class="chat-layout">
   <div class="card chat-list" id="chatList">لا توجد محادثات بعد.</div>
   <div class="card">
      <div id="chatTitle"><b>اختر محادثة</b></div>
      <div class="messages" id="messages"><div class="muted">ستظهر الرسائل هنا بعد استقبالها.</div></div>
      <div class="row">
        <input id="sendText" placeholder="اكتب رسالة...">
        <button class="primary" onclick="sendMsg()">إرسال</button>
      </div>
   </div>
 </div>
</section>

<section id="replies" class="panel">
 <div class="card">
   <h2>➕ إضافة رد تلقائي</h2>
   <p class="muted">اكتب عدة كلمات مفصولة بفواصل، مثل: وينك, فينك, أين أنت</p>
   <div class="grid">
     <input id="keywords" placeholder="الكلمات أو العبارات">
     <textarea id="response" placeholder="الرد الذي سيرسل تلقائيًا"></textarea>
   </div>
   <br>
   <button class="primary" onclick="addReply()">حفظ الرد</button>
 </div>
 <div id="replyList"></div>
</section>

<section id="about" class="panel">
 <div class="card">
   <h2>عن الأداة</h2>
   <p>لوحة محلية لإدارة حساب WhatsApp عبر عميل WhatsApp Web غير رسمي.</p>
   <p class="muted">المطور: كهلان زيد الاشول</p>
   <p class="muted">تنبيه: الربط غير الرسمي قد يخالف شروط WhatsApp وقد يؤدي إلى تقييد الحساب.</p>
 </div>
</section>
</div>

<script>
let selectedChat=null;

function tab(id,btn){
 document.querySelectorAll('.panel').forEach(x=>x.classList.remove('active'));
 document.getElementById(id).classList.add('active');
 document.querySelectorAll('nav button').forEach(x=>x.classList.remove('active'));
 btn.classList.add('active');
 if(id==='replies') loadReplies();
 if(id==='chats') loadChats();
}

async function status(){
 try{
   const x=await fetch('/api/status').then(r=>r.json());
   document.getElementById('status').textContent='● '+x.status;
   document.getElementById('err').textContent=x.error||'';
   const box=document.getElementById('qrbox');
   if(x.qr){
      box.innerHTML='<img class="qr" src="'+x.qr+'"><div class="muted">امسح الرمز من WhatsApp</div>';
   }else if(x.status==='متصل'){
      box.innerHTML='<div style="font-size:70px">✅</div><h3>تم الاتصال بنجاح</h3><div class="muted">لن تحتاج لمسح QR مرة أخرى إذا بقيت الجلسة محفوظة.</div>';
   }else{
      box.innerHTML='<div class="muted">بانتظار QR...</div>';
   }
 }catch(e){}
}
setInterval(status,1500); status();

async function loadReplies(){
 const rows=await fetch('/api/replies').then(r=>r.json());
 const box=document.getElementById('replyList');
 box.innerHTML=rows.length?rows.map(x=>`
 <div class="card reply">
   <div class="body"><b>🔑 ${esc(x.keywords)}</b><br><span class="muted">↳ ${esc(x.response)}</span></div>
   <button class="danger" onclick="delReply(${x.id})">حذف</button>
 </div>`).join(''):'<div class="card muted">لا توجد ردود. أضف أول رد مثل: وينك → موجود.</div>';
}
async function addReply(){
 const keywords=document.getElementById('keywords').value.trim();
 const response=document.getElementById('response').value.trim();
 if(!keywords||!response)return alert('أكمل الكلمة والرد');
 await fetch('/api/replies',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({keywords,response})});
 document.getElementById('keywords').value='';document.getElementById('response').value='';
 loadReplies();
}
async function delReply(id){
 if(confirm('حذف هذا الرد؟')){
  await fetch('/api/replies/'+id,{method:'DELETE'});loadReplies();
 }
}
async function loadChats(){
 const rows=await fetch('/api/chats').then(r=>r.json());
 document.getElementById('chatList').innerHTML=rows.length?rows.map(x=>`
 <div class="chat-item ${selectedChat===x.chat_id?'sel':''}" onclick="openChat('${encodeURIComponent(x.chat_id)}','${esc(x.name||x.chat_id)}')">
   <b>${esc(x.name||x.chat_id)}</b><br><span class="muted">${esc(x.last_text||'')}</span>
 </div>`).join(''):'لا توجد محادثات بعد.';
}
async function openChat(encoded,name){
 selectedChat=decodeURIComponent(encoded);
 document.getElementById('chatTitle').innerHTML='<b>'+name+'</b>';
 const rows=await fetch('/api/messages?chat_id='+encodeURIComponent(selectedChat)).then(r=>r.json());
 document.getElementById('messages').innerHTML=rows.length?rows.map(x=>`<div class="msg ${x.direction==='in'?'in':'out'}">${esc(x.text)}<div class="muted">${x.created_at}</div></div>`).join(''):'<div class="muted">لا رسائل محفوظة.</div>';
 loadChats();
}
async function sendMsg(){
 if(!selectedChat)return alert('اختر محادثة أولاً');
 const text=document.getElementById('sendText').value.trim();if(!text)return;
 const r=await fetch('/api/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({chat_id:selectedChat,text})}).then(r=>r.json());
 if(!r.ok)return alert(r.error||'تعذر الإرسال');
 document.getElementById('sendText').value='';
 openChat(encodeURIComponent(selectedChat),'المحادثة');
}
function esc(s){return String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));}
setInterval(()=>{if(document.getElementById('chats').classList.contains('active'))loadChats()},3000);
</script>
</body>
</html>
"""

@app.get("/")
def home():
    return render_template_string(HTML, title=APP_NAME)

@app.get("/api/status")
def api_status():
    return jsonify({
        "status": wa["status"],
        "qr": wa["qr"],
        "error": wa["last_error"],
        "waeys": WAEYS_OK,
        "wa_version": wa.get("wa_version"),
        "version_error": wa.get("version_error", "")
    })

@app.get("/api/replies/test")
def test_reply():
    incoming = request.args.get("text", "")
    reply = find_auto_reply(incoming)
    return jsonify({"input": incoming, "reply": reply, "matched": bool(reply)})

@app.get("/api/replies")
def get_replies():
    with db_lock:
        c=db()
        rows=[dict(x) for x in c.execute("SELECT * FROM replies ORDER BY id DESC").fetchall()]
        c.close()
    return jsonify(rows)

@app.post("/api/replies")
def post_reply():
    data=request.get_json(force=True)
    keywords=(data.get("keywords") or "").strip()
    response=(data.get("response") or "").strip()
    if not keywords or not response:
        return jsonify({"ok":False,"error":"البيانات ناقصة"}),400
    with db_lock:
        c=db()
        c.execute("INSERT INTO replies(keywords,response,created_at) VALUES(?,?,?)",
                  (keywords,response,datetime.now().isoformat()))
        c.commit();c.close()
    return jsonify({"ok":True})

@app.delete("/api/replies/<int:rid>")
def delete_reply(rid):
    with db_lock:
        c=db();c.execute("DELETE FROM replies WHERE id=?",(rid,));c.commit();c.close()
    return jsonify({"ok":True})

@app.get("/api/chats")
def get_chats():
    with db_lock:
        c=db()
        rows=[dict(x) for x in c.execute(
            "SELECT * FROM chats ORDER BY updated_at DESC").fetchall()]
        c.close()
    return jsonify(rows)

@app.get("/api/messages")
def get_messages():
    chat_id=request.args.get("chat_id","")
    with db_lock:
        c=db()
        rows=[dict(x) for x in c.execute(
            "SELECT * FROM messages WHERE chat_id=? ORDER BY id ASC",(chat_id,)).fetchall()]
        c.close()
    return jsonify(rows)

@app.post("/api/send")
def api_send():
    data=request.get_json(force=True)
    chat_id=(data.get("chat_id") or "").strip()
    text=(data.get("text") or "").strip()
    if not chat_id or not text:
        return jsonify({"ok":False,"error":"المحادثة أو الرسالة ناقصة"}),400
    ok,err=send_text_sync(chat_id,text)
    if ok:
        save_message(chat_id,"out",text)
        save_chat(chat_id,chat_id,text)
        return jsonify({"ok":True})
    return jsonify({"ok":False,"error":err}),500

# Initialize before Gunicorn imports the application.
init_db()
try:
    start_whatsapp()
except Exception as e:
    wa["last_error"] = "تعذر تشغيل محرك WhatsApp: " + str(e)

if __name__ == "__main__":
    print("\n" + "="*58)
    print(APP_NAME)
    print("افتح: http://127.0.0.1:5000")
    print("المتطلبات: pip install -r requirements.txt")
    print("="*58 + "\n")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False, threaded=True)
