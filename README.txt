KM WhatsApp Business Manager - Railway FIXED

الملفات:
- main.py
- requirements.txt
- Procfile
- runtime.txt
- README.txt

الإصلاحات:
1) تم إصلاح SyntaxError الناتج عن وجود \n كنص داخل main.py.
2) WAeys أصبح يُحمّل بشكل كسول حتى لا يمنع Gunicorn من تشغيل واجهة الويب إذا فشل محرك WhatsApp.
3) تشغيل محرك WhatsApp أصبح يتم أيضًا عند استيراد main:app بواسطة Gunicorn، وليس فقط عند تشغيل python main.py.
4) تم تثبيت Python 3.11 عبر runtime.txt لأن Railway الحالي يستخدم Python 3.13 افتراضيًا، ويمكن تغيير الإصدار عبر إعدادات Railpack عند الحاجة.

Railway:
- ارفع محتويات هذا المجلد إلى المستودع.
- لا تضف TELEGRAM_BOT_TOKEN؛ هذا المشروع لا يحتاج توكن Telegram.
- Start command في Procfile هو Gunicorn.
- بعد نجاح النشر افتح الرابط العام ثم انتظر ظهور QR.

تنبيه:
هذا ربط غير رسمي بواتساب، وقد يخضع حسابك لقيود من WhatsApp. استخدمه لحسابك وتجنب الرسائل الجماعية أو الإرسال المزعج.
