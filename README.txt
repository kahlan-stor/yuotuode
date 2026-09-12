KM WhatsApp Manager - Railway FIXED v2

هذا الإصدار يعالج مشكلة ظهور QR ثم فشل الاتصال مباشرة بعد مسحه.

أهم التعديلات:
- ضبط browser إلى Browsers.macOS("Safari") مثل مثال WAeys الرسمي.
- رفع مهلة الاتصال إلى 60 ثانية.
- keepAlive إلى 30 ثانية.
- تعطيل مزامنة السجل الكامل عند الربط الأول لتقليل ضغط التسجيل.
- إضافة إعادة اتصال تلقائية إذا انهارت جلسة WebSocket أو تم تدمير Event Buffer.
- إظهار تفاصيل lastDisconnect في لوحة الحالة.
- الحفاظ على تخزين creds/keys محليًا.

الملفات كلها داخل مجلد واحد:
main.py
requirements.txt
runtime.txt
Procfile
README.txt

Railway:
1) ارفع الملفات إلى GitHub.
2) اعمل Deploy جديد في Railway.
3) افتح رابط الموقع وانتظر QR.
4) WhatsApp Business > الأجهزة المرتبطة > ربط جهاز > امسح QR.
5) بعد نجاح الربط ستظهر "متصل".

مهم: هذا عميل غير رسمي لـ WhatsApp Web وقد يخالف شروط WhatsApp أو يؤدي إلى تقييد الحساب. استخدمه على حسابك وعلى مسؤوليتك.

يفضل إضافة Railway Volume لمسار الجلسة إذا أردت بقاء تسجيل الدخول بعد إعادة تشغيل الخدمة.
