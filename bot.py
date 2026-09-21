"""
Hunter J087 Telegram Bot
-------------------------
Контроль чергування "хантера" (підхід до покупців) у групі магазину.

Після /chatid бот сам створює й ЗАКРІПЛЮЄ повідомлення-меню в групі —
далі команди вводити не потрібно, все через кнопки в закріпленому повідомленні.
(Дай боту права адміністратора з правом «Закріплювати повідомлення».)

Команди:
  /menu                     — відкрити/оновити закріплене меню
  /chatid                 — показати ID групи (виконати один раз у групі, щоб зареєструвати її)
  /employees               — показати список працівників
  /addemployee Ім'я1, Ім'я2 — додати одного чи кількох працівників
  /removeemployee Ім'я1, Ім'я2 — видалити одного чи кількох працівників
  /setemployees Ім'я1, Ім'я2 — повністю замінити список працівників
  /sethunters текст         — задати розклад хантерів на сьогодні (довільний текст)
  /todayhunters             — показати сьогоднішній розклад
  /starthunter Ім'я         — почати зміну хантера
  /transferhunter Ім'я      — передати зміну іншому
  /endhunter                — завершити зміну
  /status                   — поточний статус (з таймером зміни)
  /setinterval Години       — інтервал перевірки "ти на місці?" (за замовч. 2)
  /setactivity Текст        — поточна активність/акція, буде в нагадуваннях
  /help                     — список команд

Повітряна тривога:
  Кнопка "🚨 Повітряна тривога" / "🔕 Відбій" в меню — ручна пауза/продовження
  всіх автоматичних нагадувань (checkin, "ще ніхто не почав").
  Якщо задано ALERTS_API_TOKEN — бот сам стежить за тривогою в м. Києві
  через alerts.in.ua і вмикає/вимикає паузу автоматично.

Автозавершення і втома:
  Якщо зміна залишається активною після 22:00 — бот сам її завершує
  і сповіщає групу (щоб забуті відкриті зміни не висіли до ранку).
  Раз на 4 години від початку ПЕРШОЇ зміни хантера за день (передача
  зміни цей відлік не скидає) бот питає, чи не втомився хантер і чи
  не хоче передати зміну — з кнопками "продовжую" / "передати".
"""

import json
import logging
import os
from datetime import datetime, time as dtime
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
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
FATIGUE_INTERVAL_HOURS = 4       # раз на скільки годин питати "чи не втомився хантер"
AUTO_END_HOUR = 22               # година, після якої бот сам завершує активну зміну

DEFAULT_STATE = {
    "chat_id": None,
    "thread_id": None,
    "employees": ["Владислав", "Іван", "Олександр", "Діана", "Тетяна"],
    "hunters_schedule_text": None,   # довільний текст розкладу хантерів на день
    "schedule_sent_date": None,      # коли востаннє надсилали розклад о 12:00
    "menu_message_id": None,         # id закріпленого повідомлення-меню
    "today_date": None,
    "current_session": None,   # {"employee":..., "start_time":..., "last_check_in":..., "transfers":[...]}
    "log": [],
    "interval_hours": 2,
    "activity_text": "Активність не вказана",
    "last_missing_alert": None,
    "hunter_done_today": False,   # True, якщо хантерство сьогодні вже відбулось і завершилось
    "air_raid_active": False,     # True на час повітряної тривоги — паузить нагадування
    "shift_started_at": None,   # час початку ПЕРШОЇ зміни хантера сьогодні (не скидається при передачі)
    "last_fatigue_slot": -1,    # який 4-годинний проміжок вже питали "чи не втомився"
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
    """Скидає розклад/пул хантерів дня, якщо настав новий день."""
    if STATE.get("today_date") != today_str():
        STATE["today_date"] = today_str()
        STATE["hunters_schedule_text"] = None
        STATE["schedule_sent_date"] = None
        STATE["hunter_done_today"] = False
        STATE["shift_started_at"] = None
        STATE["last_fatigue_slot"] = -1
        save_state(STATE)


async def require_chat(update: Update) -> bool:
    if STATE["chat_id"] is None:
        await update.message.reply_text(
            "Спочатку виконай /chatid у робочій групі, щоб я знав, куди писати."
        )
        return False
    return True


async def push_pinned(context: ContextTypes.DEFAULT_TYPE, text: str, markup) -> None:
    """Показує текст/кнопки в ОДНОМУ закріпленому повідомленні-меню.
    Якщо воно вже існує — редагує його, якщо ні — створює і закріплює."""
    chat_id = STATE.get("chat_id")
    if not chat_id:
        return
    mid = STATE.get("menu_message_id")
    if mid:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id, message_id=mid, text=text, reply_markup=markup
            )
            return
        except Exception:
            pass  # повідомлення видалили/не змінилось — створюємо нове нижче
    try:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            message_thread_id=STATE.get("thread_id"),
            text=text,
            reply_markup=markup,
        )
    except Exception as e:
        log.error("push_pinned send failed: %s", e)
        return
    STATE["menu_message_id"] = msg.message_id
    save_state(STATE)
    try:
        await context.bot.pin_chat_message(
            chat_id=chat_id, message_id=msg.message_id, disable_notification=True
        )
    except Exception as e:
        log.warning("Не вдалось закріпити меню (потрібні права адміна): %s", e)


