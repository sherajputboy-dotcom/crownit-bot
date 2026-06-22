#!/usr/bin/env python3
"""
Crownit Bot - Advanced Surveys & Reward Claiming Engine
Equipped with Dynamic Force-Join Channels, Configurable Anti-Bot Delays,
Advanced Broadcast with Live Tracking, User Management/Banning, Database Backup/Restore,
Duplicate Registration Continue/Cancel Flow, Retry Mechanics, and Port-Binding helper for Render.
State machine uses custom context.user_data variables to ensure zero button lag.
"""

import os
import re
import time
import json
import random
import asyncio
import base64
import logging
import urllib.parse
from datetime import datetime, timedelta
from typing import Optional, Dict, List
import http.server
import socketserver
import threading

import httpx
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    CallbackQueryHandler
)

# Load env variables from .env if present
load_dotenv()

# Logger configuration
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Config environment variables (or fallbacks)
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
try:
    ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))
except ValueError:
    ADMIN_ID = 0

PORT = int(os.environ.get("PORT", "10000"))

# Check if essential vars are missing
if not BOT_TOKEN:
    logger.warning("BOT_TOKEN is missing from environment. Bot will fail to start.")
if not ADMIN_ID:
    logger.warning("ADMIN_ID is missing from environment. Admin features will be unavailable.")

CROWNIT_BASE = "https://feedback.crownit.in"
DB_FILE = "crownit_db.json"

# Indian name generators for survey profile
INDIAN_MALE = ["Rakesh", "Mukesh", "Amit", "Vijay", "Suresh", "Rajesh", "Deepak", "Rahul", "Arun", "Sanjay", "Anil", "Sunil"]
INDIAN_FEMALE = ["Anita", "Priya", "Neha", "Pooja", "Sunita", "Kavita", "Divya", "Ritu", "Manisha", "Kiran", "Rekha", "Babita"]
INDIAN_LAST = ["Kumar", "Singh", "Sharma", "Verma", "Gupta", "Patel", "Reddy", "Joshi", "Mishra", "Choudhary", "Prasad"]

SURVEY_RESPONSES = [
    "I find this product useful for daily needs",
    "The quality is good and meets expectations",
    "I am satisfied with the service provided",
    "Good experience overall would recommend",
    "The product is user friendly and easy to use",
    "The packaging is sturdy and keeps the product fresh",
    "Very reasonable pricing and excellent value for money",
    "The taste is premium and feels very authentic"
]

# Database Setup & Thread Safety
db_lock = asyncio.Lock()
db_data = {
    "users": {},
    "channels": [
        {"username": "@slprolooters", "link": "https://t.me/slprolooters"}
    ],
    "settings": {
        "force_join_enabled": True,
        "max_surveys_per_user": 2,
        "min_delay": 5,
        "max_delay": 10
    },
    "banned_users": []
}

def load_db():
    global db_data
    try:
        if os.path.exists(DB_FILE):
            with open(DB_FILE, "r") as f:
                data = json.load(f)
                # Ensure all key sections are populated
                for key in ["users", "channels", "settings", "banned_users"]:
                    if key not in data:
                        data[key] = db_data[key]
                if not isinstance(data["banned_users"], list):
                    data["banned_users"] = []
                db_data = data
                logger.info("Database loaded successfully.")
        else:
            save_db_sync()
            logger.info("Default database file created.")
    except Exception as e:
        logger.error(f"Error loading database: {e}")

def save_db_sync():
    try:
        with open(DB_FILE, "w") as f:
            json.dump(db_data, f, indent=2)
    except Exception as e:
        logger.error(f"Sync DB save error: {e}")

async def save_db():
    async with db_lock:
        try:
            await asyncio.to_thread(save_db_sync)
        except Exception as e:
            logger.error(f"Async DB save error: {e}")

load_db()

# HTTP Call Helpers using Async HTTPX Client
def crownit_headers(auth_uid=None, auth_sid=None):
    if auth_uid and auth_sid:
        raw = f"{auth_uid}:{auth_sid}"
    else:
        raw = "6667:759b064f-381d-11e5-810b-0286c96d2641"
    b64 = base64.b64encode(raw.encode()).decode()
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:151.0) Gecko/20100101 Firefox/151.0",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
        "Content-Type": "application/json",
        "Origin": CROWNIT_BASE,
        "Referer": f"{CROWNIT_BASE}/lite/onboarding",
        "Authorization": f"Basic {b64}",
    }

async def crownit_post(client: httpx.AsyncClient, path: str, data=None, uid=None, sid=None):
    try:
        headers = crownit_headers(uid, sid)
        r = await client.post(f"{CROWNIT_BASE}{path}", json=data or {}, headers=headers, timeout=30.0)
        return r.json() if r.status_code == 200 else {"error": r.text, "code": r.status_code}
    except Exception as e:
        return {"error": str(e)}

async def crownit_put(client: httpx.AsyncClient, path: str, data=None, uid=None, sid=None):
    try:
        headers = crownit_headers(uid, sid)
        r = await client.put(f"{CROWNIT_BASE}{path}", json=data or {}, headers=headers, timeout=30.0)
        return r.json() if r.status_code == 200 else {}
    except Exception as e:
        logger.error(f"crownit_put error for {path}: {e}")
        return {}

async def crownit_get(client: httpx.AsyncClient, path: str, uid=None, sid=None):
    try:
        headers = crownit_headers(uid, sid)
        r = await client.get(f"{CROWNIT_BASE}{path}", headers=headers, timeout=30.0)
        return r.json() if r.status_code == 200 else {}
    except Exception as e:
        logger.error(f"crownit_get error for {path}: {e}")
        return {}

# Business Logic Utilities
def generate_profile():
    gender = random.choice(["male", "female"])
    first = random.choice(INDIAN_MALE if gender == "male" else INDIAN_FEMALE)
    last = random.choice(INDIAN_LAST)
    name = f"{first} {last}"
    gender_str = "Male" if gender == "male" else "Female"
    today = datetime.now()
    dob = today - timedelta(days=random.randint(20*365+1, 55*365))
    return {"name": name, "first": first, "gender": gender_str, "dob": dob.strftime("%d-%m-%Y")}

def extract_container(link):
    if not link: return ""
    if "container=" in link:
        parsed = urllib.parse.urlparse(link)
        qs = urllib.parse.parse_qs(parsed.query)
        frag_qs = {}
        if "#" in link:
            frag = link.split("#")[1]
            if "?" in frag:
                frag_qs = urllib.parse.parse_qs(frag.split("?")[1])
        return (qs.get("container") or frag_qs.get("container") or [""])[0]
    if "/container/" in link:
        m = re.search(r'/container/([^?]+)', link)
        return m.group(1) if m else ""
    return ""

