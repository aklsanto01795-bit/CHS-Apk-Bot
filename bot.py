import os
import io
import re
import json
import zipfile
import shutil
import sqlite3
import tempfile
import subprocess
import threading
from flask import Flask, jsonify
from pathlib import Path

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton
)
from telegram.constants import ChatMemberStatus
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters
)

TOKEN = os.getenv("TOKEN", "").strip()
PORT = int(os.getenv("PORT", "10000"))
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))
CHANNELS = []  # Channel Join OFF
DB_PATH = os.getenv("DB_PATH", "bot.db")
WORK = Path("/app/work") if Path("/app").exists() else Path("work")
WORK.mkdir(exist_ok=True)

SERVICES = {
    "htmlapk": "🌐 HTML To APK",
    "apkhtml": "📦 APK To HTML",
    "extract": "📂 Zip Extract",
    "zip": "🗜️ Convert Zip",
}

def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        blocked INTEGER DEFAULT 0,
        joined_at TEXT DEFAULT CURRENT_TIMESTAMP
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS settings(
        service TEXT PRIMARY KEY,
        enabled INTEGER DEFAULT 1
    )""")
    for s in SERVICES:
        c.execute("INSERT OR IGNORE INTO settings(service,enabled) VALUES(?,1)", (s,))
    c.commit()
    return c

def add_user(u):
    c = db()
    c.execute("""INSERT INTO users(user_id,username,first_name)
                 VALUES(?,?,?)
                 ON CONFLICT(user_id) DO UPDATE SET username=excluded.username,
                 first_name=excluded.first_name""",
              (u.id, u.username or "", u.first_name or ""))
    c.commit(); c.close()

def is_blocked(uid):
    c=db(); r=c.execute("SELECT blocked FROM users WHERE user_id=?", (uid,)).fetchone()
    c.close()
    return bool(r and r["blocked"])

def service_enabled(s):
    c=db(); r=c.execute("SELECT enabled FROM settings WHERE service=?", (s,)).fetchone()
    c.close()
    return bool(r and r["enabled"])

def set_service(s, enabled):
    c=db(); c.execute("UPDATE settings SET enabled=? WHERE service=?", (int(enabled),s)); c.commit(); c.close()

def service_keyboard():
    # Telegram Bot API does not support custom colors for inline buttons.
    rows = [
        [InlineKeyboardButton(SERVICES["htmlapk"], callback_data="svc:htmlapk"),
         InlineKeyboardButton(SERVICES["apkhtml"], callback_data="svc:apkhtml")],
        [InlineKeyboardButton(SERVICES["extract"], callback_data="svc:extract"),
         InlineKeyboardButton(SERVICES["zip"], callback_data="svc:zip")]
    ]
    return InlineKeyboardMarkup(rows)

def join_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 @SantoBhaiOfc", url="https://t.me/SantoBhaiOfc")],
        [InlineKeyboardButton("📢 @Premium1App", url="https://t.me/Premium1App")],
        [InlineKeyboardButton("✅ Joined — Verify", callback_data="verify")]
    ])

async def joined_all(bot, uid):
    return True


async def require_access(update, context):
    uid = update.effective_user.id
    if is_blocked(uid):
        if update.callback_query:
            await update.callback_query.answer("আপনাকে block করা হয়েছে।", show_alert=True)
        elif update.message:
            await update.message.reply_text("🚫 আপনার account blocked.")
        return False
    return True


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    add_user(update.effective_user)
    if not await require_access(update, context): return
    await update.message.reply_text(
        "🛠️ <b>File Converter Bot</b>\n\nনিচের service নির্বাচন করুন:",
        parse_mode="HTML", reply_markup=service_keyboard()
    )

async def verify(update, context):
    add_user(update.effective_user)
    q = update.callback_query
    await q.answer("Channel Join system OFF ✅")
    await q.edit_message_text("✅ Channel Join system disabled.\n\nService নির্বাচন করুন:", reply_markup=service_keyboard())


def clear_state(context):
    context.user_data.clear()

async def service_click(update, context):
    q=update.callback_query
    await q.answer()
    if not await require_access(update, context): return
    s=q.data.split(":",1)[1]
    if not service_enabled(s):
        await q.answer("এই service বর্তমানে চালু নেই। অন্যগুলো ব্যবহার করুন।", show_alert=True)
        return
    clear_state(context)
    context.user_data["service"]=s
    if s=="htmlapk":
        context.user_data["step"]="name"
        await q.message.reply_text("✏️ App-এর নাম লিখুন:")
    elif s=="apkhtml":
        context.user_data["step"]="apk"
        await q.message.reply_text("📦 Send your APK:")
    elif s=="extract":
        context.user_data["step"]="extract_zip"
        await q.message.reply_text("📂 আপনার ZIP file পাঠান:")
    elif s=="zip":
        context.user_data["step"]="zip_files"
        context.user_data["zip_files"]=[]
        await q.message.reply_text("📄 একে একে file পাঠান। সব শেষ হলে নিচের Done button চাপুন।",
                                    reply_markup=ReplyKeyboardMarkup(
                                        [[KeyboardButton("✅ Done")]], resize_keyboard=True))

async def document_handler(update, context):
    if not await require_access(update, context): return
    if is_blocked(update.effective_user.id): return
    s=context.user_data.get("service"); step=context.user_data.get("step")
    doc=update.message.document
    if not s: 
        await update.message.reply_text("আগে /start দিয়ে service নির্বাচন করুন."); return

    if s=="htmlapk":
        if step=="html":
            if not doc.file_name.lower().endswith(".html"):
                await update.message.reply_text("❌ শুধু .html file দিন."); return
            f=await doc.get_file()
            p=WORK/f"{update.effective_user.id}_app.html"
            await f.download_to_drive(p)
            context.user_data["html_path"]=str(p)
            await update.message.reply_text("⏳ HTML received. APK build শুরু করছি...")
            try:
                apk=await build_apk(
                    Path(p),
                    context.user_data["app_name"],
                    context.user_data["logo_path"]
                )
                await update.message.reply_document(open(apk,"rb"),
                    caption=f"✅ {context.user_data['app_name']}.apk তৈরি হয়েছে।")
            except Exception as e:
                await update.message.reply_text("❌ APK build failed:\n"+str(e)[:2500])
            finally:
                clear_state(context)
        return

    if s=="apkhtml" and step=="apk":
        if not doc.file_name.lower().endswith(".apk"):
            await update.message.reply_text("❌ APK file দিন."); return
        f=await doc.get_file()
        p=WORK/f"{update.effective_user.id}_input.apk"
        await f.download_to_drive(p)
        await update.message.reply_text("⏳ APK থেকে embedded HTML/resource বের করছি...")
        try:
            out=extract_apk_html(p, update.effective_user.id)
            for fp in out[:30]:
                await update.message.reply_document(open(fp,"rb"))
            if len(out)>30:
                await update.message.reply_text(f"আরও {len(out)-30}টি file পাওয়া গেছে। প্রথম 30টি পাঠানো হয়েছে।")
        except Exception as e:
            await update.message.reply_text("❌ Extract failed:\n"+str(e)[:2500])
        finally: clear_state(context)
        return

    if s=="extract" and step=="extract_zip":
        if not doc.file_name.lower().endswith(".zip"):
            await update.message.reply_text("❌ ZIP file দিন."); return
        f=await doc.get_file()
        p=WORK/f"{update.effective_user.id}_input.zip"
        await f.download_to_drive(p)
        try:
            out=extract_zip_safe(p, update.effective_user.id)
            for fp in out[:50]:
                await update.message.reply_document(open(fp,"rb"), filename=fp.name)
            if len(out)>50:
                await update.message.reply_text(f"মোট {len(out)}টি file; প্রথম 50টি পাঠানো হয়েছে।")
        except Exception as e:
            await update.message.reply_text("❌ ZIP extract failed:\n"+str(e)[:2000])
        finally: clear_state(context)
        return

    if s=="zip" and step=="zip_files":
        f=await doc.get_file()
        temp=WORK/f"{update.effective_user.id}_file_{len(context.user_data['zip_files'])}"
        await f.download_to_drive(temp)
        context.user_data["zip_files"].append((doc.file_name, str(temp)))
        await update.message.reply_text(f"✅ {doc.file_name} added.\nআর file পাঠান অথবা Done চাপুন.")

async def photo_handler(update, context):
    if not await require_access(update, context): return
    if context.user_data.get("service")!="htmlapk" or context.user_data.get("step")!="logo":
        return
    photo=update.message.photo[-1]
    f=await photo.get_file()
    p=WORK/f"{update.effective_user.id}_logo.jpg"
    await f.download_to_drive(p)
    context.user_data["logo_path"]=str(p)
    context.user_data["step"]="html"
    await update.message.reply_text("🖼️ Logo received.\n📄 এখন আপনার HTML file পাঠান:")

async def text_handler(update, context):
    if update.effective_user.id == ADMIN_ID and context.user_data.get("admin_action"):
        await admin_text(update, context)
        return
    if not await require_access(update, context): return
    t=update.message.text.strip()
    s=context.user_data.get("service"); step=context.user_data.get("step")
    if s=="htmlapk" and step=="name":
        context.user_data["app_name"]=t[:40]
        context.user_data["step"]="logo"
        await update.message.reply_text("🖼️ এখন App logo image পাঠান:")
    elif s=="zip" and step=="zip_name":
        files=context.user_data.get("zip_files",[])
        if not files:
            await update.message.reply_text("❌ আগে অন্তত ১টি file দিন."); return
        out=WORK/f"{re.sub(r'[^A-Za-z0-9_-]','_',t[:50]) or 'files'}.zip"
        with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED) as z:
            for name,p in files: z.write(p,name)
        await update.message.reply_document(open(out,"rb"), caption="✅ ZIP তৈরি হয়েছে।")
        clear_state(context)
        return
    elif s=="zip" and step=="zip_files" and t=="✅ Done":
        context.user_data["step"]="zip_name"
        await update.message.reply_text("✏️ ZIP file-এর নাম দিন:", reply_markup=ReplyKeyboardMarkup(
            [[KeyboardButton("🏠 Menu")]], resize_keyboard=True))
    elif t=="🏠 Menu":
        clear_state(context)
        await update.message.reply_text("Service নির্বাচন করুন:", reply_markup=service_keyboard())

async def build_apk(html_path, name, logo_path):
    """Build a self-contained Android WebView APK from one HTML file and one logo."""
    import subprocess, textwrap

    build = WORK / f"apkbuild_{os.getpid()}_{abs(hash((str(html_path), name)))}"
    if build.exists():
        shutil.rmtree(build)
    (build/f"app/src/main/java/{application_id.replace('.', '/')}").mkdir(parents=True)
    (build/"app/src/main/assets").mkdir(parents=True)
    (build/"app/src/main/res/drawable").mkdir(parents=True)
    (build/"app/src/main/res/mipmap-hdpi").mkdir(parents=True)
    (build/"app/src/main/res/mipmap-mdpi").mkdir(parents=True)
    (build/"app/src/main/res/mipmap-xhdpi").mkdir(parents=True)
    (build/"app/src/main/res/mipmap-xxhdpi").mkdir(parents=True)
    (build/"app/src/main/res/mipmap-xxxhdpi").mkdir(parents=True)

    safe_app = re.sub(r"[^A-Za-z0-9 ._-]", "", name).strip()[:40] or "HTML App"
    application_id = "com.htmlapk." + re.sub(r"[^a-z0-9]", "", safe_app.lower())[:18] + str(abs(hash(safe_app)) % 100000)

    shutil.copyfile(html_path, build/"app/src/main/assets/index.html")
    # PNG is preferred; JPEG also works after copying to drawable as app_icon.jpg,
    # but Android drawable resource names cannot contain dots beyond the extension.
    icon_target = build/"app/src/main/res/drawable/app_icon.png"
    try:
        from PIL import Image
        im = Image.open(logo_path).convert("RGBA")
        im.thumbnail((512,512))
        canvas = Image.new("RGBA",(512,512),(0,0,0,0))
        canvas.paste(im,((512-im.width)//2,(512-im.height)//2),im)
        canvas.save(icon_target,"PNG")
    except Exception:
        # Fallback: use the original file if it is already a PNG.
        if str(logo_path).lower().endswith(".png"):
            shutil.copyfile(logo_path, icon_target)
        else:
            raise RuntimeError("Logo processing failed. Send a PNG/JPG image.")

    (build/"settings.gradle").write_text(textwrap.dedent("""
        pluginManagement { repositories { google(); mavenCentral(); gradlePluginPortal() } }
        dependencyResolutionManagement {
            repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
            repositories { google(); mavenCentral() }
        }
        rootProject.name = "HtmlApk"
        include(":app")
    """).strip()+"\n")

    (build/"build.gradle").write_text(textwrap.dedent("""
        plugins {
            id 'com.android.application' version '8.6.1' apply false
        }
    """).strip()+"\n")

    (build/"app/build.gradle").write_text(textwrap.dedent(f"""
        plugins {{ id 'com.android.application' }}

        android {{
            namespace '{application_id}'
            compileSdk 35

            defaultConfig {{
                applicationId '{application_id}'
                minSdk 23
                targetSdk 35
                versionCode 1
                versionName '1.0'
            }}
        }}
    """).strip()+"\n")

    (build/"app/src/main/AndroidManifest.xml").write_text(textwrap.dedent(f"""
        <manifest xmlns:android="http://schemas.android.com/apk/res/android">
          <uses-permission android:name="android.permission.INTERNET"/>
          <application
              android:theme="@style/AppTheme"
              android:label="{safe_app.replace('&','&amp;').replace('"','&quot;')}"
              android:icon="@drawable/app_icon"
              android:usesCleartextTraffic="true">
            <activity
                android:name=".MainActivity"
                android:exported="true">
              <intent-filter>
                <action android:name="android.intent.action.MAIN"/>
                <category android:name="android.intent.category.LAUNCHER"/>
              </intent-filter>
            </activity>
          </application>
        </manifest>
    """).strip()+"\n")

    (build/"app/src/main/res/values").mkdir(parents=True, exist_ok=True)
    (build/"app/src/main/res/values/styles.xml").write_text("""
        <resources>
          <style name="AppTheme" parent="android:style/Theme.Material.Light.NoActionBar">
            <item name="android:fontFamily">sans</item>
            <item name="android:colorAccent">#2D7CFF</item>
            <item name="android:windowActionModeOverlay">true</item>
          </style>
        </resources>
    """.strip()+"\n")

    java_dir = build/f"app/src/main/java/{application_id.replace(".", "/")}"
    java_dir.mkdir(parents=True, exist_ok=True)
    (java_dir/"MainActivity.java").write_text(f"""
        package {application_id};

        import android.app.Activity;
        import android.os.Bundle;
        import android.webkit.WebSettings;
        import android.webkit.WebView;
        import android.webkit.WebViewClient;

        public class MainActivity extends Activity {{
            @Override public void onCreate(Bundle b) {{
                super.onCreate(b);
                WebView w = new WebView(this);
                w.setWebViewClient(new WebViewClient());
                WebSettings s = w.getSettings();
                s.setJavaScriptEnabled(true);
                s.setDomStorageEnabled(true);
                s.setAllowFileAccess(true);
                s.setAllowContentAccess(true);
                w.loadUrl("file:///android_asset/index.html");
                setContentView(w);
            }}
        }}
    """.strip()+"\n")

    gradle = "/opt/gradle/bin/gradle"
    proc = subprocess.run([gradle, ":app:assembleDebug", "--no-daemon"],
                          cwd=build, text=True, capture_output=True, timeout=900)
    if proc.returncode != 0:
        raise RuntimeError((proc.stdout + "\n" + proc.stderr)[-5000:])
    apk = build/"app/build/outputs/apk/debug/app-debug.apk"
    if not apk.exists():
        raise RuntimeError("APK output not found.")
    return str(apk)

def extract_apk_html(apk, uid):
    outdir=WORK/f"{uid}_apk_extract"; shutil.rmtree(outdir,ignore_errors=True); outdir.mkdir()
    with zipfile.ZipFile(apk) as z:
        names=z.namelist()
        candidates=[n for n in names if n.lower().endswith((".html",".htm",".js",".css"))]
        files=[]
        for n in candidates:
            if n.startswith("/") or ".." in Path(n).parts: continue
            target=outdir/Path(n).name
            with z.open(n) as src, open(target,"wb") as dst: shutil.copyfileobj(src,dst)
            files.append(target)
        return files

def extract_zip_safe(zip_path, uid):
    outdir=WORK/f"{uid}_zip_extract"; shutil.rmtree(outdir,ignore_errors=True); outdir.mkdir()
    files=[]
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            p=Path(info.filename)
            if p.is_absolute() or ".." in p.parts: continue
            target=outdir/p
            target.parent.mkdir(parents=True,exist_ok=True)
            if not info.is_dir():
                with z.open(info) as src, open(target,"wb") as dst: shutil.copyfileobj(src,dst)
                files.append(target)
    return files

async def admin(update, context):
    if update.effective_user.id!=ADMIN_ID: return
    kb=[
        [InlineKeyboardButton("📊 Users",callback_data="adm:users"),
         InlineKeyboardButton("📢 Broadcast",callback_data="adm:broadcast")],
        [InlineKeyboardButton("⚙️ Services",callback_data="adm:services"),
         InlineKeyboardButton("🚫 Block/Unblock",callback_data="adm:block")]
    ]
    await update.message.reply_text("👑 Admin Panel",reply_markup=InlineKeyboardMarkup(kb))

async def admin_click(update, context):
    q=update.callback_query
    if q.from_user.id!=ADMIN_ID: return
    await q.answer()
    action=q.data.split(":",1)[1]
    if action=="users":
        c=db(); total=c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        blocked=c.execute("SELECT COUNT(*) n FROM users WHERE blocked=1").fetchone()["n"]; c.close()
        await q.message.reply_text(f"👥 Total users: {total}\n🚫 Blocked: {blocked}")
    elif action=="services":
        c=db(); rows=c.execute("SELECT service,enabled FROM settings").fetchall(); c.close()
        kb=[[InlineKeyboardButton(f"{SERVICES[s]} — {'ON' if e else 'OFF'}",
                                  callback_data=f"toggle:{s}")]
            for s,e in rows]
        await q.message.reply_text("Service control:",reply_markup=InlineKeyboardMarkup(kb))
    elif action=="broadcast":
        context.user_data["admin_action"]="broadcast"
        await q.message.reply_text("📢 Broadcast message পাঠান।")
    elif action=="block":
        context.user_data["admin_action"]="block"
        await q.message.reply_text("User ID পাঠান। আবার দিলে unblock হবে।")

async def toggle_service(update, context):
    q=update.callback_query
    if q.from_user.id!=ADMIN_ID: return
    s=q.data.split(":",1)[1]
    set_service(s,not service_enabled(s))
    await q.answer("Updated")
    c=db(); rows=c.execute("SELECT service,enabled FROM settings").fetchall(); c.close()
    kb=[[InlineKeyboardButton(f"{SERVICES[x]} — {'ON' if e else 'OFF'}",callback_data=f"toggle:{x}")]
        for x,e in rows]
    await q.edit_message_reply_markup(InlineKeyboardMarkup(kb))

async def admin_text(update, context):
    if update.effective_user.id!=ADMIN_ID: return
    action=context.user_data.get("admin_action")
    if action=="broadcast":
        c=db(); ids=[r["user_id"] for r in c.execute("SELECT user_id FROM users WHERE blocked=0")]; c.close()
        ok=0
        for uid in ids:
            try:
                await context.bot.copy_message(uid,update.effective_chat.id,update.message.message_id); ok+=1
            except Exception: pass
        context.user_data.pop("admin_action",None)
        await update.message.reply_text(f"✅ Broadcast sent: {ok}/{len(ids)}")
    elif action=="block":
        try:
            uid=int(update.message.text.strip())
            c=db(); r=c.execute("SELECT blocked FROM users WHERE user_id=?",(uid,)).fetchone()
            if not r:
                c.close(); await update.message.reply_text("User not found."); return
            new=0 if r["blocked"] else 1
            c.execute("UPDATE users SET blocked=? WHERE user_id=?",(new,uid)); c.commit(); c.close()
            await update.message.reply_text("✅ "+("Blocked" if new else "Unblocked"))
        except Exception:
            await update.message.reply_text("Invalid user ID.")
        finally: context.user_data.pop("admin_action",None)

def run_bot():
    application=Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("start",start))
    application.add_handler(CommandHandler("admin",admin))
    application.add_handler(CallbackQueryHandler(verify,pattern="^verify$"))
    application.add_handler(CallbackQueryHandler(service_click,pattern="^svc:"))
    application.add_handler(CallbackQueryHandler(admin_click,pattern="^adm:"))
    application.add_handler(CallbackQueryHandler(toggle_service,pattern="^toggle:"))
    application.add_handler(MessageHandler(filters.PHOTO,photo_handler))
    application.add_handler(MessageHandler(filters.Document.ALL,document_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,admin_text),group=0)
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_handler),group=1)
    print("Telegram bot running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, stop_signals=None)

if __name__=="__main__":
    if not TOKEN: raise RuntimeError("TOKEN environment variable missing")
    db()
    web=Flask(__name__)
    @web.get("/")
    def home(): return "HTML/APK Telegram Bot is running."
    @web.get("/health")
    def health(): return jsonify(ok=True)
    threading.Thread(target=run_bot,daemon=True).start()
    web.run(host="0.0.0.0",port=PORT)
