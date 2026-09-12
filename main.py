# -*- coding: utf-8 -*-
"""
KM WhatsApp Business Manager - ملف واحد
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
from datetime import datetime

from flask import Flask, jsonify, request, render_template_string

# ---------- Optional imports with friendly error ----------
try:
    import qrcode
except Exception:
    qrcode = None

# WAeys is loaded lazily so Gunicorn can boot the web UI even if the
# WhatsApp engine has a Python/package compatibility problem.
WAEYS_OK = False
WAEYS_ERROR = "لم يتم تحميل WAeys بعد"
default_connection_config = None
init_auth_creds = None
make_file_key_store = None
Browsers = None
make_socket = None

def load_waeys():
    global WAEYS_OK, WAEYS_ERROR
    global default_connection_config, init_auth_creds, make_file_key_store, Browsers, make_socket
    if WAEYS_OK:
        return True
    try:
        from WAeys.Defaults.index import default_connection_config as _default_connection_config
        from WAeys.Utils.auth_utils import init_auth_creds as _init_auth_creds, make_file_key_store as _make_file_key_store
        from WAeys.Utils.browser_utils import Browsers as _Browsers
        from WAeys.Socket.socket import make_socket as _make_socket
        default_connection_config = _default_connection_config
        init_auth_creds = _init_auth_creds
        make_file_key_store = _make_file_key_store
        Browsers = _Browsers
        make_socket = _make_socket
        WAEYS_OK = True
        WAEYS_ERROR = ""
        return True
    except Exception as e:
        WAEYS_OK = False
        WAEYS_ERROR = str(e)
        return False

APP_NAME = "KM WhatsApp Business Manager"
DB_FILE = "km_whatsapp.db"

app = Flask(__name__)
db_lock = threading.Lock()

wa = {
    "socket": None,
    "status": "غير متصل",
    "qr": None,
    "last_error": "",
    "started": False,
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
    return " ".join((s or "").strip().lower().split())

def find_auto_reply(text):
    t = normalize(text)
    with db_lock:
        c = db()
        rows = c.execute("SELECT * FROM replies WHERE enabled=1 ORDER BY id DESC").fetchall()
        c.close()
    for row in rows:
        # الكلمات مفصولة بفواصل
        for key in row["keywords"].split(","):
            key = normalize(key)
            if key and key in t:
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

async def whatsapp_loop():
    if not load_waeys():
        wa["last_error"] = "تعذر تحميل WAeys: " + WAEYS_ERROR
        wa["status"] = "خطأ في محرك WhatsApp"
        return

    try:
        config = default_connection_config()
        config["auth"] = {
            "creds": init_auth_creds(),
            "keys": make_file_key_store()
        }

        # متصفح افتراضي مناسب للاتصال
        try:
            config["browser"] = Browsers.macOS("Safari")
        except Exception:
            pass

        try:
            config["logger"].level = "warning"
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
                if update.get("connection") == "open":
                    wa["status"] = "متصل"
                    wa["qr"] = None
                    wa["last_error"] = ""
                elif update.get("connection") in ("close", "closed"):
                    wa["status"] = "منقطع"
            except Exception:
                pass

        async def on_message(update):
            """
            WAeys/Baileys-style message event.
            نحاول استخراج chat id والنص بطريقة مرنة لأن شكل event قد يختلف
            بين الإصدارات.
            """
            try:
                items = update.get("messages", []) if isinstance(update, dict) else []
                if isinstance(items, dict):
                    items = [items]

                for msg in items:
                    key = msg.get("key", {}) if isinstance(msg, dict) else {}
                    if key.get("fromMe"):
                        continue

                    chat_id = key.get("remoteJid") or msg.get("chatId") or msg.get("from")
                    if not chat_id:
                        continue

                    content = msg.get("message", {}) or {}
                    text = (
                        content.get("conversation")
                        or content.get("extendedTextMessage", {}).get("text")
                        or msg.get("body")
                        or msg.get("text")
                        or ""
                    )
                    if not text:
                        continue

                    name = (
                        msg.get("pushName")
                        or msg.get("name")
                        or chat_id
                    )

                    save_chat(chat_id, name, text)
                    save_message(chat_id, "in", text)

                    reply = find_auto_reply(text)
                    if reply and wa["status"] == "متصل":
                        ok, err = await send_text(chat_id, reply)
                        if ok:
                            save_message(chat_id, "out", reply)
                            save_chat(chat_id, name, reply)
                        else:
                            wa["last_error"] = err

            except Exception as e:
                wa["last_error"] = "رسالة: " + str(e)

        ev.on("connection.update",
              lambda u: asyncio.ensure_future(on_connection(u)))

        # اسم الحدث المعتاد في بروتوكول Baileys/WAeys
        try:
            ev.on("messages.upsert",
                  lambda u: asyncio.ensure_future(on_message(u)))
        except Exception:
            pass

        await asyncio.Event().wait()

    except Exception as e:
        wa["status"] = "خطأ"
        wa["last_error"] = str(e) + "\n" + traceback.format_exc()

def start_whatsapp():
    if wa["started"]:
        return
    threading.Thread(
        target=lambda: asyncio.run(whatsapp_loop()),
        daemon=True,
        name="waeys-thread"
    ).start()

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
    <div class="sub">إدارة المحادثات والردود التلقائية</div>
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
   <h2>ربط WhatsApp Business</h2>
   <p class="muted">افتح WhatsApp Business ← الأجهزة المرتبطة ← ربط جهاز، ثم امسح QR الظاهر هنا.</p>
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
   <p>لوحة محلية لإدارة حساب WhatsApp Business عبر عميل WhatsApp Web غير رسمي.</p>
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
      box.innerHTML='<img class="qr" src="'+x.qr+'"><div class="muted">امسح الرمز من WhatsApp Business</div>';
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
        "waeys": WAEYS_OK
    })

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

# Initialize the local database for both Gunicorn and direct Python runs.
init_db()

# Railway/Gunicorn imports `main:app`, so an __main__ block would never run.
# Start the WhatsApp worker during module loading while keeping the web UI alive
# even if WAeys itself fails to load.
try:
    start_whatsapp()
except Exception as e:
    wa["last_error"] = "تعذر تشغيل محرك WhatsApp: " + str(e)

if __name__ == "__main__":
    print("\n" + "="*58)
    print(APP_NAME)
    print("افتح: http://127.0.0.1:5000")
    print("المتطلبات: pip install flask qrcode pillow waeys")
    print("="*58 + "\n")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False, threaded=True)


# Railway/Gunicorn entry point:
# gunicorn main:app