async def ensure_pinned_menu(context: ContextTypes.DEFAULT_TYPE) -> None:
    await push_pinned(context, main_menu_text(), build_main_menu())


async def announce(context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    """Надсилає ОКРЕМЕ повідомлення в групу (на відміну від редагування
    закріпленого меню) — щоб старт/передача/завершення хантерства були
    помітні у стрічці чату, а не губились у тихому редагуванні pinned-меню."""
    chat_id = STATE.get("chat_id")
    if not chat_id:
        return
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            message_thread_id=STATE.get("thread_id"),
            text=text,
        )
    except Exception as e:
        log.error("announce failed: %s", e)


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
        f"Автоматичні повідомлення надходитимуть саме сюди.\n\n"
        f"Нижче — закріплене меню, ним і користуйтесь, команди більше не потрібні "
        f"(якщо закріплення не спрацювало — дай боту права адміністратора з правом «Закріплювати повідомлення»)."
    )
    await ensure_pinned_menu(context)


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
    text = " ".join(context.args).strip()
    if not text:
        await update.message.reply_text(
            "Використання: /sethunters текст розкладу\n"
            "Наприклад: /sethunters 12:00-15:00 Іван, 15:00-18:00 Олена"
        )
        return
    STATE["hunters_schedule_text"] = text
    STATE["schedule_sent_date"] = None
    STATE["last_missing_alert"] = None
    STATE["hunter_done_today"] = False
    save_state(STATE)
    await update.message.reply_text(f"✅ Розклад хантерів на сьогодні збережено:\n{text}")
    await ensure_pinned_menu(context)


async def cmd_todayhunters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_today_reset()
    text = STATE.get("hunters_schedule_text") or "розклад ще не задано"
    await update.message.reply_text(f"Розклад хантерів на сьогодні:\n{text}")


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
    STATE["shift_started_at"] = ts
    STATE["last_fatigue_slot"] = -1
    save_state(STATE)
    await update.message.reply_text(f"🟢 Hunter почав: {name} ({fmt_time(ts)})")
    await announce(context, f"🟢 Хантерство розпочав {name} ({fmt_time(ts)})")


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
    await announce(context, f"🔄 Хантерство передано: {name} ({fmt_time(ts)})")