async def take_survey_exact(client: httpx.AsyncClient, uid: str, sid: str, survey: dict) -> int:
    """Takes survey asynchronously and returns count of answered questions."""
    link = survey.get("link", "")
    if not link: return 0
    
    container = survey.get("containerId") or extract_container(link)
    if not container: return 0
    
    ts = str(int(time.time() * 1000))
    web_link = f"fb{ts}"
    
    session_payload = {
        "uid": "", "targetLanguage": "",
        "extraParams": {
            "fingerprintnew": int(time.time()),
            "clientscreen": {
                "availHeight": 720, "availLeft": 0, "availTop": 0,
                "availWidth": 1366, "colorDepth": 24,
                "height": 768, "pixelDepth": 24, "width": 1366,
                "orientation": {"type": "landscape-primary"}
            }
        },
        "referer": f"{CROWNIT_BASE}/lite/onboarding",
        "autoAnswer": {}, "preview": "published",
        "surveyLink": link, "webLink": web_link,
        "cookies": {"browserInfo": "Mozilla/5.0"},
        "channel": container, "unique": ts,
        "survey_source": "pwa", "utm_source": "pwa", "utm_medium": "registration",
        "sid": web_link, "isShowBackClicked": "false",
        "questionId": 1068, "options": [{"id": "-1", "text": ""}],
        "unselect": [], "otpGet": True, "seqNo": -1
    }
    
    session_resp = await crownit_post(client, "/api/survey/session", session_payload, uid, sid)
    survey_uid = session_resp.get("uid")
    if not survey_uid: return 0
    
    answered = 0
    min_delay = db_data["settings"].get("min_delay", 5)
    max_delay = db_data["settings"].get("max_delay", 10)
    
    for qi in range(30):
        await asyncio.sleep(random.uniform(min_delay, max_delay))
        
        q_payload = dict(session_payload)
        q_payload["uid"] = survey_uid
        for k in ["questionId", "options", "unselect", "otpGet", "seqNo", "isShowBackClicked"]:
            q_payload.pop(k, None)
        
        q_resp = await crownit_post(client, "/api/survey/smart/question", q_payload, uid, sid)
        if q_resp.get("ended") or q_resp.get("terminated"): break
        
        question = q_resp.get("question") or q_resp.get("entity", {}).get("question") or {}
        if isinstance(question, list): question = question[0] if question else {}
        
        qid = question.get("questionId") or question.get("qId")
        qtype = str(question.get("type") or question.get("qType") or "")
        if not qid: break
        
        opts = question.get("choice") or question.get("options") or question.get("choices") or []
        valid_opts = [o for o in opts if isinstance(o, dict) and o.get("id") is not None]
        
        answer_opts, unselect_opts = [], []
        
        if qtype == "I": pass
        elif qtype in ("5", "text", "input", "T"):
            text = random.choice(SURVEY_RESPONSES)
            if valid_opts:
                opt = valid_opts[0]
                misc = dict(opt.get("misc") or opt.get("miscellaneous") or {})
                misc["rank"] = text
                answer_opts.append({"id": str(opt["id"]), "text": opt.get("text", ""), "misc": misc})
        elif valid_opts:
            chosen = random.choice(valid_opts)
            misc = dict(chosen.get("misc") or chosen.get("miscellaneous") or {})
            answer_opts.append({"id": str(chosen["id"]), "text": chosen.get("text", ""), "misc": misc})
            for o in valid_opts:
                if str(o["id"]) != str(chosen["id"]):
                    umisc = dict(o.get("misc") or {})
                    unselect_opts.append({"id": str(o["id"]), "text": o.get("text", ""), "misc": umisc})
        
        answer = {
            "uid": survey_uid, "options": answer_opts, "unselect": unselect_opts,
            "questionId": int(qid), "seqNo": qi + 1, "type": qtype,
            "extraParams": session_payload["extraParams"],
            "autoAnswer": {}, "preview": "published",
            "surveyLink": link, "linkReceived": link,
            "webLink": web_link, "survey_source": "pwa",
            "utm_source": "pwa", "utm_medium": "registration",
            "channel": container, "sid": web_link,
            "unique": str(int(time.time() * 1000)),
            "cookies": {"browserInfo": "Mozilla/5.0"}
        }
        
        a_resp = await crownit_post(client, "/api/survey/smart/answer", answer, uid, sid)
        if a_resp.get("responseCode") == 1:
            answered += 1
            if a_resp.get("ended") or a_resp.get("terminated"): break
            
    return answered

async def claim_rewards(client: httpx.AsyncClient, uid: str, sid: str) -> int:
    """Checks and claims pending scratch cards asynchronously."""
    claimed = 0
    try:
        resp = await crownit_get(client, f"/api/user/rewards?type=all&pageNo=1&source=pwa", uid, sid)
        pending_cards = resp.get("pendingCards", {})
        token = pending_cards.get("token")
        pending_count = pending_cards.get("pendingCount", 0)
        
        logger.info(f"Rewards lookup: pending={pending_count}, token={bool(token)}")
        
        if token and pending_count > 0:
            surveys = (await crownit_post(client, "/rer/pwa/eligible", {}, uid, sid)).get("result", [])
            if surveys:
                survey_id = surveys[0].get("surveyId", "") or surveys[0].get("_mapped_sid", "")
                scratch_resp = await crownit_post(client, "/api/scratch", {"surveyId": str(survey_id), "token": token}, uid, sid)
                
                logger.info(f"Scratch card open responseCode: {scratch_resp.get('responseCode')}")
                
                if scratch_resp.get("responseCode") == 1:
                    all_rewards = scratch_resp.get("result", {}).get("allRewards", {})
                    rid = all_rewards.get("rid")
                    reward_details = all_rewards.get("rewardDetails", [])
                    
                    if rid and reward_details:
                        reward_id = reward_details[0].get("reward_id")
                        claim_resp = await crownit_post(client, "/api/scratch/claim", {
                            "surveyId": str(survey_id),
                            "rewardId": reward_id,
                            "rid": rid,
                            "token": token
                        }, uid, sid)
                        
                        logger.info(f"Reward claim responseCode: {claim_resp.get('responseCode')}")
                        
                        if claim_resp.get("responseCode") == 1:
                            claimed += 1
    except Exception as e:
        logger.error(f"Async reward claiming error: {e}")
    
    return claimed

