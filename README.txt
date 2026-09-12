KM WhatsApp Manager - Railway FIXED
===================================

الملفات كلها في مجلد واحد:
- main.py
- requirements.txt
- runtime.txt
- Procfile
- README.txt

تم إصلاح مشكلة WAeys:
النسخة 0.1.3 لا تصدّر make_file_key_store من auth_utils، لذلك أصبح مخزن الجلسة داخل main.py نفسه وفق واجهة WAeys الموثقة.

Railway:
1) ارفع الملفات إلى GitHub.
2) اربط المستودع بخدمة Railway.
3) أعد Deploy.
4) افتح رابط الخدمة وانتظر ظهور QR.
5) من WhatsApp Business اختر الأجهزة المرتبطة ثم ربط جهاز وامسح QR.

ملاحظات:
- runtime.txt يحدد Python 3.11.
- الجلسة تحفظ داخل wa_session. على Railway يفضل استخدام Volume حتى لا تضيع جلسة WhatsApp عند إعادة التشغيل.
- هذا ربط غير رسمي عبر WhatsApp Web/WAeys، وقد يخالف شروط WhatsApp وقد يؤدي إلى تقييد الحساب.
- استخدمه لحسابك وبحجم رسائل طبيعي، وتجنب الرسائل الجماعية المزعجة.