async def cmd_endhunter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = STATE["current_session"]
    if not session:
        await update.message.reply_text("Зараз немає активного Hunter.")
        return
    end_ts = now_iso()
    duration = (parse_iso(end_ts) - parse_iso(session["start_time"])).total_seconds()
    STATE["log"].insert(0, {**session, "end_time": end_ts})
    STATE["current_session"] = None
    STATE["hunter_done_today"] = True
    STATE["shift_started_at"] = None
    STATE["last_fatigue_slot"] = -1
    save_state(STATE)
    await update.message.reply_text(
        f"🔴 Hunter завершено. Тривалість: {fmt_duration(duration)}"
    )
    await announce(
        context,
        f"🔴 Хантерство завершено ({session['employee']}). "
        f"Тривалість: {fmt_duration(duration)}",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_today_reset()
    session = STATE["current_session"]
    if not session:
        schedule = STATE.get("hunters_schedule_text") or "не задано"
        await update.message.reply_text(
            f"НЕАКТИВНО — Hunter зараз не працює.\nРозклад на сьогодні: {schedule}"
        )
        return
    since_start = (datetime.now(TZ) - parse_iso(session["start_time"])).total_seconds()
    await update.message.reply_text(
        f"АКТИВНО — {session['employee']}\n"
        f"⏱ На зміні: {fmt_duration(since_start)} (з {fmt_time(session['start_time'])})\n"
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
    if STATE.get("air_raid_active"):
        rows.append([InlineKeyboardButton("🔕 Відбій", callback_data="alarm_off")])
    else:
        rows.append([InlineKeyboardButton("🚨 Повітряна тривога", callback_data="alarm_on")])
    rows.append([InlineKeyboardButton("⚙️ Налаштування", callback_data="menu_settings")])
    return InlineKeyboardMarkup(rows)


def build_employee_menu(prefix, exclude=None):
    exclude = exclude or set()
    candidates = STATE["employees"]
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
        [InlineKeyboardButton("📋 Розклад хантерів на день", callback_data="settings_sethunters")],
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
    schedule = STATE.get("hunters_schedule_text") or "не задано"
    alarm_banner = "🚨 ПОВІТРЯНА ТРИВОГА — хантерство на паузі\n\n" if STATE.get("air_raid_active") else ""
    if session:
        since_start = (datetime.now(TZ) - parse_iso(session["start_time"])).total_seconds()
        return (
            f"📋 Меню\n\n"
            f"{alarm_banner}"
            f"🟢 АКТИВНО — {session['employee']}\n"
            f"⏱ На зміні: {fmt_duration(since_start)} (почав {fmt_time(session['start_time'])})\n"
            f"Активність: {STATE['activity_text']}\n\n"
            f"Розклад на сьогодні: {schedule}"
        )
    return (
        f"📋 Меню\n\n"
        f"{alarm_banner}"
        f"🔴 НЕАКТИВНО — Hunter зараз не працює.\n"
        f"Активність: {STATE['activity_text']}\n\n"
        f"Розклад на сьогодні: {schedule}"
    )


async def cmd_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_today_reset()
    if not await require_chat(update):
        return
    await ensure_pinned_menu(context)
    try:
        await update.message.delete()
    except Exception:
        pass


async def set_air_raid(context: ContextTypes.DEFAULT_TYPE, active: bool, source: str = "manual"):
    """Вмикає/вимикає паузу через повітряну тривогу і сповіщає групу.
    source: 'manual' (кнопка) або 'auto' (alerts.in.ua)."""
    if STATE.get("air_raid_active") == active:
        return  # вже в потрібному стані — нема чого дублювати
    STATE["air_raid_active"] = active
    save_state(STATE)
    tag = "" if source == "manual" else " (авто)"
    if active:
        await announce(context, f"🚨 Повітряна тривога{tag}! Хантерство призупинено.")
    else:
        # скидаємо таймер "чи на місці", щоб одразу після відбою не прилетіло нагадування
        session = STATE.get("current_session")
        if session:
            session["last_check_in"] = now_iso()
        STATE["last_missing_alert"] = None
        save_state(STATE)
        await announce(context, f"🔕 Відбій{tag}. Хантерство продовжується.")
    await ensure_pinned_menu(context)


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

    # тримаємо в курсі, яке саме повідомлення є нашим закріпленим меню
    if STATE.get("menu_message_id") != query.message.message_id:
        STATE["menu_message_id"] = query.message.message_id
        save_state(STATE)

    async def show_main():
        await query.edit_message_text(main_menu_text(), reply_markup=build_main_menu())

    if data == "back_main":
        await show_main()
        return

    if data == "alarm_on":
        await set_air_raid(context, True, source="manual")
        return

    if data == "alarm_off":
        await set_air_raid(context, False, source="manual")
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
        STATE["hunter_done_today"] = True
        STATE["shift_started_at"] = None
        STATE["last_fatigue_slot"] = -1
        save_state(STATE)
        await query.edit_message_text(
            f"🔴 Хантерство завершено ({session['employee']}). Тривалість: {fmt_duration(duration)}\n\n"
            f"{main_menu_text()}",
            reply_markup=build_main_menu(),
        )
        await announce(
            context,
            f"🔴 Хантерство завершено ({session['employee']}). "
            f"Тривалість: {fmt_duration(duration)}",
        )
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
        STATE["shift_started_at"] = ts
        STATE["last_fatigue_slot"] = -1
        save_state(STATE)
        await query.edit_message_text(
            f"🟢 Хантерство почав: {name} ({fmt_time(ts)})\n\n{main_menu_text()}",
            reply_markup=build_main_menu(),
        )
        await announce(context, f"🟢 Хантерство розпочав {name} ({fmt_time(ts)})")
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
        await query.edit_message_text(
            f"🔄 Хантерство передано: {name} ({fmt_time(ts)})\n\n{main_menu_text()}",
            reply_markup=build_main_menu(),
        )
        await announce(context, f"🔄 Хантерство передано: {name} ({fmt_time(ts)})")
        return

    if data == "fatigue_ok":
        await query.edit_message_text("✅ Ок, продовжуємо! Гарної зміни 💪")
        return

    if data == "fatigue_transfer":
        session = STATE["current_session"]
        if not session:
            await query.edit_message_text("Hunter вже не активний.")
            return
        await query.edit_message_text(
            "Кому передати хантерство?",
            reply_markup=build_employee_menu("fatigue_transfer_emp", exclude={session["employee"]}),
        )
        return

    if data.startswith("fatigue_transfer_emp:"):
        name = data.split(":", 1)[1]
        session = STATE["current_session"]
        if not session:
            await query.edit_message_text("Hunter вже не активний.")
            return
        ts = now_iso()
        session["transfers"].append({"from": session["employee"], "to": name, "time": ts})
        session["employee"] = name
        session["last_check_in"] = ts
        save_state(STATE)
        await query.edit_message_text(f"🔄 Хантерство передано: {name} ({fmt_time(ts)})")
        await announce(context, f"🔄 Хантерство передано: {name} ({fmt_time(ts)})")
        await ensure_pinned_menu(context)
        return

    if data == "menu_settings":
        await query.edit_message_text("⚙️ Налаштування:", reply_markup=build_settings_menu())
        return

    if data == "settings_sethunters":
        STATE["pending_input"] = {"type": "sethunters", "chat_id": query.message.chat_id}
        save_state(STATE)
        await query.edit_message_text(
            "Напиши розклад хантерів на сьогодні (будь-який текст), наприклад:\n"
            "«12:00-15:00 Іван, 15:00-18:00 Олена»\n\n"
            "❗️ДАЙ ВІДПОВІДЬ (Reply) на це повідомлення з текстом розкладу — "
            "просто довге натискання на нього → «Відповісти», інакше Telegram "
            "може не передати повідомлення боту."
        )
        return

    if data == "settings_activity":
        STATE["pending_input"] = {"type": "activity", "chat_id": query.message.chat_id}
        save_state(STATE)
        await query.edit_message_text(
            "Напиши, яка зараз проходить активність/акція.\n\n"
            "❗️ДАЙ ВІДПОВІДЬ (Reply) на це повідомлення з текстом — "
            "довге натискання на нього → «Відповісти»."
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
        await query.edit_message_text(
            f"✅ Інтервал перевірки: кожні {hours} год\n\n{main_menu_text()}",
            reply_markup=build_main_menu(),
        )
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
        STATE["hunters_schedule_text"] = text
        STATE["schedule_sent_date"] = None
        STATE["last_missing_alert"] = None
        STATE["hunter_done_today"] = False
        STATE["pending_input"] = None
        save_state(STATE)
        await update.message.reply_text(f"✅ Розклад збережено:\n{text}")
        await ensure_pinned_menu(context)
        return

    if pending["type"] == "activity":
        STATE["activity_text"] = text
        STATE["pending_input"] = None
        save_state(STATE)
        await update.message.reply_text(f"✅ Активність оновлено: {text}")
        await ensure_pinned_menu(context)
        return


# ---------------- callback (кнопка "я на місці") ----------------

async def on_checkin_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    session = STATE["current_session"]
    if session:
        session["last_check_in"] = now_iso()
        save_state(STATE)
        await query.edit_message_text(
            f"✅ Підтверджено: {session['employee']} на місці й проінформував покупців."
        )
    else:
        await query.edit_message_text("Hunter вже не активний.")


# ---------------- фонові перевірки ----------------

async def periodic_check(context: ContextTypes.DEFAULT_TYPE):
    ensure_today_reset()
    if STATE["chat_id"] is None:
        return
    if STATE.get("air_raid_active"):
        return  # на час тривоги всі автоматичні нагадування призупинені
    chat_id = STATE["chat_id"]
    session = STATE["current_session"]

    if session:
        now = datetime.now(TZ)

        # авто-завершення після 22:00 — працівники інколи забувають закрити зміну вручну
        if now.hour >= AUTO_END_HOUR:
            end_ts = now_iso()
            duration = (now - parse_iso(session["start_time"])).total_seconds()
            STATE["log"].insert(0, {**session, "end_time": end_ts})
            STATE["current_session"] = None
            STATE["hunter_done_today"] = True
            STATE["shift_started_at"] = None
            STATE["last_fatigue_slot"] = -1
            save_state(STATE)
            await announce(
                context,
                f"🔴 Зміну автоматично завершено о {AUTO_END_HOUR}:00 ({session['employee']}). "
                f"Тривалість: {fmt_duration(duration)}\n"
                f"⚠️ Не забувай завершувати хантерство вручну наступного разу.",
            )
            await ensure_pinned_menu(context)
            return

        elapsed = (now - parse_iso(session["last_check_in"])).total_seconds()
        threshold = STATE["interval_hours"] * 3600
        if elapsed >= threshold:
            keyboard = InlineKeyboardMarkup(
                [[InlineKeyboardButton("✅ Так, на місці й проінформував", callback_data="checkin")]]
            )
            await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=STATE.get("thread_id"),
                text=(
                    f"⏰ {session['employee']}, ти на місці?\n"
                    f"Активність: {STATE['activity_text']}\n\n"
                    f"Проінформував покупців про нашу активність?"
                ),
                reply_markup=keyboard,
            )
            # позначаємо момент відправки нагадування, щоб не дублювати його
            # щохвилини, поки хантер не підтвердить кнопкою
            session["last_check_in"] = now_iso()
            save_state(STATE)

        # питання про втому — кожні FATIGUE_INTERVAL_HOURS годин від початку
        # ПЕРШОЇ зміни хантера сьогодні (передача зміни цей відлік не скидає)
        shift_start = STATE.get("shift_started_at")
        if shift_start:
            elapsed_since_start = (now - parse_iso(shift_start)).total_seconds()
            slot = int(elapsed_since_start // (FATIGUE_INTERVAL_HOURS * 3600))
            if slot > 0 and slot > STATE.get("last_fatigue_slot", -1):
                fatigue_keyboard = InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("✅ Все ок, продовжую", callback_data="fatigue_ok")],
                        [InlineKeyboardButton("🔄 Так, хочу передати зміну", callback_data="fatigue_transfer")],
                    ]
                )
                await context.bot.send_message(
                    chat_id=chat_id,
                    message_thread_id=STATE.get("thread_id"),
                    text=(
                        f"⏰ {session['employee']}, ти вже {FATIGUE_INTERVAL_HOURS} год на хантерстві.\n"
                        f"Не втомився? Можливо, час передати зміну наступному хантеру?"
                    ),
                    reply_markup=fatigue_keyboard,
                )
                STATE["last_fatigue_slot"] = slot
                save_state(STATE)
        return

    # немає активного хантера — з 13:00 нагадуємо щоразу через 15 хв,
    # поки хтось не розпочне зміну. Тільки якщо на сьогодні заданий
    # текстовий розклад хантерів — якщо розклад не задано (наприклад,
    # магазин вихідний), бот мовчить і не турбує команду.
    if not STATE.get("hunters_schedule_text"):
        return
    # якщо хантерство сьогодні вже відбулось і завершилось — більше не нагадуємо
    if STATE.get("hunter_done_today"):
        return
    now = datetime.now(TZ)
    if now.hour < 13:
        return
    last_alert = STATE.get("last_missing_alert")
    need_alert = True
    if last_alert:
        since = (now - parse_iso(last_alert)).total_seconds()
        need_alert = since >= 15 * 60  # кожні 15 хв
    if need_alert:
        await context.bot.send_message(
            chat_id=chat_id,
            message_thread_id=STATE.get("thread_id"),
            text="🚨 Ще ніхто не розпочав хантерство сьогодні!",
        )
        STATE["last_missing_alert"] = now_iso()
        save_state(STATE)


