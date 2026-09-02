"""
Hunter J087 Telegram Bot
-------------------------
Контроль чергування "хантера" (підхід до покупців) у групі магазину.

Команди:
  /chatid                 — показати ID групи (виконати один раз у групі, щоб зареєструвати її)
  /employees               — показати список працівників
  /addemployee Ім'я        — додати працівника
  /removeemployee Ім'я     — видалити працівника
  /sethunters Ім'я1, Ім'я2 — призначити хантерів на сьогодні (робити зранку)
  /todayhunters             — хто сьогодні в пулі хантерів
  /starthunter Ім'я         — почати зміну хантера
  /transferhunter Ім'я      — передати зміну іншому
  /endhunter                — завершити зміну
  /status                   — поточний статус
  /setinterval Години       — інтервал перевірки "ти на місці?" (за замовч. 2)
  /setactivity Текст        — поточна активність/акція, буде в нагадуваннях
  /help                     — список команд
"""

import json
import logging
import os
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hunter-bot")

TZ = ZoneInfo("Europe/Kyiv")
STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

DEFAULT_STATE = {
    "chat_id": None,
    "employees": ["Владислав", "Іван", "Олександр", "Діана", "Тетяна"],
    "today_hunters": [],
    "today_date": None,
    "current_session": None,   # {"employee":..., "start_time":..., "last_check_in":..., "transfers":[...]}
    "log": [],
    "interval_hours": 2,
    "activity_text": "Активність не вказана",
    "last_missing_alert": None,
}


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                merged = {**DEFAULT_STATE, **data}
                return merged
        except Exception as e:
            log.error("Failed to load state: %s", e)
    return dict(DEFAULT_STATE)


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error("Failed to save state: %s", e)


STATE = load_state()


def now_iso():
    return datetime.now(TZ).isoformat()


def parse_iso(s):
    return datetime.fromisoformat(s)


def fmt_time(iso_str):
    return parse_iso(iso_str).strftime("%H:%M")


def fmt_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}год {m}хв"
    return f"{m}хв {s}с"


def today_str():
    return datetime.now(TZ).strftime("%Y-%m-%d")


def ensure_today_reset():
    """Скидає пул хантерів дня, якщо настав новий день."""
    if STATE.get("today_date") != today_str():
        STATE["today_date"] = today_str()
        STATE["today_hunters"] = []
        save_state(STATE)


async def require_chat(update: Update) -> bool:
    if STATE["chat_id"] is None:
        await update.message.reply_text(
            "Спочатку виконай /chatid у робочій групі, щоб я знав, куди писати."
        )
        return False
    return True


# ---------------- команди ----------------

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(__doc__)


async def cmd_chatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    STATE["chat_id"] = update.effective_chat.id
    save_state(STATE)
    await update.message.reply_text(f"✅ Групу зареєстровано (chat_id={update.effective_chat.id})")


async def cmd_employees(update: Update, context: ContextTypes.DEFAULT_TYPE):
    names = "\n".join(f"• {e}" for e in STATE["employees"]) or "Список порожній"
    await update.message.reply_text(f"Працівники:\n{names}")


async def cmd_addemployee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args).strip()
    if not name:
        await update.message.reply_text("Використання: /addemployee Ім'я")
        return
    if name not in STATE["employees"]:
        STATE["employees"].append(name)
        save_state(STATE)
    await update.message.reply_text(f"✅ Додано: {name}")


async def cmd_removeemployee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args).strip()
    if name in STATE["employees"]:
        STATE["employees"].remove(name)
        save_state(STATE)
        await update.message.reply_text(f"🗑 Видалено: {name}")
    else:
        await update.message.reply_text("Такого працівника немає в списку.")


