"""
Hunter J087 Telegram Bot
-------------------------
Контроль чергування "хантера" (підхід до покупців) у групі магазину.

Команди:
  /menu                     — відкрити меню з кнопками (почати/передати/завершити/налаштування)
  /chatid                 — показати ID групи (виконати один раз у групі, щоб зареєструвати її)
  /employees               — показати список працівників
  /addemployee Ім'я1, Ім'я2 — додати одного чи кількох працівників
  /removeemployee Ім'я1, Ім'я2 — видалити одного чи кількох працівників
  /setemployees Ім'я1, Ім'я2 — повністю замінити список працівників
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
    MessageHandler,
    ContextTypes,
    filters,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hunter-bot")

TZ = ZoneInfo("Europe/Kyiv")
STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")

DEFAULT_STATE = {
    "chat_id": None,
    "thread_id": None,
    "employees": ["Владислав", "Іван", "Олександр", "Діана", "Тетяна"],
    "today_hunters": [],
    "today_date": None,
    "current_session": None,   # {"employee":..., "start_time":..., "last_check_in":..., "transfers":[...]}
    "log": [],
    "interval_hours": 2,
    "activity_text": "Активність не вказана",
    "last_missing_alert": None,
    "pending_input": None,  # {"type": "sethunters"|"activity", "chat_id": ...}
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
    thread_id = getattr(update.message, "message_thread_id", None)
    is_topic = getattr(update.message, "is_topic_message", False)
    STATE["thread_id"] = thread_id if is_topic else None
    save_state(STATE)
    where = f", гілка {thread_id}" if STATE["thread_id"] else ""
    await update.message.reply_text(
        f"✅ Групу зареєстровано (chat_id={update.effective_chat.id}{where}). "
        f"Автоматичні повідомлення надходитимуть саме сюди."
    )


async def cmd_employees(update: Update, context: ContextTypes.DEFAULT_TYPE):
    names = "\n".join(f"• {e}" for e in STATE["employees"]) or "Список порожній"
    await update.message.reply_text(f"Працівники:\n{names}")


async def cmd_setemployees(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Повністю замінює список працівників (весь колектив одним повідомленням)."""
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(
            "Використання: /setemployees Ім'я1, Ім'я2, Ім'я3\n"
            "⚠️ Це ПОВНІСТЮ замінить поточний список працівників."
        )
        return
    names = [n.strip() for n in text.split(",") if n.strip()]
    STATE["employees"] = names
    save_state(STATE)
    await update.message.reply_text(
        f"✅ Список працівників оновлено ({len(names)}):\n" + "\n".join(f"• {n}" for n in names)
    )


async def cmd_addemployee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(
            "Використання: /addemployee Ім'я\n"
            "Можна одразу кілька через кому: /addemployee Іван, Олена, Владислав"
        )
        return
    names = [n.strip() for n in text.split(",") if n.strip()]
    added = [n for n in names if n not in STATE["employees"]]
    already = [n for n in names if n in STATE["employees"]]
    for n in added:
        STATE["employees"].append(n)
    if added:
        save_state(STATE)
    parts = []
    if added:
        parts.append(f"✅ Додано: {', '.join(added)}")
    if already:
        parts.append(f"ℹ️ Вже були в списку: {', '.join(already)}")
    await update.message.reply_text("\n".join(parts))


async def cmd_removeemployee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(
            "Використання: /removeemployee Ім'я\n"
            "Можна одразу кілька через кому: /removeemployee Іван, Олена"
        )
        return
    names = [n.strip() for n in text.split(",") if n.strip()]
    removed = [n for n in names if n in STATE["employees"]]
    unknown = [n for n in names if n not in STATE["employees"]]
    for n in removed:
        STATE["employees"].remove(n)
    if removed:
        save_state(STATE)
    parts = []
    if removed:
        parts.append(f"🗑 Видалено: {', '.join(removed)}")
    if unknown:
        parts.append(f"⚠️ Немає в списку: {', '.join(unknown)}")
    await update.message.reply_text("\n".join(parts) or "Нічого не змінено.")


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


# ---------------- меню (inline-кнопки) ----------------

def build_main_menu():
    session = STATE["current_session"]
    rows = []
    if session:
        rows.append([InlineKeyboardButton("🔄 Передати хантерство", callback_data="menu_transfer")])
        rows.append([InlineKeyboardButton("🔴 Завершити хантерство", callback_data="menu_end")])
    else:
        rows.append([InlineKeyboardButton("🟢 Почати хантерство", callback_data="menu_start")])
    rows.append([InlineKeyboardButton("⚙️ Налаштування", callback_data="menu_settings")])
    return InlineKeyboardMarkup(rows)


