import os
import random
import time
import logging
import threading
import dbm  # Urinishlarni bot o'chsa ham eslab qolish uchun baza
from flask import Flask

import telebot
from telebot import types

from words import WORDS_DATA

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8873408406:AAFRKOy6SXymeQsDWfejccJnSfZQ8_dcLP4")
TEACHER_CHAT_ID = 6455710768

USER_DATABASE = {
    "theend70": {"name": "MsEzoza", "role": "Teacher"},
    "minotaur1": {"name": "Ahmad", "role": "Student"},
    "mlbbbbnn": {"name": "Adham", "role": "Student"},
}

TEACHER_CHAT_IDS = {6455710768}
QUESTIONS_PER_TEST = 30    # Savollar soni eski holatida qoldi (30 ta)
STUDENT_ATTEMPT_LIMIT = 3  # O'quvchilar uchun har bir unitga 3 ta urinish
TEACHER_ATTEMPT_LIMIT = 10 # O'qituvchi uchun har bir unitga 10 ta urinish
QUESTION_TIMEOUT = 8.0     # Har bir savolga beriladigan vaqt (soniya)

# ---------------------------------------------------------------------------
# Logging & Bot Initialization
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

user_states: dict[int, dict] = {}
UNITS = list(WORDS_DATA.keys())

# ---------------------------------------------------------------------------
# Persistent Attempts DB Helpers (Urinishlarni saqlash tizimi)
# ---------------------------------------------------------------------------
DB_FILE = "user_attempts_db"

def get_attempts_count(user_id: int, unit: str) -> int:
    """Foydalanuvchining ma'lum bir unit bo'yicha urinishlar sonini qaytaradi"""
    key = f"{user_id}:{unit}"
    try:
        with dbm.open(DB_FILE, "c") as db:
            if key.encode() in db:
                return int(db[key.encode()].decode())
    except Exception as e:
        logger.error("DB read error: %s", e)
    return 0

def increment_attempts_count(user_id: int, unit: str) -> int:
    """Urinishlar sonini 1 taga oshiradi va bazaga yozib qo'yadi"""
    key = f"{user_id}:{unit}"
    current = get_attempts_count(user_id, unit) + 1
    try:
        with dbm.open(DB_FILE, "c") as db:
            db[key.encode()] = str(current).encode()
    except Exception as e:
        logger.error("DB write error: %s", e)
    return current

# ---------------------------------------------------------------------------
# Flask Web Server for Render Keep-Alive
# ---------------------------------------------------------------------------
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is Active!", 200

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

# ---------------------------------------------------------------------------
# Core Helpers & Test Logic
# ---------------------------------------------------------------------------
def get_user_record(user: types.User) -> dict | None:
    if user.id in TEACHER_CHAT_IDS:
        return {"name": "MsEzoza", "role": "Teacher"}
    if user.username:
        return USER_DATABASE.get(user.username.lower())
    return None

def is_allowed(user: types.User) -> bool:
    return get_user_record(user) is not None

def get_user_name(user: types.User) -> str:
    record = get_user_record(user)
    return record["name"] if record else "Noma'lum"

def get_unit_wrong_pool(unit: str, correct_answer: str, target_lang: str) -> list[str]:
    unit_words = list(WORDS_DATA[unit].items())
    if target_lang == "uzbek":
        pool = [u for e, u in unit_words if u != correct_answer]
    else:
        pool = [e for e, u in unit_words if e != correct_answer]
    return pool

def get_all_wrong_pool(correct_answer: str, target_lang: str) -> list[str]:
    pool = []
    for words in WORDS_DATA.values():
        for e, u in words.items():
            if target_lang == "uzbek":
                if u != correct_answer:
                    pool.append(u)
            else:
                if e != correct_answer:
                    pool.append(e)
    return pool