async def cmd_sethunters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_today_reset()
    text = " ".join(context.args)
    names = [n.strip() for n in text.split(",") if n.strip()]
    unknown = [n for n in names if n not in STATE["employees"]]
    if unknown:
        await update.message.reply_text(
            f"⚠️ Немає в списку працівників: {', '.join(unknown)}\n"
            f"Спочатку додай через /addemployee, або перевір написання."
        )
        return
    STATE["today_hunters"] = names
    STATE["today_date"] = today_str()
    STATE["last_missing_alert"] = None
    save_state(STATE)
    await update.message.reply_text(
        f"✅ Хантери на сьогодні ({today_str()}): {', '.join(names) if names else '—'}"
    )


async def cmd_todayhunters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_today_reset()
    names = ", ".join(STATE["today_hunters"]) or "ще не призначені"
    await update.message.reply_text(f"Хантери на сьогодні: {names}")


async def cmd_setactivity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text("Використання: /setactivity Дні меблів, знижки до 60%")
        return
    STATE["activity_text"] = text
    save_state(STATE)
    await update.message.reply_text(f"✅ Активність оновлено: {text}")


async def cmd_setinterval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        hours = float(context.args[0])
        if hours <= 0:
            raise ValueError
    except (IndexError, ValueError):
        await update.message.reply_text("Використання: /setinterval 2  (кількість годин)")
        return
    STATE["interval_hours"] = hours
    save_state(STATE)
    await update.message.reply_text(f"✅ Інтервал перевірки: кожні {hours} год")


async def cmd_starthunter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_today_reset()
    if not await require_chat(update):
        return
    name = " ".join(context.args).strip()
    if not name:
        await update.message.reply_text("Використання: /starthunter Ім'я")
        return
    if STATE["today_hunters"] and name not in STATE["today_hunters"]:
        await update.message.reply_text(
            f"⚠️ {name} не в сьогоднішньому списку хантерів ({', '.join(STATE['today_hunters'])}).\n"
            f"Онови через /sethunters, якщо потрібно."
        )
        return
    if STATE["current_session"]:
        await update.message.reply_text(
            f"Hunter вже активний ({STATE['current_session']['employee']}). "
            f"Спочатку /endhunter або /transferhunter."
        )
        return
    ts = now_iso()
    STATE["current_session"] = {
        "employee": name, "start_time": ts, "last_check_in": ts, "transfers": []
    }
    STATE["last_missing_alert"] = None
    save_state(STATE)
    await update.message.reply_text(f"🟢 Hunter почав: {name} ({fmt_time(ts)})")


async def cmd_transferhunter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not STATE["current_session"]:
        await update.message.reply_text("Зараз немає активного Hunter.")
        return
    name = " ".join(context.args).strip()
    if not name:
        await update.message.reply_text("Використання: /transferhunter Ім'я")
        return
    ts = now_iso()
    STATE["current_session"]["transfers"].append(
        {"from": STATE["current_session"]["employee"], "to": name, "time": ts}
    )
    STATE["current_session"]["employee"] = name
    STATE["current_session"]["last_check_in"] = ts
    save_state(STATE)
    await update.message.reply_text(f"🔄 Hunter передано: {name} ({fmt_time(ts)})")