def build_employee_menu(prefix, exclude=None):
    exclude = exclude or set()
    candidates = STATE["today_hunters"] or STATE["employees"]
    rows, row = [], []
    for name in candidates:
        if name in exclude:
            continue
        row.append(InlineKeyboardButton(name, callback_data=f"{prefix}:{name}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("⬅️ Назад", callback_data="back_main")])
    return InlineKeyboardMarkup(rows)


def build_settings_menu():
    rows = [
        [InlineKeyboardButton("📋 Хантери на день", callback_data="settings_sethunters")],
        [InlineKeyboardButton("📝 Змінити активність", callback_data="settings_activity")],
        [InlineKeyboardButton("⏱ Інтервал перевірки", callback_data="settings_interval")],
        [InlineKeyboardButton("⬅️ Назад", callback_data="back_main")],
    ]
    return InlineKeyboardMarkup(rows)


def build_interval_menu():
    hours_options = [1, 2, 3, 4, 6]
    row = [InlineKeyboardButton(f"{h} год", callback_data=f"interval_set:{h}") for h in hours_options]
    return InlineKeyboardMarkup([row, [InlineKeyboardButton("⬅️ Назад", callback_data="menu_settings")]])


def main_menu_text():
    session = STATE["current_session"]
    if session:
        elapsed = (datetime.now(TZ) - parse_iso(session["last_check_in"])).total_seconds()
        return (
            f"📋 Меню\n\n"
            f"АКТИВНО — {session['employee']}\n"
            f"З моменту останньої перевірки: {fmt_duration(elapsed)}\n"
            f"Зміна триває з: {fmt_time(session['start_time'])}\n"
            f"Активність: {STATE['activity_text']}"
        )
    hunters = ", ".join(STATE["today_hunters"]) or "не призначені"
    return (
        f"📋 Меню\n\n"
        f"НЕАКТИВНО — Hunter зараз не працює.\n"
        f"Сьогоднішні хантери: {hunters}\n"
        f"Активність: {STATE['activity_text']}"
    )


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_today_reset()
    if not await require_chat(update):
        return
    await update.message.reply_text(main_menu_text(), reply_markup=build_main_menu())


async def on_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    ensure_today_reset()

    # реєструємо чат/гілку, якщо ще не зареєстровані (щоб фонові перевірки й тут працювали)
    if STATE["chat_id"] is None:
        STATE["chat_id"] = query.message.chat_id
        thread_id = getattr(query.message, "message_thread_id", None)
        STATE["thread_id"] = thread_id
        save_state(STATE)

    async def show_main():
        await query.edit_message_text(main_menu_text(), reply_markup=build_main_menu())

    async def resend_main():
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            message_thread_id=STATE.get("thread_id"),
            text=main_menu_text(),
            reply_markup=build_main_menu(),
        )

    if data == "back_main":
        await show_main()
        return

    if data == "menu_start":
        if STATE["current_session"]:
            await query.answer("Hunter вже активний.", show_alert=True)
            await show_main()
            return
        await query.edit_message_text(
            "Хто починає хантерство? Обери зі списку:",
            reply_markup=build_employee_menu("start_emp"),
        )
        return

    if data == "menu_transfer":
        session = STATE["current_session"]
        if not session:
            await query.answer("Зараз немає активного Hunter.", show_alert=True)
            await show_main()
            return
        await query.edit_message_text(
            "Кому передати хантерство?",
            reply_markup=build_employee_menu("transfer_emp", exclude={session["employee"]}),
        )
        return

    if data == "menu_end":
        session = STATE["current_session"]
        if not session:
            await query.answer("Зараз немає активного Hunter.", show_alert=True)
            await show_main()
            return
        end_ts = now_iso()
        duration = (parse_iso(end_ts) - parse_iso(session["start_time"])).total_seconds()
        STATE["log"].insert(0, {**session, "end_time": end_ts})
        STATE["current_session"] = None
        save_state(STATE)
        await query.edit_message_text(
            f"🔴 Хантерство завершено ({session['employee']}). Тривалість: {fmt_duration(duration)}"
        )
        await resend_main()
        return

    if data.startswith("start_emp:"):
        name = data.split(":", 1)[1]
        if STATE["current_session"]:
            await query.answer("Hunter вже активний.", show_alert=True)
            await show_main()
            return
        ts = now_iso()
        STATE["current_session"] = {
            "employee": name, "start_time": ts, "last_check_in": ts, "transfers": []
        }
        STATE["last_missing_alert"] = None
        save_state(STATE)
        await query.edit_message_text(f"🟢 Хантерство почав: {name} ({fmt_time(ts)})")
        await resend_main()
        return

    if data.startswith("transfer_emp:"):
        name = data.split(":", 1)[1]
        session = STATE["current_session"]
        if not session:
            await query.answer("Зараз немає активного Hunter.", show_alert=True)
            await show_main()
            return
        ts = now_iso()
        session["transfers"].append({"from": session["employee"], "to": name, "time": ts})
        session["employee"] = name
        session["last_check_in"] = ts
        save_state(STATE)
        await query.edit_message_text(f"🔄 Хантерство передано: {name} ({fmt_time(ts)})")
        await resend_main()
        return

    if data == "menu_settings":
        await query.edit_message_text("⚙️ Налаштування:", reply_markup=build_settings_menu())
        return

    if data == "settings_sethunters":
        STATE["pending_input"] = {"type": "sethunters", "chat_id": query.message.chat_id}
        save_state(STATE)
        await query.edit_message_text(
            "Напиши імена хантерів на сьогодні через кому (наприклад: Іван, Олена).\n"
            "Просто надішли текст наступним повідомленням у цей чат."
        )
        return

    if data == "settings_activity":
        STATE["pending_input"] = {"type": "activity", "chat_id": query.message.chat_id}
        save_state(STATE)
        await query.edit_message_text(
            "Напиши, яка зараз проходить активність/акція.\n"
            "Просто надішли текст наступним повідомленням у цей чат."
        )
        return

    if data == "settings_interval":
        await query.edit_message_text(
            f"Поточний інтервал перевірки: кожні {STATE['interval_hours']} год.\nОбери новий:",
            reply_markup=build_interval_menu(),
        )
        return

    if data.startswith("interval_set:"):
        hours = float(data.split(":", 1)[1])
        STATE["interval_hours"] = hours
        save_state(STATE)
        await query.edit_message_text(f"✅ Інтервал перевірки: кожні {hours} год")
        await resend_main()
        return


async def on_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ловить текст, коли бот щойно запитав дані через кнопку налаштувань."""
    pending = STATE.get("pending_input")
    if not pending:
        return
    if update.effective_chat.id != pending.get("chat_id"):
        return
    text = update.message.text.strip()

    if pending["type"] == "sethunters":
        ensure_today_reset()
        names = [n.strip() for n in text.split(",") if n.strip()]
        unknown = [n for n in names if n not in STATE["employees"]]
        if unknown:
            await update.message.reply_text(
                f"⚠️ Немає в списку працівників: {', '.join(unknown)}\n"
                f"Спочатку додай через /addemployee, або перевір написання й спробуй ще раз."
            )
            return
        STATE["today_hunters"] = names
        STATE["today_date"] = today_str()
        STATE["last_missing_alert"] = None
        STATE["pending_input"] = None
        save_state(STATE)
        await update.message.reply_text(
            f"✅ Хантери на сьогодні: {', '.join(names) if names else '—'}",
            reply_markup=build_main_menu(),
        )
        return

    if pending["type"] == "activity":
        STATE["activity_text"] = text
        STATE["pending_input"] = None
        save_state(STATE)
        await update.message.reply_text(
            f"✅ Активність оновлено: {text}", reply_markup=build_main_menu()
        )
        return


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
                message_thread_id=STATE.get("thread_id"),
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

    # немає активного хантера — після 13:00 нагадуємо кожні 10 хв, доки не розпочнуть зміну
    hour = datetime.now(TZ).hour
    if hour >= 13 and STATE["today_hunters"]:
        last_alert = STATE.get("last_missing_alert")
        need_alert = True
        if last_alert:
            since = (datetime.now(TZ) - parse_iso(last_alert)).total_seconds()
            need_alert = since >= 10 * 60  # кожні 10 хв
        if need_alert:
            await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=STATE.get("thread_id"),
                text="🚨 Хантер ще не на місці!",
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
        message_thread_id=STATE.get("thread_id"),
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
    app.add_handler(CommandHandler("setemployees", cmd_setemployees))
    app.add_handler(CommandHandler("sethunters", cmd_sethunters))
    app.add_handler(CommandHandler("todayhunters", cmd_todayhunters))
    app.add_handler(CommandHandler("starthunter", cmd_starthunter))
    app.add_handler(CommandHandler("transferhunter", cmd_transferhunter))
    app.add_handler(CommandHandler("endhunter", cmd_endhunter))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("setinterval", cmd_setinterval))
    app.add_handler(CommandHandler("setactivity", cmd_setactivity))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CallbackQueryHandler(on_checkin_button, pattern="^checkin$"))
    app.add_handler(CallbackQueryHandler(on_menu_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_input))

    jq = app.job_queue
    jq.run_repeating(periodic_check, interval=300, first=10)  # кожні 5 хв
    jq.run_daily(morning_prompt, time=dtime(hour=9, minute=0, tzinfo=TZ))

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
