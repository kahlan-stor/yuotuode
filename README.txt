KM WhatsApp Manager - Railway
==============================

الملفات كلها في مجلد واحد:
- main.py
- requirements.txt
- Procfile
- README.txt

Railway:
1) ارفع الملفات إلى GitHub.
2) اربط المستودع بـ Railway.
3) Railway سيستخدم Procfile تلقائيًا.
4) افتح رابط الخدمة.

تشغيل محلي:
pip install -r requirements.txt
python main.py

مهم:
- هذه النسخة تستخدم PORT الذي توفره Railway.
- واجهة Flask لا تعتمد على نجاح محرك WhatsApp لكي تبدأ.
- الربط مع WhatsApp هنا غير رسمي عبر WhatsApp Web/WAeys.
- قد يتغير أو يتوقف وقد يؤدي إلى تقييد الحساب.
- الجلسات والبيانات التي تحفظها المكتبات قد تحتاج Volume دائم في Railway
  إذا أردت بقاء تسجيل الدخول بعد إعادة تشغيل/إعادة نشر الخدمة.