# Dynamic Force-Join Handlers
async def check_user_channels(user_id: int, bot) -> List[dict]:
    """Checks memberships of all channels dynamically. Returns list of not-joined channels."""
    if user_id == ADMIN_ID:
        return []
    if not db_data.get("settings", {}).get("force_join_enabled", True):
        return []
        
    not_joined = []
    for c in db_data.get("channels", []):
        username = c.get("username", "")
        if not username:
            continue
        try:
            member = await bot.get_chat_member(chat_id=username, user_id=user_id)
            if member.status not in ["member", "administrator", "creator"]:
                not_joined.append(c)
        except Exception as e:
            logger.error(f"Error checking channel {username} member status: {e}. Treating as NOT joined.")
            not_joined.append(c)
    return not_joined

def get_join_keyboard(not_joined_channels: List[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for c in not_joined_channels:
        lbl = c.get("username", "Join Channel")
        link = c.get("link", "https://t.me")
        buttons.append([InlineKeyboardButton(f"📢 Join {lbl} ↗", url=link)])
    buttons.append([InlineKeyboardButton("✅ Done! I've Joined", callback_data="confirm_join")])
    return InlineKeyboardMarkup(buttons)

# Menus and Keyboards Generator
def user_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📱 Register & Earn", callback_data="register")],
        [InlineKeyboardButton("📊 Status", callback_data="status")],
    ])

def admin_dashboard_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Global Settings", callback_data="admin_config_menu")],
        [InlineKeyboardButton("📢 Manage Channels", callback_data="admin_channels")],
        [InlineKeyboardButton("👤 Manage Users", callback_data="admin_users")],
        [InlineKeyboardButton("📊 Broadcast Notification", callback_data="admin_broadcast_prompt")],
        [InlineKeyboardButton("💾 Backup & Restore", callback_data="admin_backup_menu")],
        [InlineKeyboardButton("📈 Extended Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("⬅️ Test User Menu", callback_data="admin_test_user_menu")]
    ])

# Bot Commands
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    
    # Reset any active text-input state upon /start
    context.user_data["state"] = None
    
    if user_id in db_data.get("banned_users", []):
        await update.message.reply_text("❌ *You are banned from using this bot.*", parse_mode="Markdown")
        return
        
    u_str = str(user_id)
    if u_str not in db_data["users"]:
        db_data["users"][u_str] = {
            "joined_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status": "joined",
            "phone": "N/A",
            "name": "N/A",
            "surveys": 0,
            "rewards": 0
        }
        await save_db()
        
    not_joined = await check_user_channels(user_id, context.bot)
    if not_joined:
        join_msg = (
            "⚠️ *Access Restricted*\n\n"
            "Bot use karne ke liye channel join karo:\n\n"
            "Join karne ke baad niche *Done! I've Joined* dabao."
        )
        await update.message.reply_text(join_msg, reply_markup=get_join_keyboard(not_joined), parse_mode="Markdown")
        return
        
    if user_id == ADMIN_ID:
        users_count = len(db_data["users"])
        await update.message.reply_text(
            f"🌟 *Crownit Admin Control Panel*\n👥 Registered Users: {users_count}\n\nSelect an option below:",
            reply_markup=admin_dashboard_kb(),
            parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(
            "🌟 *CROWNIT EARNING BOT*\n\nNiche diye gae buttons use karein:",
            reply_markup=user_menu_kb(),
            parse_mode="Markdown"
        )

# Global State Variables for Temporary Admin In-Memory Data
pending_otp = {}
admin_session = {}
active_sessions = {}

async def run_surveys_and_claims(query, user_id: int, uid: str, sid: str, phone: str):
    """Core automated engine to run campaigns and handle retry mechanics."""
    async with httpx.AsyncClient(verify=False) as client:
        await query.edit_message_text("📋 *Fetching eligible surveys from Crownit...*", parse_mode="Markdown")
        surveys_resp = await crownit_post(client, "/rer/pwa/eligible", {}, uid, sid)
        surveys = surveys_resp.get("result", [])
        
        total_q = 0
        limit = db_data["settings"].get("max_surveys_per_user", 2)
        
        survey_failed = False
        if isinstance(surveys, list) and len(surveys) > 0:
            count = min(len(surveys), limit)
            await query.edit_message_text(f"📋 *Found {len(surveys)} surveys. Running {count} task(s)...*", parse_mode="Markdown")
            
            for i, s in enumerate(surveys[:count]):
                category_name = s.get("category", "General survey")
                await query.edit_message_text(
                    f"📝 *Taking Survey {i+1}/{count}:* {category_name}\n"
                    f"⏳ Anti-bot delays active. Please wait...",
                    parse_mode="Markdown"
                )
                q = await take_survey_exact(client, uid, sid, s)
                if q:
                    total_q += q
                    await query.edit_message_text(f"✅ *Survey {i+1} completed!* ({q} answers submitted)", parse_mode="Markdown")
                    await asyncio.sleep(2)
                else:
                    survey_failed = True
                    break
        else:
            await query.edit_message_text("⚠️ *No eligible surveys found for this profile right now.*", parse_mode="Markdown")
            
        if survey_failed:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Retry Surveys", callback_data="retry_surveys")],
                [InlineKeyboardButton("⬅️ Main Menu", callback_data="admin_menu" if user_id == ADMIN_ID else "confirm_join")]
            ])
            await query.edit_message_text(
                "❌ *Survey Completion Failed!*\n\n"
                "Crownit server rejected answers or timed out. Click below to retry:",
                reply_markup=kb,
                parse_mode="Markdown"
            )
            return
            
        # 2. Claim rewards
        rewards = 0
        await query.edit_message_text("🎁 *Checking rewards and claiming scratch cards...*", parse_mode="Markdown")
        rewards = await claim_rewards(client, uid, sid)
        
        if rewards == 0 and (total_q > 0 or (isinstance(surveys, list) and len(surveys) > 0)):
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Retry Claiming Rewards", callback_data="retry_claim")],
                [InlineKeyboardButton("⬅️ Main Menu", callback_data="admin_menu" if user_id == ADMIN_ID else "confirm_join")]
            ])
            await query.edit_message_text(
                "⚠️ *Reward Claiming Failed!*\n\n"
                "Surveys were completed successfully, but card claiming failed. Click below to retry:",
                reply_markup=kb,
                parse_mode="Markdown"
            )
            return
            
        # Update local database
        u_str = str(user_id)
        profile = generate_profile()
        db_data["users"][u_str] = {
            "phone": phone,
            "name": profile["name"],
            "gender": profile["gender"],
            "dob": profile["dob"],
            "status": "completed",
            "surveys": total_q,
            "rewards": rewards,
            "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "joined_at": db_data["users"].get(u_str, {}).get("joined_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        }
        await save_db()
        
        # Clear temporary active session
        active_sessions.pop(user_id, None)
        
        kb = admin_dashboard_kb() if user_id == ADMIN_ID else user_menu_kb()
        
        # Deliver explicit ₹10 Amazon Gift Card reward status
        reward_display = "🎁 Reward claimed: *₹10 Amazon Gift Card*"
        if rewards > 0:
            reward_display += f" (Successfully Claimed: {rewards})"
        else:
            reward_display += " (Pending Crediting)"
            
        success_text = (
            f"🎉 *CAMPAIGN PROCESS COMPLETED!*\n\n"
            f"👤 Profile: *{profile['name']}* ({profile['gender']})\n"
            f"📱 Phone: `+91{phone}`\n"
            f"📝 Questions Answered: `{total_q}`\n"
            f"{reward_display}\n\n"
            f"Earnings will be credited directly to your Crownit wallet."
        )
        await query.edit_message_text(success_text, reply_markup=kb, parse_mode="Markdown")