async def cmd_endhunter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = STATE["current_session"]
    if not session:
        await update.message.reply_text("Зараз немає активного Hunter.")
        return
    end_ts = now_iso()
    duration = (parse_iso(end_ts) - parse_iso(session["start_time"])).total_seconds()
    STATE["log"].insert(0, {**session, "end_time": end_ts})
    STATE["current_session"] = None
    save_state(STATE)
    await update.message.reply_text(
        f"🔴 Hunter завершено. Тривалість: {fmt_duration(duration)}"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_today_reset()
    session = STATE["current_session"]
    if not session:
        hunters = ", ".join(STATE["today_hunters"]) or "не призначені"
        await update.message.reply_text(
            f"НЕАКТИВНО — Hunter зараз не працює.\nСьогоднішні хантери: {hunters}"
        )
        return
    elapsed = (datetime.now(TZ) - parse_iso(session["last_check_in"])).total_seconds()
    await update.message.reply_text(
        f"АКТИВНО — {session['employee']}\n"
        f"З моменту останньої перевірки: {fmt_duration(elapsed)}\n"
        f"Зміна триває з: {fmt_time(session['start_time'])}\n"
        f"Активність: {STATE['activity_text']}"
    )


# ---------------- callback (кнопка "я на місці") ----------------

async def on_checkin_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    session = STATE["current_session"]
    if session:
        session["last_check_in"] = now_iso()
        save_state(STATE)
        await query.edit_message_text(f"✅ Підтверджено, {session['employee']} на місці.")
    else:
        await query.edit_message_text("Hunter вже не активний.")


# ---------------- фонові перевірки ----------------

async def periodic_check(context: ContextTypes.DEFAULT_TYPE):
    ensure_today_reset()
    if STATE["chat_id"] is None:
        return
    chat_id = STATE["chat_id"]
    session = STATE["current_session"]

    if session:
        elapsed = (datetime.now(TZ) - parse_iso(session["last_check_in"])).total_seconds()
        threshold = STATE["interval_hours"] * 3600
        if elapsed >= threshold:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("✅ Так, я на місці", callback_data="checkin")]]
            )
            await context.bot.send_message(
                chat_id=chat_id,
                text=(
                    f"⏰ {session['employee']}, ти на місці?\n"
                    f"Активність: {STATE['activity_text']}"
                ),
                reply_markup=keyboard,
            )
            # позначаємо момент відправки нагадування, щоб не дублювати його
            # щохвилини, поки хантер не підтвердить кнопкою
            session["last_check_in"] = now_iso()
            save_state(STATE)
        return

    # немає активного хантера — перевіряємо чи мали бути (в межах робочого дня і є призначені)
    hour = datetime.now(TZ).hour
    if 9 <= hour < 21 and STATE["today_hunters"]:
        last_alert = STATE.get("last_missing_alert")
        need_alert = True
        if last_alert:
            since = (datetime.now(TZ) - parse_iso(last_alert)).total_seconds()
            need_alert = since >= 30 * 60  # не частіше ніж раз на 30 хв
        if need_alert:
            await context.bot.send_message(
                chat_id=chat_id,
                text="🚨 Хантер не на місці! Зараз ніхто не веде чергування.",
            )
            STATE["last_missing_alert"] = now_iso()
            save_state(STATE)


async def morning_prompt(context: ContextTypes.DEFAULT_TYPE):
    ensure_today_reset()
    if STATE["chat_id"] is None:
        return
    if STATE["today_hunters"]:
        return
    await context.bot.send_message(
        chat_id=STATE["chat_id"],
        text=(
            "🌅 Доброго ранку! Хто сьогодні хантери?\n"
            "Надішли: /sethunters Ім'я1, Ім'я2"
        ),
    )


def main():
    if not BOT_TOKEN:
        raise SystemExit("Задай змінну середовища BOT_TOKEN з токеном від BotFather")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_help))
    app.add_handler(CommandHandler("chatid", cmd_chatid))
    app.add_handler(CommandHandler("employees", cmd_employees))
    app.add_handler(CommandHandler("addemployee", cmd_addemployee))
    app.add_handler(CommandHandler("removeemployee", cmd_removeemployee))
    app.add_handler(CommandHandler("sethunters", cmd_sethunters))
    app.add_handler(CommandHandler("todayhunters", cmd_todayhunters))
    app.add_handler(CommandHandler("starthunter", cmd_starthunter))
    app.add_handler(CommandHandler("transferhunter", cmd_transferhunter))
    app.add_handler(CommandHandler("endhunter", cmd_endhunter))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("setinterval", cmd_setinterval))
    app.add_handler(CommandHandler("setactivity", cmd_setactivity))
    app.add_handler(CallbackQueryHandler(on_checkin_button, pattern="^checkin$"))

    jq = app.job_queue
    jq.run_repeating(periodic_check, interval=300, first=10)  # кожні 5 хв
    jq.run_daily(morning_prompt, time=dtime(hour=9, minute=0, tzinfo=TZ))

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