def build_choices(unit: str, correct_answer: str, target_lang: str) -> list[str]:
    wrong_pool = get_unit_wrong_pool(unit, correct_answer, target_lang)
    wrong_pool = list(dict.fromkeys(wrong_pool))
    wrong_pool = [w for w in wrong_pool if w != correct_answer]

    if len(wrong_pool) < 3:
        all_pool = get_all_wrong_pool(correct_answer, target_lang)
        all_pool = list(dict.fromkeys(all_pool))
        all_pool = [w for w in all_pool if w != correct_answer and w not in wrong_pool]
        random.shuffle(all_pool)
        wrong_pool += all_pool

    if len(wrong_pool) >= 3:
        wrong = random.sample(wrong_pool, 3)
    else:
        wrong = wrong_pool
        while len(wrong) < 3:
            wrong.append(correct_answer)

    choices = wrong + [correct_answer]
    random.shuffle(choices)
    return choices

def build_test_questions(unit: str, count: int = QUESTIONS_PER_TEST) -> list[dict]:
    words = list(WORDS_DATA[unit].items())
    if len(words) < count:
        count = len(words)

    selected = random.sample(words, count)
    questions = []

    for english, uzbek in selected:
        direction = random.choice(["en_to_uz", "uz_to_en"])

        if direction == "en_to_uz":
            question_text = english
            correct_answer = uzbek
            choices = build_choices(unit, correct_answer, "uzbek")
        else:
            question_text = uzbek
            correct_answer = english
            choices = build_choices(unit, correct_answer, "english")

        questions.append({
            "question_text": question_text,
            "correct_answer": correct_answer,
            "choices": choices,
            "direction": direction,
            "english": english,
            "uzbek": uzbek,
        })
    return questions

def build_inline_markup(choices: list[str]) -> types.InlineKeyboardMarkup:
    labels = ["A", "B", "C", "D"]
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    for i, label in enumerate(labels):
        buttons.append(types.InlineKeyboardButton(f"{label}) {choices[i]}", callback_data=f"ans_{i}"))
    markup.add(*buttons)
    return markup

def send_main_menu(chat_id: int, text: str = "Unitni tanlang:") -> None:
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    buttons = [types.KeyboardButton(unit) for unit in UNITS]
    markup.add(*buttons)
    bot.send_message(chat_id, text, reply_markup=markup)

def send_question_edit(chat_id: int, message_id: int, state: dict) -> None:
    idx = state["index"]
    questions = state["questions"]
    if idx >= len(questions):
        return

    q = questions[idx]
    total = len(questions)
    progress = f"[{idx + 1}/{total}]"

    text = f"{progress} So'zni toping:\n\n<b>{q['question_text']}</b>\n\n⏱ <i>Vaqt: {int(QUESTION_TIMEOUT)} soniya</i>"
    markup = build_inline_markup(q["choices"])

    try:
        bot.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=markup)
    except Exception as e:
        logger.error("Failed to edit question message: %s", e)
        msg = bot.send_message(chat_id, text, reply_markup=markup)
        state["message_id"] = msg.message_id
    
    start_question_timer(chat_id, state["user_id"])

def send_question_new(chat_id: int, state: dict) -> int:
    idx = state["index"]
    questions = state["questions"]
    total = len(questions)
    progress = f"[{idx + 1}/{total}]"

    q = questions[idx]
    text = f"{progress} So'zni toping:\n\n<b>{q['question_text']}</b>\n\n⏱ <i>Vaqt: {int(QUESTION_TIMEOUT)} soniya</i>"
    markup = build_inline_markup(q["choices"])

    msg = bot.send_message(chat_id, text, reply_markup=markup)
    return msg.message_id