# Unified Callback Query Handler
async def handle_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    # Answer immediately to ensure buttons never spin forever or lag
    await query.answer()
    
    user_id = query.from_user.id
    data = query.data
    
    if user_id in db_data.get("banned_users", []):
        await query.edit_message_text("❌ You are banned from using this bot.")
        return
        
    # Reset any active text state when button routing begins
    if not data.startswith("admin_user_") and data not in ["dup_continue", "dup_cancel"]:
        context.user_data["state"] = None
        
    # Force Join checking
    if data == "confirm_join":
        not_joined = await check_user_channels(user_id, context.bot)
        if not_joined:
            await query.answer("❌ Aapne abhi tak sabhi channels join nahi kiye!", show_alert=True)
        else:
            await query.answer("✅ Verified successfully!", show_alert=True)
            if user_id == ADMIN_ID:
                await query.edit_message_text(
                    "🌟 *Crownit Admin Control Panel*\n\nSelect an option below:",
                    reply_markup=admin_dashboard_kb(),
                    parse_mode="Markdown"
                )
            else:
                await query.edit_message_text(
                    "🌟 *CROWNIT EARNING BOT*\n\nNiche diye gae buttons use karein:",
                    reply_markup=user_menu_kb(),
                    parse_mode="Markdown"
                )
        return
        
    if user_id != ADMIN_ID:
        not_joined = await check_user_channels(user_id, context.bot)
        if not_joined:
            join_msg = "⚠️ *Access Restricted*\n\nBot use karne ke liye channel join karo:"
            await query.edit_message_text(join_msg, reply_markup=get_join_keyboard(not_joined), parse_mode="Markdown")
            return
            
    # --- ROUTING OF BUTTON CLICKS ---
    if data == "register":
        await query.edit_message_text("📱 *Enter 10-digit Indian Mobile Number:*\n\nType `/cancel` to abort.", parse_mode="Markdown")
        context.user_data["state"] = "WAITING_FOR_MOBILE"
        
    elif data == "status":
        u = db_data["users"].get(str(user_id), {})
        txt = (
            f"📊 *User Account Status*\n\n"
            f"📱 Phone: `{u.get('phone', 'N/A')}`\n"
            f"👤 Profile: `{u.get('name', 'N/A')}`\n"
            f"📋 Status: *{u.get('status', 'New')}*\n"
            f"📝 Surveys Taken: `{u.get('surveys', 0)}`\n"
            f"🎁 Total Rewards: `{u.get('rewards', 0)}`"
        )
        kb = admin_dashboard_kb() if user_id == ADMIN_ID else user_menu_kb()
        await query.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown")
        
    elif data == "dup_continue":
        reg_info = context.user_data.get("pending_register")
        if not reg_info:
            await query.edit_message_text("❌ Session expired. Please try again.", reply_markup=user_menu_kb())
            return
            
        mobile = reg_info["phone"]
        # OTP was already requested during /api/users, prompt user for entry
        await query.edit_message_text(
            f"📡 Proceeding with OTP verification...\n\n"
            f"📩 *Enter OTP code received on +91{mobile}:*",
            parse_mode="Markdown"
        )
        context.user_data["state"] = "WAITING_FOR_OTP"
        
    elif data == "dup_cancel":
        context.user_data.pop("pending_register", None)
        await query.edit_message_text("❌ Registration cancelled.", reply_markup=user_menu_kb())
        
    elif data == "retry_surveys":
        session = active_sessions.get(user_id)
        if not session:
            await query.edit_message_text("❌ Session expired. Please register again.", reply_markup=user_menu_kb())
            return
        await run_surveys_and_claims(query, user_id, session["uid"], session["sid"], session["phone"])
        
    elif data == "retry_claim":
        session = active_sessions.get(user_id)
        if not session:
            await query.edit_message_text("❌ Session expired. Please register again.", reply_markup=user_menu_kb())
            return
            
        await query.edit_message_text("🔄 *Retrying reward claim...*", parse_mode="Markdown")
        async with httpx.AsyncClient(verify=False) as client:
            rewards = await claim_rewards(client, session["uid"], session["sid"])
            
        if rewards > 0:
            u_str = str(user_id)
            if u_str in db_data["users"]:
                db_data["users"][u_str]["rewards"] = rewards
                await save_db()
                
            active_sessions.pop(user_id, None)
            kb = admin_dashboard_kb() if user_id == ADMIN_ID else user_menu_kb()
            await query.edit_message_text(
                f"🎉 *REWARD CLAIM SUCCESSFUL!*\n\n"
                f"📱 Phone: `+91{session['phone']}`\n"
                f"🎁 Reward claimed: *₹10 Amazon Gift Card*\n\n"
                f"Earnings will be credited directly to your Crownit account.",
                reply_markup=kb,
                parse_mode="Markdown"
            )
        else:
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔄 Retry Claiming Rewards", callback_data="retry_claim")],
                [InlineKeyboardButton("⬅️ Main Menu", callback_data="admin_menu" if user_id == ADMIN_ID else "confirm_join")]
            ])
            await query.edit_message_text(
                "❌ *Reward Claiming Failed Again*\n\n"
                "The bot failed to claim the reward. Click below to retry.",
                reply_markup=kb,
                parse_mode="Markdown"
            )
            
    # --- ADMIN CALLBACK ACTIONS ---
    elif user_id == ADMIN_ID:
        if data == "admin_menu":
            users_count = len(db_data["users"])
            await query.edit_message_text(
                f"🌟 *Crownit Admin Control Panel*\n👥 Registered Users: {users_count}\n\nSelect an option below:",
                reply_markup=admin_dashboard_kb(),
                parse_mode="Markdown"
            )
            
        elif data == "admin_test_user_menu":
            await query.edit_message_text(
                "🌟 *CROWNIT EARNING BOT* (Testing Mode)\n\nNiche diye gae buttons use karein:",
                reply_markup=user_menu_kb(),
                parse_mode="Markdown"
            )
            
        elif data == "admin_channels":
            chans = db_data.get("channels", [])
            status = "🟢 Enabled" if db_data["settings"].get("force_join_enabled", True) else "🔴 Disabled"
            txt = f"📢 *Force Join Settings*\n\nForce Join is currently: *{status}*\n\n*Channels:*\n"
            if not chans:
                txt += "❌ No channels configured.\n"
            for i, c in enumerate(chans, 1):
                txt += f"{i}. `{c.get('username')}` ([Link]({c.get('link')}))\n"
                
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Channel", callback_data="admin_add_channel")],
                [InlineKeyboardButton("❌ Remove Channel", callback_data="admin_remove_channel_list")],
                [InlineKeyboardButton("🔄 Toggle Force Join", callback_data="admin_toggle_join")],
                [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_menu")]
            ])
            await query.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown", disable_web_page_preview=True)
            
        elif data == "admin_toggle_join":
            cur = db_data["settings"].get("force_join_enabled", True)
            db_data["settings"]["force_join_enabled"] = not cur
            await save_db()
            await query.answer(f"Force Join set to {not cur}", show_alert=True)
            chans = db_data.get("channels", [])
            status = "🟢 Enabled" if not cur else "🔴 Disabled"
            txt = f"📢 *Force Join Settings*\n\nForce Join is currently: *{status}*\n\n*Channels:*\n"
            for i, c in enumerate(chans, 1):
                txt += f"{i}. `{c.get('username')}` ([Link]({c.get('link')}))\n"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Channel", callback_data="admin_add_channel")],
                [InlineKeyboardButton("❌ Remove Channel", callback_data="admin_remove_channel_list")],
                [InlineKeyboardButton("🔄 Toggle Force Join", callback_data="admin_toggle_join")],
                [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_menu")]
            ])
            await query.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown", disable_web_page_preview=True)
            
        elif data == "admin_add_channel":
            await query.edit_message_text(
                "➕ *Add Force Join Channel*\n\n"
                "Send channel details in the following format:\n"
                "`@channel_username|https://t.me/invite_link` (Must include pipe '|').\n\n"
                "Bot must be admin in the channel to verify joins.\n"
                "Type `/cancel` to abort.",
                parse_mode="Markdown"
            )
            context.user_data["state"] = "ADMIN_STATE_ADD_CHANNEL"
            
        elif data == "admin_remove_channel_list":
            chans = db_data.get("channels", [])
            if not chans:
                await query.answer("No channels to remove!", show_alert=True)
                return
            buttons = []
            for i, c in enumerate(chans):
                buttons.append([InlineKeyboardButton(f"❌ {c.get('username')}", callback_data=f"admin_remove_channel_{i}")])
            buttons.append([InlineKeyboardButton("⬅️ Back", callback_data="admin_channels")])
            await query.edit_message_text("❌ *Select Channel to Delete:*", reply_markup=InlineKeyboardMarkup(buttons), parse_mode="Markdown")
            
        elif data.startswith("admin_remove_channel_"):
            idx = int(data.split("_")[-1])
            chans = db_data.get("channels", [])
            if 0 <= idx < len(chans):
                removed = chans.pop(idx)
                await save_db()
                await query.answer(f"Removed {removed.get('username')}", show_alert=True)
            chans = db_data.get("channels", [])
            status = "🟢 Enabled" if db_data["settings"].get("force_join_enabled", True) else "🔴 Disabled"
            txt = f"📢 *Force Join Settings*\n\nForce Join is currently: *{status}*\n\n*Channels:*\n"
            for i, c in enumerate(chans, 1):
                txt += f"{i}. `{c.get('username')}` ([Link]({c.get('link')}))\n"
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add Channel", callback_data="admin_add_channel")],
                [InlineKeyboardButton("❌ Remove Channel", callback_data="admin_remove_channel_list")],
                [InlineKeyboardButton("🔄 Toggle Force Join", callback_data="admin_toggle_join")],
                [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_menu")]
            ])
            await query.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown", disable_web_page_preview=True)
            
        elif data == "admin_users":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("🔍 Search User (Phone / ID)", callback_data="admin_user_lookup_prompt")],
                [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_menu")]
            ])
            await query.edit_message_text(
                "👤 *User Management Dashboard*\n\n"
                "Search, ban/unban, or reset user registrations to let them redo campaigns.",
                reply_markup=kb,
                parse_mode="Markdown"
            )
            
        elif data == "admin_user_lookup_prompt":
            await query.edit_message_text("🔍 Send Telegram User ID or Phone Number (10 digits) to lookup:")
            context.user_data["state"] = "ADMIN_STATE_USER_LOOKUP"
            
        elif data == "admin_broadcast_prompt":
            await query.edit_message_text(
                "📊 *Create Broadcast Message*\n\n"
                "Send the message text you wish to broadcast to all registered bot users. "
                "Markdown style formatting is supported.\n\n"
                "Type `/cancel` to abort.",
                parse_mode="Markdown"
            )
            context.user_data["state"] = "ADMIN_STATE_BROADCAST_DRAFT"
            
        elif data == "admin_broadcast_confirm":
            draft = admin_session.get("broadcast_draft", "")
            if not draft:
                await query.edit_message_text("❌ Draft empty. Try again.", reply_markup=admin_dashboard_kb())
                return
            asyncio.create_task(run_broadcast_background(update, context, draft))
            
        elif data == "admin_broadcast_cancel":
            admin_session.pop("broadcast_draft", None)
            await query.edit_message_text("❌ Broadcast cancelled.", reply_markup=admin_dashboard_kb())
            
        elif data == "admin_backup_menu":
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("💾 Export Database (JSON)", callback_data="admin_export_db")],
                [InlineKeyboardButton("📥 Restore Database (Upload)", callback_data="admin_restore_prompt")],
                [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_menu")]
            ])
            await query.edit_message_text(
                "💾 *Backup & Restore Suite*\n\n"
                "Export the users database as a JSON document or restore it by uploading a backup file.",
                reply_markup=kb,
                parse_mode="Markdown"
            )
            
        elif data == "admin_restore_prompt":
            await query.edit_message_text("📥 *Upload crownit_db.json file backup:*\n\nSend the backup document to restore data.")
            context.user_data["state"] = "ADMIN_STATE_RESTORE_DB"
            
        elif data == "admin_export_db":
            try:
                await query.edit_message_text("⏳ Generating database export...")
                with open(DB_FILE, "rb") as f:
                    await context.bot.send_document(
                        chat_id=ADMIN_ID,
                        document=InputFile(f, filename="crownit_db_backup.json"),
                        caption=f"📂 *Crownit DB Backup*\n📅 Exported: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                        parse_mode="Markdown"
                    )
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text="✅ Backup sent successfully!",
                    reply_markup=admin_dashboard_kb()
                )
            except Exception as e:
                logger.error(f"Backup export error: {e}")
                await context.bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"❌ Failed to export backup: {e}",
                    reply_markup=admin_dashboard_kb()
                )
                
        elif data == "admin_config_menu":
            settings = db_data.get("settings", {})
            txt = (
                f"⚙️ *Global Survey Settings*\n\n"
                f"📋 Max Surveys / user: *{settings.get('max_surveys_per_user', 2)}*\n"
                f"⏳ Question delay: *{settings.get('min_delay', 5)} - {settings.get('max_delay', 10)} seconds*\n"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✏️ Edit Max Surveys Limit", callback_data="admin_config_limit_prompt")],
                [InlineKeyboardButton("✏️ Edit Min Delay", callback_data="admin_config_delay_min_prompt")],
                [InlineKeyboardButton("✏️ Edit Max Delay", callback_data="admin_config_delay_max_prompt")],
                [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_menu")]
            ])
            await query.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown")
            
        elif data == "admin_config_limit_prompt":
            await query.edit_message_text("✏️ Enter maximum surveys to run per session (integer):")
            context.user_data["state"] = "ADMIN_STATE_CONFIG_LIMIT"
            
        elif data == "admin_config_delay_min_prompt":
            await query.edit_message_text("✏️ Enter minimum question answering delay in seconds:")
            context.user_data["state"] = "ADMIN_STATE_CONFIG_DELAY_MIN"
            
        elif data == "admin_config_delay_max_prompt":
            await query.edit_message_text("✏️ Enter maximum question answering delay in seconds:")
            context.user_data["state"] = "ADMIN_STATE_CONFIG_DELAY_MAX"
            
        elif data == "admin_stats":
            users = db_data["users"]
            total_users = len(users)
            completed_count = sum(1 for u in users.values() if u.get("status") == "completed")
            surveys_taken = sum(u.get("surveys", 0) for u in users.values())
            rewards_claimed = sum(u.get("rewards", 0) for u in users.values())
            
            today_str = datetime.now().strftime("%Y-%m-%d")
            registered_today = 0
            for u in users.values():
                jat = u.get("joined_at", "")
                if jat and jat.startswith(today_str):
                    registered_today += 1
                    
            txt = (
                f"📈 *Extended Statistical Breakdown*\n\n"
                f"👥 Total Users registered: `{total_users}`\n"
                f"✅ Completed profiles: `{completed_count}`\n"
                f"📝 Total Surveys completed: `{surveys_taken}`\n"
                f"🎁 Total Scratch Cards claimed: `{rewards_claimed}`\n"
                f"🆕 Registered Today: `{registered_today}`"
            )
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_menu")]])
            await query.edit_message_text(txt, reply_markup=kb, parse_mode="Markdown")