# ---------------- авто-виявлення повітряної тривоги (alerts.in.ua) ----------------

ALERTS_API_TOKEN = os.environ.get("ALERTS_API_TOKEN", "")
ALERTS_REGION_UID = os.environ.get("ALERTS_REGION_UID", "31")  # 31 = м. Київ

async def check_air_raid_api(context: ContextTypes.DEFAULT_TYPE):
    """Раз на ~90 сек опитує alerts.in.ua і сам вмикає/вимикає паузу
    хантерства, коли реально лунає/закінчується повітряна тривога.
    Працює тільки якщо задано ALERTS_API_TOKEN в змінних середовища."""
    if not ALERTS_API_TOKEN or STATE["chat_id"] is None:
        return
    import urllib.request
    import urllib.error

    url = (
        f"https://api.alerts.in.ua/v1/iot/active_air_raid_alerts/{ALERTS_REGION_UID}.json"
        f"?token={ALERTS_API_TOKEN}"
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            raw = resp.read().decode("utf-8")
        status = json.loads(raw)  # повертає рядок: "A" (тривога), "P" (часткова), "N" (немає)
        active = status in ("A", "P")
    except (urllib.error.URLError, ValueError, KeyError) as e:
        log.warning("alerts.in.ua polling failed: %s", e)
        return

    if active != STATE.get("air_raid_active"):
        await set_air_raid(context, active, source="auto")


async def noon_broadcast(context: ContextTypes.DEFAULT_TYPE):
    """О 12:00 надсилає розклад хантерів (якщо заданий) і відкриває меню початку зміни."""
    ensure_today_reset()
    if STATE["chat_id"] is None:
        return
    schedule = STATE.get("hunters_schedule_text")
    if not schedule:
        return  # розклад на сьогодні ще не задали — нічого слати
    if STATE.get("schedule_sent_date") == today_str():
        return  # вже надсилали сьогодні
    await context.bot.send_message(
        chat_id=STATE["chat_id"],
        message_thread_id=STATE.get("thread_id"),
        text=(
            f"👋 Всім привіт!\n\n"
            f"Нагадую зараз проходить активність: {STATE['activity_text']}\n\n"
            f"Давайте поторгуємо! 💪\n\n"
            f"🗒 Хантери на сьогодні:\n{schedule}"
        ),
    )
    STATE["schedule_sent_date"] = today_str()
    save_state(STATE)
    if not STATE["current_session"]:
        # відразу відкриваємо вибір "хто починає" в закріпленому меню
        await push_pinned(
            context,
            "Хто починає хантерство? Обери зі списку:",
            build_employee_menu("start_emp"),
        )
    else:
        await ensure_pinned_menu(context)


async def morning_prompt(context: ContextTypes.DEFAULT_TYPE):
    ensure_today_reset()
    if STATE["chat_id"] is None:
        return
    if STATE.get("hunters_schedule_text"):
        return
    await context.bot.send_message(
        chat_id=STATE["chat_id"],
        message_thread_id=STATE.get("thread_id"),
        text=(
            "🌅 Доброго ранку! Який розклад хантерів на сьогодні?\n"
            "Задай через меню (⚙️ Налаштування → 📋 Розклад хантерів на день) "
            "або командою /sethunters текст розкладу."
        ),
    )


async def post_init(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("menu", "Відкрити меню"),
        BotCommand("chatid", "Зареєструвати групу"),
        BotCommand("status", "Поточний статус"),
        BotCommand("employees", "Список працівників"),
        BotCommand("sethunters", "Задати розклад хантерів"),
        BotCommand("help", "Список команд"),
    ])


def main():
    if not BOT_TOKEN:
        raise SystemExit("Задай змінну середовища BOT_TOKEN з токеном від BotFather")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

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
    jq.run_repeating(check_air_raid_api, interval=90, first=15)  # авто-тривога, кожні 90 сек
    jq.run_daily(morning_prompt, time=dtime(hour=9, minute=0, tzinfo=TZ))
    jq.run_daily(noon_broadcast, time=dtime(hour=12, minute=0, tzinfo=TZ))

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