def finish_test(chat_id: int, user_id: int) -> None:
    state = user_states.pop(user_id, {})
    if "timer" in state and state["timer"]:
        state["timer"].cancel()

    correct = state.get("correct", 0)
    incorrect = state.get("incorrect", 0)
    unit = state.get("unit", "?")
    attempt = state.get("attempt", 1)
    wrong_words = state.get("wrong_words", [])
    total_questions = len(state.get("questions", []))

    user_info = state.get("user_info", {})
    db_name = user_info.get("db_name", "Noma'lum")
    username = user_info.get("username", "yo'q")

    result_text = (
        f"✅ Test yakunlandi!\n\n"
        f"Unit: <b>{unit}</b>\n"
        f"Urinish: <b>{attempt}</b>\n"
        f"To'g'ri: <b>{correct}</b> / {total_questions}\n"
        f"Noto'g'ri: <b>{incorrect}</b> / {total_questions}\n\n"
        f"Natijangiz tekshirish uchun muvaffaqiyatli tarzda ustozga yuborildi! 🤝"
    )
    send_main_menu(chat_id, result_text)

    if wrong_words:
        wrong_lines = "\n".join(f"{i + 1}. {w}" for i, w in enumerate(wrong_words))
    else:
        wrong_lines = "Xatolar mavjud emas! Mukammal!"

    teacher_report = (
        f"📊 Yangi test natijalari\n\n"
        f"👤 Ism: {db_name}\n"
        f"🔗 Username: @{username}\n"
        f"📚 Unit: {unit}\n"
        f"🔢 Urinish: {attempt}\n"
        f"✅ To'g'ri: {correct} / {total_questions}\n"
        f"❌ Noto'g'ri: {incorrect} / {total_questions}\n\n"
        f"❌ Xato qilingan so'zlar ro'yxati:\n"
        f"{wrong_lines}"
    )

    try:
        bot.send_message(TEACHER_CHAT_ID, teacher_report)
    except Exception as e:
        logger.error("Could not send report to teacher: %s", e)

# ---------------------------------------------------------------------------
# Auto-Timeout Logic (8 soniya tugaganda)
# ---------------------------------------------------------------------------
def handle_question_timeout(chat_id: int, user_id: int, current_index: int) -> None:
    state = user_states.get(user_id)
    if not state or state["index"] != current_index:
        return

    q = state["questions"][state["index"]]
    state["incorrect"] += 1
    
    wrong_entry = f"{q['question_text']} — (⏱ Vaqt tugadi! To'g'ri javob: {q['correct_answer']})"
    state["wrong_words"].append(wrong_entry)

    try:
        bot.send_message(chat_id, f"⏳ <b>{q['question_text']}</b> so'ziga vaqtingiz tugadi! Noto'g'ri deb hisoblandi.")
    except Exception:
        pass

    state["index"] += 1

    if state["index"] >= len(state["questions"]):
        finish_test(chat_id, user_id)
    else:
        send_question_edit(chat_id, state["message_id"], state)

def start_question_timer(chat_id: int, user_id: int) -> None:
    state = user_states.get(user_id)
    if not state:
        return

    if "timer" in state and state["timer"]:
        state["timer"].cancel()

    t = threading.Timer(
        QUESTION_TIMEOUT, 
        handle_question_timeout, 
        args=[chat_id, user_id, state["index"]]
    )
    state["timer"] = t
    t.start()

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------
@bot.message_handler(commands=["start", "help"])
def handle_start(message: types.Message) -> None:
    if not is_allowed(message.from_user):
        bot.send_message(message.chat.id, "Ushbu bot faqat maxsus foydalanuvchilar uchun xizmat ko'rsatadi.")
        return

    db_name = get_user_name(message.from_user)
    if db_name == "MsEzoza":
        send_main_menu(
            message.chat.id,
            "👋 Xush kelibsiz, ustoz MsEzoza\n\nO'quvchilar test topshirganida natijalar sizga avtomatik yuboriladi.\n\nSiz ham test yechmoqchimisiz? Unitni tanlang:",
        )
    else:
        send_main_menu(message.chat.id, f"Salom, <b>{db_name}</b>! 👋\nQaysi unitni test qilmoqchisiz?")