# Handle User Moderation Action callback queries
async def handle_user_actions_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    action = query.data
    target_id = admin_session.get("target_user_id")
    
    if not target_id:
        await query.edit_message_text("❌ Session expired! Lookup user again.", reply_markup=admin_dashboard_kb())
        return
        
    if action == "admin_user_reset":
        if target_id in db_data["users"]:
            db_data["users"][target_id]["status"] = "joined"
            db_data["users"][target_id]["phone"] = "N/A"
            db_data["users"][target_id]["surveys"] = 0
            db_data["users"][target_id]["rewards"] = 0
            await save_db()
            await query.answer("User registration status reset!", show_alert=True)
            
    elif action == "admin_user_ban":
        uid_int = int(target_id)
        if uid_int not in db_data["banned_users"]:
            db_data["banned_users"].append(uid_int)
            await save_db()
            await query.answer("User banned successfully!", show_alert=True)
            
    elif action == "admin_user_unban":
        uid_int = int(target_id)
        if uid_int in db_data["banned_users"]:
            db_data["banned_users"].remove(uid_int)
            await save_db()
            await query.answer("User unbanned successfully!", show_alert=True)
            
    admin_session.pop("target_user_id", None)
    await query.edit_message_text("✅ User modified successfully.", reply_markup=admin_dashboard_kb())

# --- USER REGISTRATION LOGIC ---

async def handle_mobile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    mobile = re.sub(r"\D", "", update.message.text.strip())
    if len(mobile) < 10:
        await update.message.reply_text("❌ Invalid! Enter exactly 10 digits:")
        return
    mobile = mobile[-10:]
    
    msg = await update.message.reply_text("📡 Checking phone number on Crownit...")
    
    async with httpx.AsyncClient(verify=False) as client:
        # Create virtual device
        dev_resp = await crownit_post(client, "/api/devices", {
            "isDeviceRooted": "0", "macAddress": "", "campaignType": "na",
            "manufacturerName": "Unknown", "deviceVersion": "PWA",
            "modelNo": "PWA", "deviceId": "00000"
        })
        reg_id = dev_resp.get("id", "10375346")
        
        # Init User Registration / Check existing user on Crownit
        user_resp = await crownit_post(client, "/api/users", {
            "phoneNo": mobile, "deviceId": "00000", "registrationStatusId": reg_id
        })
        
    if user_resp.get("responseCode") != 1:
        await msg.edit_text("❌ *Crownit API Registration Failed.* Phone number check standard criteria or invalid response.", parse_mode="Markdown")
        context.user_data["state"] = None
        return
        
    ud = user_resp.get("userDetails", {})
    uid = ud.get("id")
    
    # Smart check if the number is already registered directly on Crownit (profile has name, gender, or city already filled out)
    is_existing = bool(ud.get("name") or ud.get("cityId") or ud.get("email") or ud.get("gender") or ud.get("city"))
    
    # Save parameters to user session context
    context.user_data["pending_register"] = {"phone": mobile, "uid": uid, "reg_id": reg_id}
    
    if is_existing:
        # OTP has already been sent by Crownit, show prompt asking if they want to continue
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Yes, Continue", callback_data="dup_continue")],
            [InlineKeyboardButton("❌ No, Cancel", callback_data="dup_cancel")]
        ])
        await msg.edit_text(
            f"⚠️ *Already Registered on Crownit!*\n\n"
            f"The phone number `+91{mobile}` is already registered in Crownit.\n\n"
            f"Do you want to continue anyway?",
            reply_markup=kb,
            parse_mode="Markdown"
        )
        context.user_data["state"] = None
    else:
        # User is new, OTP was sent, prompt to input it directly
        await msg.edit_text(f"✅ OTP sent to +91{mobile}\n\n📩 *Enter OTP code received:*", parse_mode="Markdown")
        context.user_data["state"] = "WAITING_FOR_OTP"