@bot.message_handler(func=lambda m: m.text in UNITS)
def handle_unit_selection(message: types.Message) -> None:
    if not is_allowed(message.from_user):
        bot.send_message(message.chat.id, "Ushbu bot faqat maxsus foydalanuvchilar uchun xizmat ko'rsatadi.")
        return

    unit = message.text
    user_id = message.from_user.id
    chat_id = message.chat.id

    if not WORDS_DATA[unit]:
        bot.send_message(chat_id, "Bu unit testlari hozircha mavjud emas.")
        send_main_menu(chat_id)
        return

    user_record = get_user_record(message.from_user)
    is_teacher = user_record and user_record.get("role") == "Teacher"

    # Har bir foydalanuvchining statusiga qarab limitni aniqlash
    limit = TEACHER_ATTEMPT_LIMIT if is_teacher else STUDENT_ATTEMPT_LIMIT
    
    # Doimiy bazadan urinishlar sonini tekshirish
    used_attempts = get_attempts_count(user_id, unit)
    if used_attempts >= limit:
        bot.send_message(
            chat_id, 
            f"Siz <b>{unit}</b> uchun ajratilgan maksimal <b>{limit} ta</b> urinishdan foydalanib bo'ldingiz! ❌\nBoshqa topshira olmaysiz."
        )
        send_main_menu(chat_id)
        return

    # Urinishlar sonini 1 taga oshirish
    attempt_num = increment_attempts_count(user_id, unit)

    questions = build_test_questions(unit, QUESTIONS_PER_TEST)
    db_name = user_record["name"] if user_record else "Noma'lum"
    username = message.from_user.username or "yo'q"

    user_states[user_id] = {
        "user_id": user_id,
        "unit": unit,
        "questions": questions,
        "index": 0,
        "correct": 0,
        "incorrect": 0,
        "wrong_words": [],
        "attempt": attempt_num,
        "message_id": None,
        "timer": None,
        "user_info": {
            "db_name": db_name,
            "username": username,
            "full_name": message.from_user.full_name,
        },
    }

    bot.send_message(chat_id, f"<b>{unit}</b> testi boshlanmoqda... Har bir savolga {int(QUESTION_TIMEOUT)} soniya beriladi! 🚀", reply_markup=types.ReplyKeyboardRemove())
    msg_id = send_question_new(chat_id, user_states[user_id])
    user_states[user_id]["message_id"] = msg_id
    
    start_question_timer(chat_id, user_id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("ans_"))
def handle_inline_answer(call: types.CallbackQuery) -> None:
    if not is_allowed(call.from_user):
        bot.answer_callback_query(call.id, "Ruxsat berilmagan.")
        return

    user_id = call.from_user.id
    chat_id = call.message.chat.id
    state = user_states.get(user_id)

    if not state or not state.get("questions"):
        bot.answer_callback_query(call.id, "Xatolik yuz berdi yoki test vaqti tugagan.")
        return

    if "timer" in state and state["timer"]:
        state["timer"].cancel()

    idx = int(call.data.split("_")[1])
    
    if state["index"] >= len(state["questions"]):
        bot.answer_callback_query(call.id, "Test yakunlangan.")
        return

    q = state["questions"][state["index"]]
    user_answer = q["choices"][idx]
    correct = user_answer == q["correct_answer"]

    if correct:
        state["correct"] += 1
        feedback = "✅ To'g'ri!"
    else:
        state["incorrect"] += 1
        wrong_entry = f"{q['question_text']} — {user_answer} (To'g'ri javob: {q['correct_answer']})"
        state["wrong_words"].append(wrong_entry)
        feedback = "❌ Noto'g'ri!"

    bot.answer_callback_query(call.id, feedback)
    state["index"] += 1

    if state["index"] >= len(state["questions"]):
        finish_test(chat_id, user_id)
    else:
        send_question_edit(chat_id, state["message_id"], state)

@bot.message_handler(func=lambda m: True)
def handle_unknown(message: types.Message) -> None:
    if not is_allowed(message.from_user):
        bot.send_message(message.chat.id, "Ushbu bot faqat maxsus foydalanuvchilar uchun xizmat ko'rsatadi.")
        return

    if message.from_user.id in user_states:
        bot.send_message(message.chat.id, "Iltimos, testni davom ettirish uchun savol ostidagi inline tugmalardan foydalaning.")
        return

    send_main_menu(message.chat.id, "Iltimos, menyudan unitni tanlang.")

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info("Starting Flask keep-alive server...")
    threading.Thread(target=run_flask, daemon=True).start()

    logger.info("Bot started. Polling...")
    while True:
        try:
            bot.infinity_polling(timeout=30, long_polling_timeout=20)
        except Exception as e:
            logger.error("Polling crashed: %s — restarting in 5 seconds...", e)
            time.sleep(5)