async def handle_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    otp = update.message.text.strip()
    if not otp.isdigit() or len(otp) < 4:
        await update.message.reply_text("❌ Invalid OTP! Enter digits only:")
        return
        
    session = context.user_data.get("pending_register")
    if not session:
        await update.message.reply_text("❌ Registration session expired or not found. Try /start again.")
        context.user_data["state"] = None
        return
        
    phone, uid, reg_id = session["phone"], session["uid"], session["reg_id"]
    msg = await update.message.reply_text("🔐 Verifying OTP on server...")
    
    async with httpx.AsyncClient(verify=False) as client:
        verify = await crownit_put(client, f"/api/users/{phone}/otp", {
            "phoneNo": phone, "deviceId": "00000", "registrationStatusId": reg_id,
            "otp": otp, "userId": phone, "api_version": "71"
        })
        
        if verify.get("responseCode") != 1:
            await msg.edit_text("❌ Invalid OTP! Verification failed.")
            return
            
        ud = verify.get("userDetails", {})
        sid = ud.get("sessionId")
        
        # Clear temporary pending OTP session
        context.user_data.pop("pending_register", None)
        context.user_data["state"] = None
        
        # Save credentials for recovery retries
        active_sessions[user_id] = {"uid": uid, "sid": sid, "phone": phone}
        
        # Set up profile details on Crownit
        await msg.edit_text("✅ Verification successful!\n📍 Setting up profile and city metadata...")
        await crownit_put(client, "/api/user/profile", {"city": "Bihar Sharif", "cityId": 1134}, uid, sid)
        await crownit_post(client, "/api/user/milestone", {}, uid, sid)
        
    # Launch automated campaign surveys
    await run_surveys_and_claims(msg, user_id, uid, sid, phone)

# --- ADMIN ACTIONS TEXT INPUT PROCESSING ---

async def handle_admin_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if "|" not in text:
        await update.message.reply_text("❌ Invalid format! Please include the '|' character. E.g. `@my_channel|https://t.me/my_channel`")
        return
        
    parts = text.split("|")
    username = parts[0].strip()
    link = parts[1].strip()
    
    if not username.startswith("@"):
        await update.message.reply_text("❌ Channel username must start with '@'. Try again:")
        return
        
    db_data["channels"].append({"username": username, "link": link})
    await save_db()
    context.user_data["state"] = None
    
    await update.message.reply_text(f"✅ Added {username} successfully!", reply_markup=admin_dashboard_kb())

async def handle_admin_user_lookup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_str = update.message.text.strip()
    found_user = None
    found_id = None
    
    if query_str in db_data["users"]:
        found_user = db_data["users"][query_str]
        found_id = query_str
    else:
        for uid, u in db_data["users"].items():
            if u.get("phone") == query_str:
                found_user = u
                found_id = uid
                break
                
    if not found_user:
        await update.message.reply_text("❌ No user matching that ID or Phone was found.", reply_markup=admin_dashboard_kb())
        context.user_data["state"] = None
        return
        
    admin_session["target_user_id"] = found_id
    context.user_data["state"] = None
    
    is_banned = int(found_id) in db_data.get("banned_users", [])
    ban_lbl = "Unban User" if is_banned else "Ban User"
    ban_cb = "admin_user_unban" if is_banned else "admin_user_ban"
    
    txt = (
        f"👤 *User Profile Details (ID: {found_id})*\n\n"
        f"📱 Phone: `{found_user.get('phone', 'N/A')}`\n"
        f"👤 Name: *{found_user.get('name', 'N/A')}*\n"
        f"⚧ Gender: {found_user.get('gender', 'N/A')}\n"
        f"📅 DOB: {found_user.get('dob', 'N/A')}\n"
        f"📊 Status: *{found_user.get('status', 'N/A')}*\n"
        f"📝 Surveys Answered: `{found_user.get('surveys', 0)}`\n"
        f"🎁 Rewards Claimed: `{found_user.get('rewards', 0)}`\n"
        f"🕒 Joined: `{found_user.get('joined_at', 'N/A')}`\n"
        f"🚫 Account Status: *{'Banned' if is_banned else 'Active'}*"
    )
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Reset User Status", callback_data="admin_user_reset")],
        [InlineKeyboardButton(f"🚫 {ban_lbl}", callback_data=ban_cb)],
        [InlineKeyboardButton("⬅️ Back to Admin Panel", callback_data="admin_menu")]
    ])
    await update.message.reply_text(txt, reply_markup=kb, parse_mode="Markdown")

async def handle_admin_broadcast_draft(update: Update, context: ContextTypes.DEFAULT_TYPE):
    draft = update.message.text
    admin_session["broadcast_draft"] = draft
    context.user_data["state"] = None
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm & Send", callback_data="admin_broadcast_confirm")],
        [InlineKeyboardButton("❌ Cancel", callback_data="admin_broadcast_cancel")]
    ])
    await update.message.reply_text(
        f"📊 *Broadcast Preview:*\n---\n{draft}\n---\nDo you want to send this message to all users?",
        reply_markup=kb,
        parse_mode="Markdown"
    )

async def run_broadcast_background(update: Update, context: ContextTypes.DEFAULT_TYPE, message_text: str):
    """Sends broadcast messages in background showing live tracking of counts."""
    status_msg = await context.bot.send_message(
        chat_id=ADMIN_ID,
        text="📡 *Broadcast Initialized...*\nSending to users: 0% completed."
    )
    
    success = 0
    failed = 0
    users_ids = list(db_data["users"].keys())
    total_users = len(users_ids)
    
    if total_users == 0:
        await status_msg.edit_text("❌ No registered users to broadcast to.")
        return
        
    for idx, uid in enumerate(users_ids, 1):
        try:
            await context.bot.send_message(chat_id=int(uid), text=message_text, parse_mode="Markdown")
            success += 1
        except Exception as e:
            logger.debug(f"Broadcast fail for user {uid}: {e}")
            failed += 1
            
        if idx % 10 == 0 or idx == total_users:
            percent = int((idx / total_users) * 100)
            try:
                await status_msg.edit_text(
                    f"📡 *Broadcast Progress:*\n\n"
                    f"📊 Status: `{percent}%` done\n"
                    f"✅ Success: `{success}`\n"
                    f"❌ Failed/Blocked: `{failed}`\n"
                    f"👥 Checked: `{idx}/{total_users}`",
                    parse_mode="Markdown"
                )
            except Exception:
                pass
        await asyncio.sleep(0.05)
        
    await context.bot.send_message(
        chat_id=ADMIN_ID,
        text=f"📢 *Broadcast Job Complete!*\n\n✅ Delivered: `{success}`\n❌ Failed: `{failed}`",
        reply_markup=admin_dashboard_kb(),
        parse_mode="Markdown"
    )

async def handle_admin_config_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Please enter a valid positive integer:")
        return
        
    limit = int(text)
    db_data["settings"]["max_surveys_per_user"] = limit
    await save_db()
    context.user_data["state"] = None
    await update.message.reply_text(f"✅ Max surveys limit set to: {limit}", reply_markup=admin_dashboard_kb())

async def handle_admin_config_delay_min(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Please enter a valid integer for delay:")
        return
        
    val = int(text)
    db_data["settings"]["min_delay"] = val
    await save_db()
    context.user_data["state"] = None
    await update.message.reply_text(f"✅ Min delay set to: {val} seconds", reply_markup=admin_dashboard_kb())

async def handle_admin_config_delay_max(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if not text.isdigit():
        await update.message.reply_text("❌ Please enter a valid integer for delay:")
        return
        
    val = int(text)
    db_data["settings"]["max_delay"] = val
    await save_db()
    context.user_data["state"] = None
    await update.message.reply_text(f"✅ Max delay set to: {val} seconds", reply_markup=admin_dashboard_kb())

async def handle_admin_restore_db(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc.file_name.endswith(".json"):
        await update.message.reply_text("❌ Upload failed. File must be a JSON document (.json).")
        return
        
    msg = await update.message.reply_text("📥 Downloading backup file...")
    file_obj = await context.bot.get_file(doc.file_id)
    content = await file_obj.download_as_bytearray()
    
    try:
        data = json.loads(content.decode("utf-8"))
        for key in ["users", "channels", "settings"]:
            if key not in data:
                raise ValueError(f"Missing key '{key}' in JSON layout.")
                
        db_data.update(data)
        await save_db()
        context.user_data["state"] = None
        await msg.edit_text("✅ *Database restored successfully!*", reply_markup=admin_dashboard_kb(), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Failed to restore DB: {e}")
        await msg.edit_text(f"❌ Failed to parse or restore database structure: {e}", reply_markup=admin_dashboard_kb())

# Message router to handle text states dynamically without ConversationHandler
async def handle_text_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    
    # Banned list check
    if user_id in db_data.get("banned_users", []):
        await update.message.reply_text("❌ *You are banned from using this bot.*", parse_mode="Markdown")
        return
        
    state = context.user_data.get("state")
    text = update.message.text.strip()
    
    if text.startswith("/"):
        return # Skip slash command inputs
        
    # Route text input dynamically
    if state == "WAITING_FOR_MOBILE":
        await handle_mobile(update, context)
    elif state == "WAITING_FOR_OTP":
        await handle_otp(update, context)
    elif state == "ADMIN_STATE_ADD_CHANNEL" and user_id == ADMIN_ID:
        await handle_admin_add_channel(update, context)
    elif state == "ADMIN_STATE_USER_LOOKUP" and user_id == ADMIN_ID:
        await handle_admin_user_lookup(update, context)
    elif state == "ADMIN_STATE_BROADCAST_DRAFT" and user_id == ADMIN_ID:
        await handle_admin_broadcast_draft(update, context)
    elif state == "ADMIN_STATE_CONFIG_LIMIT" and user_id == ADMIN_ID:
        await handle_admin_config_limit(update, context)
    elif state == "ADMIN_STATE_CONFIG_DELAY_MIN" and user_id == ADMIN_ID:
        await handle_admin_config_delay_min(update, context)
    elif state == "ADMIN_STATE_CONFIG_DELAY_MAX" and user_id == ADMIN_ID:
        await handle_admin_config_delay_max(update, context)
    else:
        # Default start menu routing
        await cmd_start(update, context)

# Document message router
async def handle_document_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    state = context.user_data.get("state")
    
    if user_id == ADMIN_ID and state == "ADMIN_STATE_RESTORE_DB":
        await handle_admin_restore_db(update, context)
    else:
        await update.message.reply_text("⚠️ Unknown document format received.")

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    context.user_data["state"] = None
    context.user_data.pop("pending_register", None)
    admin_session.pop("broadcast_draft", None)
    admin_session.pop("target_user_id", None)
    
    await update.message.reply_text(
        "✅ Operation cancelled.", 
        reply_markup=admin_dashboard_kb() if user_id == ADMIN_ID else user_menu_kb()
    )

# Lightweight, non-blocking HTTP health check server for Render Web Services
def run_health_check_server(port):
    class HealthHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path in ("/", "/health"):
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"OK")
            else:
                self.send_response(404)
                self.end_headers()
                
        def log_message(self, format, *args):
            pass
            
    def start_listening():
        try:
            socketserver.TCPServer.allow_reuse_address = True
            with socketserver.TCPServer(("0.0.0.0", port), HealthHandler) as httpd:
                logger.info(f"Render health check helper server listening on port {port}")
                httpd.serve_forever()
        except Exception as e:
            logger.error(f"Health server failed: {e}")
            
    t = threading.Thread(target=start_listening, daemon=True)
    t.start()

def main():
    if not BOT_TOKEN:
        print("CRITICAL ERROR: BOT_TOKEN is not defined in environment variables.")
        return
        
    # Start health check server
    run_health_check_server(PORT)
    
    # Initialize Application
    app = Application.builder().token(BOT_TOKEN).build()
    
    # User Action callbacks (Ban/Reset callbacks)
    app.add_handler(CallbackQueryHandler(handle_user_actions_callbacks, pattern="^admin_user_(reset|ban|unban)$"))
    
    # Unified Callback Handler for all routing (never gets stuck/laggy)
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    
    # Document Handler
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document_messages))
    
    # Command Handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    
    # Text Message Handler Router (checks state variables dynamically)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_messages))
    
    print("🤖 Crownit Bot v6.2 Starting - Async Engine and Zero-Lag State Router Ready.")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
