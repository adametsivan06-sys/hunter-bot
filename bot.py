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
(Команди /starthunter, /transferhunter, /endhunter працюють з першим хантером;
 другим хантером керують кнопки в меню.)

Другий хантер (для вихідних і завантажених днів):
  Кнопка "➕ Другий хантер" в меню запускає другу зміну паралельно з першою.
  У кожного хантера свій таймер "ти на місці?", своя передача і завершення.
  Сповіщення "ще ніхто не розпочав" приходить, лише якщо немає жодного хантера.

Повітряна тривога:
  Кнопка "🚨 Повітряна тривога" / "🔕 Відбій" в меню — ручна пауза/продовження
  всіх автоматичних нагадувань (checkin, "ще ніхто не почав").
  Якщо відбій забули натиснути — бот сам знімає тривогу через
  AIR_RAID_AUTO_OFF_HOURS годин або з настанням нової доби.
  Якщо задано ALERTS_API_TOKEN — бот сам стежить за тривогою в м. Києві
  через alerts.in.ua і вмикає/вимикає паузу автоматично (тоді автозняття
  за таймером не потрібне і вимкнене).

Автозавершення і втома:
  Якщо зміна залишається активною після 22:00 — бот сам її завершує
  і сповіщає групу (щоб забуті відкриті зміни не висіли до ранку).
  Раз на 4 години від початку зміни хантера бот питає, чи не втомився
  хантер і чи не хоче передати зміну (для першого хантера передача зміни
  цей відлік не скидає) — з кнопками "продовжую" / "передати".

Теги і реєстрація:
  Кожен працівник один раз пише боту в ОСОБИСТІ, натискає Start і обирає себе
  зі списку (/register). Після цього бот тегає його в групі (старт, передача,
  «ти на місці?», «не втомився?») і може писати йому в особисті.
  Керівнику: /who — хто вже зареєстрований, /unreg Прізвище Ім'я — відв'язати.

Опитування після зміни:
  Коли відрізок зміни працівника завершився (передача, завершення, автозавершення
  о 22:00) і він тривав щонайменше 2 години — бот пише йому в особисті:
  скільком запропонував допомогу, скільки закінчилось покупкою, скількох
  проінформував про акцію, самооцінка зміни 1–5. Коротшим відрізкам опитування не йде.

Тижневий звіт:
  У змінній OWNER_ID задай свій Telegram ID (його покаже /myid) і натисни Start
  у чаті з ботом. Щопонеділка після 09:30 прийде звіт за минулий тиждень:
  години по людях, «ти на місці?» (підтверджено/надіслано), відповіді опитувань
  з позначкою ⚠️ там, де відповіді не сходяться. /report — звіт за 7 днів у будь-який момент.

Збереження даних:
  За замовчуванням стан лежить у state.json поряд з ботом. На Railway
  підключи Volume (наприклад, на /data) і задай змінну STATE_FILE=/data/state.json,
  щоб дані переживали перезапуск і новий деплой.
"""

import html
import json
import logging
import os
from datetime import datetime, time as dtime, timedelta
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
STATE_FILE = os.environ.get("STATE_FILE") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "state.json")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
ALERTS_API_TOKEN = os.environ.get("ALERTS_API_TOKEN", "")
ALERTS_REGION_UID = os.environ.get("ALERTS_REGION_UID", "31")  # 31 = м. Київ
FATIGUE_INTERVAL_HOURS = 4       # раз на скільки годин питати "чи не втомився хантер"
AUTO_END_HOUR = 22               # година, після якої бот сам завершує активну зміну
AIR_RAID_AUTO_OFF_HOURS = 3      # через скільки годин бот сам знімає тривогу, якщо забули натиснути "Відбій"
SURVEY_MIN_MINUTES = 120         # опитування отримують лише ті, чий відрізок зміни тривав не менше 2 годин
OWNER_ID = os.environ.get("OWNER_ID", "").strip()   # твій Telegram ID (дізнатись: /myid) — для тижневого звіту

# Питання опитування після зміни: (текст, варіанти відповіді)
SURVEY_QUESTIONS = [
    ("Скільком покупцям запропонував допомогу?", ["0", "1–5", "6–15", "16+"]),
    ("Скільки з цих розмов закінчилось покупкою, приблизно?", ["0", "1–2", "3–5", "6+"]),
    ("Скількох покупців проінформував про нашу поточну акцію?", ["0", "1–5", "6–15", "16+"]),
    ("Як оцінюєш свою зміну? (1 — слабко, 5 — відмінно)", ["1", "2", "3", "4", "5"]),
]
# для перевірки логіки відповідей: нижня/верхня межа кожного варіанту питань 1 і 2
Q1_LOW, Q1_HIGH = [0, 1, 6, 16], [0, 5, 15, 10**6]
Q2_LOW = [0, 1, 3, 6]
WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "нд"]

# Стандартний список працівників. Змінюєш список тут і піднімаєш EMPLOYEES_VERSION —
# при наступному запуску бот один раз перезапише збережений список цим.
EMPLOYEES_VERSION = 1
DEFAULT_EMPLOYEES = [
    "Адамець Іван",
    "Вовк Аліна",
    "Суржук Олександр",
    "Назарук Іван",
    "Магницький Іван",
    "Редько Діана",
    "Балема Владислав",
    "Клименко Сергій",
    "Яцина Поліна",
    "Мицик Олександр",
    "Сидоренко Руслан",
    "Гарасюта Микола",
]

# слот зміни -> ключ у стані
SLOT_KEYS = {"main": "current_session", "second": "second_session"}

# callback-кнопки другого хантера -> відповідна базова дія
SECOND_HEADS = {
    "menu_start2": "menu_start",
    "menu_transfer2": "menu_transfer",
    "menu_end2": "menu_end",
    "start_emp2": "start_emp",
    "transfer_emp2": "transfer_emp",
    "fatigue_ok2": "fatigue_ok",
    "fatigue_transfer2": "fatigue_transfer",
    "fatigue_transfer_emp2": "fatigue_transfer_emp",
}

DEFAULT_STATE = {
    "chat_id": None,
    "thread_id": None,
    "employees": list(DEFAULT_EMPLOYEES),
    "employees_version": EMPLOYEES_VERSION,
    "hunters_schedule_text": None,   # довільний текст розкладу хантерів на день
    "schedule_sent_date": None,      # коли востаннє надсилали розклад о 12:00
    "menu_message_id": None,         # id закріпленого повідомлення-меню
    "today_date": None,
    "current_session": None,   # {"employee":..., "start_time":..., "last_check_in":..., "transfers":[...]}
    "second_session": None,    # те саме для другого хантера (+ "fatigue_slot")
    "log": [],
    "interval_hours": 2,
    "activity_text": "Активність не вказана",
    "last_missing_alert": None,
    "hunter_done_today": False,   # True, якщо хантерство сьогодні вже відбулось і завершилось
    "air_raid_active": False,     # True на час повітряної тривоги — паузить нагадування
    "air_raid_since": None,       # коли ввімкнули тривогу (для автозняття)
    "shift_started_at": None,   # час початку ПЕРШОЇ зміни хантера сьогодні (не скидається при передачі)
    "last_fatigue_slot": -1,    # який 4-годинний проміжок вже питали "чи не втомився"
    "pending_input": None,  # {"type": "sethunters"|"activity", "chat_id": ...}
    "users": {},             # {"Прізвище Ім'я": telegram_user_id} — для тегів і опитувань
    "segments": [],          # завершені відрізки: один працівник безперервно на хантерстві
    "surveys": {},           # {"id": опитування після відрізка зміни}
    "next_id": 1,
    "last_report_week": None,  # понеділок (YYYY-MM-DD) тижня, коли востаннє слали звіт
}


def fresh_state():
    return json.loads(json.dumps(DEFAULT_STATE))


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            merged = {**fresh_state(), **data}
            # разова заміна списку працівників на стандартний
            if data.get("employees_version") != EMPLOYEES_VERSION:
                merged["employees"] = list(DEFAULT_EMPLOYEES)
                merged["employees_version"] = EMPLOYEES_VERSION
            return merged
        except Exception as e:
            log.error("Failed to load state: %s", e)
    return fresh_state()


def save_state(state):
    try:
        folder = os.path.dirname(STATE_FILE)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log.error("Failed to save state: %s", e)


STATE = load_state()
save_state(STATE)


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


# ---------------- сесії (перший і другий хантер) ----------------

def get_session(slot):
    return STATE.get(SLOT_KEYS[slot])


def active_sessions():
    """[(slot, session), ...] для всіх активних змін."""
    return [(slot, STATE.get(key)) for slot, key in SLOT_KEYS.items() if STATE.get(key)]


def any_active():
    return bool(active_sessions())


def busy_names():
    """Хто зараз на хантерстві (в будь-якому слоті)."""
    return {s["employee"] for _, s in active_sessions()}


def slot_label(slot):
    return "" if slot == "main" else " (другий хантер)"


def close_segment(slot, session, end_ts):
    """Фіксує відрізок «один працівник безперервно на хантерстві». Якщо він тривав
    не менше SURVEY_MIN_MINUTES — ставить у чергу опитування для цього працівника."""
    start = session.get("seg_start") or session["start_time"]
    minutes = (parse_iso(end_ts) - parse_iso(start)).total_seconds() / 60
    sid = STATE["next_id"]
    STATE["next_id"] = sid + 1
    seg = {
        "id": sid, "employee": session["employee"], "start": start, "end": end_ts,
        "slot": slot, "minutes": round(minutes, 1),
    }
    STATE["segments"].append(seg)
    STATE["segments"] = STATE["segments"][-800:]
    if minutes >= SURVEY_MIN_MINUTES:
        STATE["surveys"][str(sid)] = {
            "id": sid, "employee": seg["employee"], "start": start, "end": end_ts,
            "minutes": seg["minutes"], "slot": slot, "status": "queued", "answers": {},
        }
        # старі опитування не накопичуємо безкінечно
        if len(STATE["surveys"]) > 400:
            for k in sorted(STATE["surveys"], key=int)[:-400]:
                del STATE["surveys"][k]
    return seg


def do_start(slot, name):
    ts = now_iso()
    STATE[SLOT_KEYS[slot]] = {
        "employee": name, "start_time": ts, "last_check_in": ts, "transfers": [],
        "seg_start": ts, "stats": {},
    }
    STATE["last_missing_alert"] = None
    if slot == "main":
        STATE["shift_started_at"] = ts
        STATE["last_fatigue_slot"] = -1
    save_state(STATE)
    return ts


def do_transfer(slot, name):
    session = get_session(slot)
    ts = now_iso()
    close_segment(slot, session, ts)
    session["transfers"].append({"from": session["employee"], "to": name, "time": ts})
    session["employee"] = name
    session["seg_start"] = ts
    session["last_check_in"] = ts
    save_state(STATE)
    return ts


def do_end(slot):
    session = get_session(slot)
    end_ts = now_iso()
    duration = (parse_iso(end_ts) - parse_iso(session["start_time"])).total_seconds()
    close_segment(slot, session, end_ts)
    STATE["log"].insert(0, {**session, "end_time": end_ts, "slot": slot})
    del STATE["log"][400:]
    STATE[SLOT_KEYS[slot]] = None
    if slot == "main":
        STATE["shift_started_at"] = None
        STATE["last_fatigue_slot"] = -1
    if not any_active():
        STATE["hunter_done_today"] = True
    save_state(STATE)
    return session, duration


def start_announcement(slot, name, ts):
    if slot == "main":
        return f"🟢 Хантерство розпочав {name} ({fmt_time(ts)})"
    return f"🟢 Другий хантер розпочав {name} ({fmt_time(ts)})"


def transfer_announcement(slot, name, ts):
    if slot == "main":
        return f"🔄 Хантерство передано: {name} ({fmt_time(ts)})"
    return f"🔄 Другий хантер: хантерство передано {name} ({fmt_time(ts)})"


def end_announcement(slot, session, duration):
    if slot == "main":
        return f"🔴 Хантерство завершено ({session['employee']}). Тривалість: {fmt_duration(duration)}"
    return f"🔴 Другий хантер завершив ({session['employee']}). Тривалість: {fmt_duration(duration)}"


# ---------------- закріплене меню ----------------

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
        except Exception as e:
            if "not modified" in str(e).lower():
                return  # текст не змінився — нове повідомлення створювати не треба
            # повідомлення видалили — створюємо нове нижче
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


def tag(name):
    """Ім'я як тег (клікабельна згадка), якщо працівник зареєстрований; інакше просто ім'я.
    Повертає HTML — використовувати з parse_mode=HTML."""
    uid = STATE.get("users", {}).get(name)
    esc = html.escape(name)
    if uid:
        return f'<a href="tg://user?id={uid}">{esc}</a>'
    return esc


async def announce(context: ContextTypes.DEFAULT_TYPE, text: str, mention: str = None) -> None:
    """Надсилає ОКРЕМЕ повідомлення в групу (на відміну від редагування
    закріпленого меню) — щоб старт/передача/завершення хантерства були
    помітні у стрічці чату, а не губились у тихому редагуванні pinned-меню.
    mention — ім'я працівника, яке треба замінити на тег (якщо він зареєстрований)."""
    chat_id = STATE.get("chat_id")
    if not chat_id:
        return
    body = html.escape(text)
    if mention:
        body = body.replace(html.escape(mention), tag(mention), 1)
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            message_thread_id=STATE.get("thread_id"),
            text=body,
            parse_mode="HTML",
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
    if name in busy_names():
        await update.message.reply_text(f"{name} вже на хантерстві.")
        return
    ts = do_start("main", name)
    await update.message.reply_text(f"🟢 Hunter почав: {name} ({fmt_time(ts)})")
    await announce(context, start_announcement("main", name, ts), mention=name)


async def cmd_transferhunter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not STATE["current_session"]:
        await update.message.reply_text("Зараз немає активного Hunter.")
        return
    name = " ".join(context.args).strip()
    if not name:
        await update.message.reply_text("Використання: /transferhunter Ім'я")
        return
    if name in busy_names():
        await update.message.reply_text(f"{name} вже на хантерстві.")
        return
    ts = do_transfer("main", name)
    await update.message.reply_text(f"🔄 Hunter передано: {name} ({fmt_time(ts)})")
    await announce(context, transfer_announcement("main", name, ts), mention=name)


async def cmd_endhunter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not STATE["current_session"]:
        await update.message.reply_text("Зараз немає активного Hunter.")
        return
    session, duration = do_end("main")
    await update.message.reply_text(
        f"🔴 Hunter завершено. Тривалість: {fmt_duration(duration)}"
    )
    await announce(context, end_announcement("main", session, duration))


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_today_reset()
    sessions = active_sessions()
    if not sessions:
        schedule = STATE.get("hunters_schedule_text") or "не задано"
        await update.message.reply_text(
            f"НЕАКТИВНО — Hunter зараз не працює.\nРозклад на сьогодні: {schedule}"
        )
        return
    now = datetime.now(TZ)
    lines = []
    for slot, s in sessions:
        since_start = (now - parse_iso(s["start_time"])).total_seconds()
        title = "АКТИВНО" if slot == "main" else "АКТИВНО (другий хантер)"
        lines.append(
            f"{title} — {s['employee']}\n"
            f"⏱ На зміні: {fmt_duration(since_start)} (з {fmt_time(s['start_time'])})"
        )
    await update.message.reply_text(
        "\n\n".join(lines) + f"\n\nАктивність: {STATE['activity_text']}"
    )


# ---------------- меню (inline-кнопки) ----------------

def build_main_menu():
    main = STATE["current_session"]
    second = STATE.get("second_session")
    tag1 = " (1-й хантер)" if (main and second) else ""
    rows = []
    if main:
        rows.append([InlineKeyboardButton(f"🔄 Передати хантерство{tag1}", callback_data="menu_transfer")])
        rows.append([InlineKeyboardButton(f"🔴 Завершити хантерство{tag1}", callback_data="menu_end")])
    else:
        rows.append([InlineKeyboardButton("🟢 Почати хантерство", callback_data="menu_start")])
    if second:
        rows.append([InlineKeyboardButton("🔄 Передати (2-й хантер)", callback_data="menu_transfer2")])
        rows.append([InlineKeyboardButton("🔴 Завершити (2-й хантер)", callback_data="menu_end2")])
    else:
        rows.append([InlineKeyboardButton("➕ Другий хантер", callback_data="menu_start2")])
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
    schedule = STATE.get("hunters_schedule_text") or "не задано"
    alarm_banner = "🚨 ПОВІТРЯНА ТРИВОГА — хантерство на паузі\n\n" if STATE.get("air_raid_active") else ""
    sessions = active_sessions()
    if sessions:
        now = datetime.now(TZ)
        blocks = []
        for slot, s in sessions:
            since_start = (now - parse_iso(s["start_time"])).total_seconds()
            title = "АКТИВНО" if slot == "main" else "АКТИВНО (2-й хантер)"
            blocks.append(
                f"🟢 {title} — {s['employee']}\n"
                f"⏱ На зміні: {fmt_duration(since_start)} (почав {fmt_time(s['start_time'])})"
            )
        status_block = "\n\n".join(blocks)
    else:
        status_block = "🔴 НЕАКТИВНО — Hunter зараз не працює."
    return (
        f"📋 Меню\n\n"
        f"{alarm_banner}"
        f"{status_block}\n"
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


# ---------------- повітряна тривога ----------------

async def set_air_raid(context: ContextTypes.DEFAULT_TYPE, active: bool, source: str = "manual"):
    """Вмикає/вимикає паузу через повітряну тривогу і сповіщає групу.
    source: 'manual' (кнопка), 'auto' (alerts.in.ua) або 'timeout' (бот сам зняв,
    бо відбій не натиснули)."""
    if STATE.get("air_raid_active") == active:
        return  # вже в потрібному стані — нема чого дублювати
    STATE["air_raid_active"] = active
    STATE["air_raid_since"] = now_iso() if active else None
    save_state(STATE)
    tag = " (авто)" if source == "auto" else ""
    if active:
        await announce(context, f"🚨 Повітряна тривога{tag}! Хантерство призупинено.")
    else:
        # скидаємо таймер "чи на місці", щоб одразу після відбою не прилетіло нагадування
        for _, s in active_sessions():
            s["last_check_in"] = now_iso()
        STATE["last_missing_alert"] = None
        save_state(STATE)
        if source == "timeout":
            await announce(
                context,
                "🔕 Тривогу знято автоматично (відбій не натиснули). Хантерство продовжується.",
            )
        else:
            await announce(context, f"🔕 Відбій{tag}. Хантерство продовжується.")
    await ensure_pinned_menu(context)


async def maybe_expire_air_raid(context: ContextTypes.DEFAULT_TYPE):
    """Знімає тривогу, якщо про відбій забули: минуло AIR_RAID_AUTO_OFF_HOURS годин
    або настала нова доба. Якщо підключено alerts.in.ua — не чіпає, бо стан бере з API."""
    if not STATE.get("air_raid_active") or ALERTS_API_TOKEN:
        return
    since = STATE.get("air_raid_since")
    expired = True  # тривога без позначки часу (залишилась зі старої версії) — вважаємо забутою
    if since:
        now = datetime.now(TZ)
        started = parse_iso(since)
        expired = (
            (now - started).total_seconds() >= AIR_RAID_AUTO_OFF_HOURS * 3600
            or started.date() != now.date()
        )
    if expired:
        await set_air_raid(context, False, source="timeout")


# ---------------- обробка кнопок ----------------

async def toast(query, text):
    """Спливаюче повідомлення на кнопці; помилки Telegram ігноруємо."""
    try:
        await query.answer(text, show_alert=True)
    except Exception:
        pass


async def on_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    ensure_today_reset()

    head, _, arg = data.partition(":")
    slot = "main"
    if head in SECOND_HEADS:
        slot = "second"
        head = SECOND_HEADS[head]
    sfx = "2" if slot == "second" else ""

    # реєструємо чат/гілку, якщо ще не зареєстровані (щоб фонові перевірки й тут працювали)
    if STATE["chat_id"] is None:
        STATE["chat_id"] = query.message.chat_id
        thread_id = getattr(query.message, "message_thread_id", None)
        STATE["thread_id"] = thread_id
        save_state(STATE)

    # якщо id закріпленого меню втрачено — підхоплюємо повідомлення, з якого натиснули кнопку
    # (але не нагадування про втому — вони не є меню)
    if STATE.get("menu_message_id") is None and not head.startswith("fatigue"):
        STATE["menu_message_id"] = query.message.message_id
        save_state(STATE)

    async def show_main():
        try:
            await query.edit_message_text(main_menu_text(), reply_markup=build_main_menu())
        except Exception as e:
            if "not modified" not in str(e).lower():
                raise

    if head == "back_main":
        await show_main()
        return

    if head == "alarm_on":
        await set_air_raid(context, True, source="manual")
        return

    if head == "alarm_off":
        await set_air_raid(context, False, source="manual")
        return

    if head == "menu_start":
        if get_session(slot):
            await toast(query, "Цей хантер вже активний.")
            await show_main()
            return
        title = "Хто починає хантерство? Обери зі списку:" if slot == "main" else "Хто буде другим хантером? Обери зі списку:"
        await query.edit_message_text(
            title,
            reply_markup=build_employee_menu("start_emp" + sfx, exclude=busy_names()),
        )
        return

    if head == "menu_transfer":
        if not get_session(slot):
            await toast(query, "Зараз немає активного Hunter.")
            await show_main()
            return
        await query.edit_message_text(
            "Кому передати хантерство?",
            reply_markup=build_employee_menu("transfer_emp" + sfx, exclude=busy_names()),
        )
        return

    if head == "menu_end":
        if not get_session(slot):
            await toast(query, "Зараз немає активного Hunter.")
            await show_main()
            return
        session, duration = do_end(slot)
        text = end_announcement(slot, session, duration)
        await query.edit_message_text(
            f"{text}\n\n{main_menu_text()}",
            reply_markup=build_main_menu(),
        )
        await announce(context, text)
        return

    if head == "start_emp":
        name = arg
        if get_session(slot):
            await toast(query, "Цей хантер вже активний.")
            await show_main()
            return
        if name in busy_names():
            await toast(query, f"{name} вже на хантерстві.")
            await show_main()
            return
        ts = do_start(slot, name)
        await query.edit_message_text(
            f"{start_announcement(slot, name, ts)}\n\n{main_menu_text()}",
            reply_markup=build_main_menu(),
        )
        await announce(context, start_announcement(slot, name, ts), mention=name)
        return

    if head == "transfer_emp":
        name = arg
        if not get_session(slot):
            await toast(query, "Зараз немає активного Hunter.")
            await show_main()
            return
        if name in busy_names():
            await toast(query, f"{name} вже на хантерстві.")
            await show_main()
            return
        ts = do_transfer(slot, name)
        await query.edit_message_text(
            f"{transfer_announcement(slot, name, ts)}\n\n{main_menu_text()}",
            reply_markup=build_main_menu(),
        )
        await announce(context, transfer_announcement(slot, name, ts), mention=name)
        return

    if head == "fatigue_ok":
        await query.edit_message_text("✅ Ок, продовжуємо! Гарної зміни 💪")
        return

    if head == "fatigue_transfer":
        if not get_session(slot):
            await query.edit_message_text("Hunter вже не активний.")
            return
        await query.edit_message_text(
            "Кому передати хантерство?",
            reply_markup=build_employee_menu("fatigue_transfer_emp" + sfx, exclude=busy_names()),
        )
        return

    if head == "fatigue_transfer_emp":
        name = arg
        if not get_session(slot):
            await query.edit_message_text("Hunter вже не активний.")
            return
        if name in busy_names():
            await toast(query, f"{name} вже на хантерстві.")
            return
        ts = do_transfer(slot, name)
        await query.edit_message_text(transfer_announcement(slot, name, ts))
        await announce(context, transfer_announcement(slot, name, ts), mention=name)
        await ensure_pinned_menu(context)
        return

    if head == "menu_settings":
        await query.edit_message_text("⚙️ Налаштування:", reply_markup=build_settings_menu())
        return

    if head == "settings_sethunters":
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

    if head == "settings_activity":
        STATE["pending_input"] = {"type": "activity", "chat_id": query.message.chat_id}
        save_state(STATE)
        await query.edit_message_text(
            "Напиши, яка зараз проходить активність/акція.\n\n"
            "❗️ДАЙ ВІДПОВІДЬ (Reply) на це повідомлення з текстом — "
            "довге натискання на нього → «Відповісти»."
        )
        return

    if head == "settings_interval":
        await query.edit_message_text(
            f"Поточний інтервал перевірки: кожні {STATE['interval_hours']} год.\nОбери новий:",
            reply_markup=build_interval_menu(),
        )
        return

    if head == "interval_set":
        hours = float(arg)
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
    slot = "second" if query.data == "checkin2" else "main"
    session = get_session(slot)
    if session:
        session["last_check_in"] = now_iso()
        st = session.setdefault("stats", {}).setdefault(session["employee"], {"sent": 0, "ok": 0})
        st["ok"] += 1
        save_state(STATE)
        await query.edit_message_text(
            f"✅ Підтверджено: {session['employee']} на місці й проінформував покупців."
        )
    else:
        await query.edit_message_text("Hunter вже не активний.")


# ---------------- реєстрація працівників (для тегів і опитувань) ----------------

def first_name(full):
    """«Балема Владислав» -> «Владислав» (у списку спочатку прізвище)."""
    parts = full.split()
    return parts[-1] if parts else full


def is_owner(update: Update) -> bool:
    return bool(OWNER_ID) and str(update.effective_user.id) == OWNER_ID


def build_register_menu(user_id):
    users = STATE["users"]
    rows, row = [], []
    for name in STATE["employees"]:
        if users.get(name) == user_id:
            mark = "✅ "
        elif name in users:
            mark = "🔒 "
        else:
            mark = ""
        row.append(InlineKeyboardButton(mark + name, callback_data=f"reg:{name}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """У особистих — реєстрація працівника, у групі — довідка."""
    if update.effective_chat.type == "private":
        await cmd_register(update, context)
    else:
        await cmd_help(update, context)


async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text(
            "Реєстрація — в особистих повідомленнях: відкрий чат зі мною, натисни Start і обери себе зі списку."
        )
        return
    await update.message.reply_text(
        "Привіт! 👋 Обери себе зі списку — після цього я зможу тегати тебе в групі "
        "та надсилати короткий опитувальник після твоєї зміни хантера.\n\n"
        "✅ — це ти, 🔒 — ім'я вже закріплене за іншою людиною.",
        reply_markup=build_register_menu(update.effective_user.id),
    )


async def on_register_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    name = query.data.partition(":")[2]
    uid = query.from_user.id
    if name not in STATE["employees"]:
        await query.answer("Такого працівника немає в списку.", show_alert=True)
        return
    holder = STATE["users"].get(name)
    if holder and holder != uid:
        await query.answer("Це ім'я вже закріплене за іншою людиною. Напиши керівнику.", show_alert=True)
        return
    await query.answer()
    for n, u in list(STATE["users"].items()):
        if u == uid and n != name:
            del STATE["users"][n]
    STATE["users"][name] = uid
    # опитування за останню добу, які не вдалось доставити, відправимо тепер
    now = datetime.now(TZ)
    for s in STATE["surveys"].values():
        if (s["employee"] == name and s["status"] == "undelivered"
                and (now - parse_iso(s["end"])).total_seconds() < 24 * 3600):
            s["status"] = "queued"
    save_state(STATE)
    await query.edit_message_text(
        f"✅ Готово, {name}! Тепер я тегатиму тебе в групі й надсилатиму опитування після зміни.\n\n"
        f"Обрав не те ім'я — просто натисни /register ще раз.",
    )


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"Твій Telegram ID: {update.effective_user.id}\n"
        f"Щоб отримувати тижневий звіт — задай його у Railway як змінну OWNER_ID "
        f"і натисни Start у чаті зі мною."
    )


async def cmd_who(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Для керівника: хто вже зареєстрований, а хто ні."""
    if not is_owner(update):
        return
    users = STATE["users"]
    done = [n for n in STATE["employees"] if n in users]
    todo = [n for n in STATE["employees"] if n not in users]
    await update.message.reply_text(
        f"✅ Зареєстровані ({len(done)}):\n" + ("\n".join(f"• {n}" for n in done) or "—")
        + f"\n\n⏳ Ще ні ({len(todo)}):\n" + ("\n".join(f"• {n}" for n in todo) or "—")
    )


async def cmd_unreg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Для керівника: відв'язати працівника, щоб він міг зареєструватись заново."""
    if not is_owner(update):
        return
    name = " ".join(context.args).strip()
    if name in STATE["users"]:
        del STATE["users"][name]
        save_state(STATE)
        await update.message.reply_text(f"🗑 {name} відв'язано. Може зареєструватись знову через /register.")
    else:
        await update.message.reply_text("Використання: /unreg Прізвище Ім'я (як у списку працівників).")


# ---------------- опитування після зміни ----------------

def survey_text(s, q):
    """q — номер питання, з 1."""
    span = f"{fmt_time(s['start'])}–{fmt_time(s['end'])}"
    if q == 1:
        head = (
            f"Привіт, {first_name(s['employee'])}! 👋 Твоя зміна завершена ({span}). "
            f"Оціни її та дай відповіді на питання.\n\n"
        )
    else:
        head = f"Зміна {span}\n\n"
    question = SURVEY_QUESTIONS[q - 1][0]
    if q == 3:
        question += f"\n«{STATE['activity_text']}»"
    return f"{head}{q}/{len(SURVEY_QUESTIONS)}. {question}"


def survey_markup(s, q):
    options = SURVEY_QUESTIONS[q - 1][1]
    row = [
        InlineKeyboardButton(opt, callback_data=f"sv:{s['id']}:{q}:{i}")
        for i, opt in enumerate(options)
    ]
    return InlineKeyboardMarkup([row])


async def survey_dispatch(context: ContextTypes.DEFAULT_TYPE):
    """Раз на 30 сек розсилає опитування, які стоять у черзі, у приватні повідомлення."""
    changed = False
    for s in list(STATE["surveys"].values()):
        if s["status"] != "queued":
            continue
        changed = True
        name = s["employee"]
        uid = STATE["users"].get(name)
        if uid:
            try:
                await context.bot.send_message(
                    chat_id=uid, text=survey_text(s, 1), reply_markup=survey_markup(s, 1)
                )
                s["status"] = "sent"
                s["user_id"] = uid
                s["sent_at"] = now_iso()
                save_state(STATE)
                continue
            except Exception as e:
                log.warning("Не вдалось надіслати опитування %s: %s", name, e)
        s["status"] = "undelivered"
        save_state(STATE)
        await announce(
            context,
            f"📩 {name}, не можу надіслати тобі опитування по зміні. "
            f"Відкрий чат зі мною, натисни Start і обери себе зі списку (/register) — "
            f"опитування прийде одразу.",
            mention=name,
        )
    if changed:
        save_state(STATE)


async def on_survey_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        _, sid, q, idx = query.data.split(":")
        q, idx = int(q), int(idx)
    except ValueError:
        await query.answer()
        return
    s = STATE["surveys"].get(sid)
    if (not s or s["status"] != "sent" or query.from_user.id != s.get("user_id")
            or q != len(s["answers"]) + 1 or q > len(SURVEY_QUESTIONS)
            or not 0 <= idx < len(SURVEY_QUESTIONS[q - 1][1])):
        await query.answer()  # повторне натискання або чуже опитування
        return
    await query.answer()
    s["answers"][str(q)] = idx
    if q < len(SURVEY_QUESTIONS):
        save_state(STATE)
        await query.edit_message_text(survey_text(s, q + 1), reply_markup=survey_markup(s, q + 1))
        return
    s["status"] = "done"
    s["answered_at"] = now_iso()
    save_state(STATE)
    await query.edit_message_text("Дякую! ✅ Відповіді записано. Гарного дня 💪")


# ---------------- тижневий звіт ----------------

def survey_flags(s):
    """Підозрілі розбіжності у відповідях — щоб керівник міг уточнити в людини."""
    a = s["answers"]
    flags = []
    if len(a) >= 2 and Q2_LOW[a["2"]] > Q1_HIGH[a["1"]]:
        flags.append("покупок більше, ніж розмов")
    if a.get("1") == 0 and s["minutes"] >= 180:
        flags.append(f"0 розмов за {fmt_duration(s['minutes'] * 60)}")
    return flags


def survey_line(s):
    day = WEEKDAYS[parse_iso(s["start"]).weekday()]
    head = f"{day} {fmt_time(s['start'])}–{fmt_time(s['end'])} {s['employee']}"
    if s["status"] == "done":
        a = s["answers"]
        opts = [SURVEY_QUESTIONS[i][1][a[str(i + 1)]] for i in range(len(SURVEY_QUESTIONS))]
        line = f"• {head}: розмов {opts[0]} · покупок {opts[1]} · про акцію {opts[2]} · оцінка {opts[3]}/5"
        flags = survey_flags(s)
        if flags:
            line += " ⚠️ " + "; ".join(flags)
        return line
    if s["status"] == "undelivered":
        return f"• {head}: 📭 опитування не доставлено (не зареєстрований у боті)"
    return f"• {head}: ⏳ без відповіді"


def build_report(start, end):
    """Текст звіту за період [start, end)."""
    segs = [g for g in STATE["segments"] if start <= parse_iso(g["start"]) < end]
    surveys = sorted(
        (s for s in STATE["surveys"].values() if start <= parse_iso(s["start"]) < end),
        key=lambda s: s["start"],
    )
    title = f"📊 Звіт Hunter J087: {start:%d.%m}–{(end - timedelta(seconds=1)):%d.%m}"
    if not segs:
        return title + "\n\nЗа цей період хантерства не було."

    total_min = sum(g["minutes"] for g in segs)
    days = {parse_iso(g["start"]).date() for g in segs}
    span_days = max(1, round((end - start).total_seconds() / 86400))

    # check-in статистика по людях — із завершених і активних змін
    sessions = [e for e in STATE["log"] if start <= parse_iso(e["start_time"]) < end]
    sessions += [s for _, s in active_sessions() if start <= parse_iso(s["start_time"]) < end]
    checks = {}
    for sess in sessions:
        for emp, st in sess.get("stats", {}).items():
            c = checks.setdefault(emp, {"sent": 0, "ok": 0})
            c["sent"] += st["sent"]
            c["ok"] += st["ok"]

    per = {}
    for g in segs:
        p = per.setdefault(g["employee"], {"min": 0, "n": 0})
        p["min"] += g["minutes"]
        p["n"] += 1
    sv_per = {}
    for s in surveys:
        p = sv_per.setdefault(s["employee"], {"all": 0, "done": 0})
        p["all"] += 1
        p["done"] += s["status"] == "done"

    lines = [
        title,
        "",
        f"Хантерство: {fmt_duration(total_min * 60)} · відрізків зміни: {len(segs)} · "
        f"днів із хантером: {len(days)} з {span_days}",
        "",
        "По людях:",
    ]
    for emp, p in sorted(per.items(), key=lambda kv: -kv[1]["min"]):
        extra = []
        c = checks.get(emp)
        if c and c["sent"]:
            extra.append(f"«ти на місці?» {c['ok']}/{c['sent']}")
        sp = sv_per.get(emp)
        if sp:
            extra.append(f"опитування {sp['done']}/{sp['all']}")
        tail = (" · " + " · ".join(extra)) if extra else ""
        lines.append(f"• {emp} — {fmt_duration(p['min'] * 60)} ({p['n']} відр.){tail}")

    if surveys:
        done = sum(s["status"] == "done" for s in surveys)
        lines += ["", f"Опитування після зміни (від {SURVEY_MIN_MINUTES // 60} год): "
                      f"відповіли {done} з {len(surveys)}"]
        shown = surveys[:30]
        lines += [survey_line(s) for s in shown]
        if len(surveys) > len(shown):
            lines.append(f"… і ще {len(surveys) - len(shown)}")
    else:
        lines += ["", f"Опитувань не було (відрізків зміни від {SURVEY_MIN_MINUTES // 60} год не набралось)."]
    return "\n".join(lines)


async def send_report(context: ContextTypes.DEFAULT_TYPE, start, end, chat_id=None) -> bool:
    target = chat_id or (int(OWNER_ID) if OWNER_ID.isdigit() else None)
    if not target:
        return False
    text = build_report(start, end)
    try:
        # Telegram ліміт — 4096 символів на повідомлення
        while text:
            cut = len(text) if len(text) <= 3900 else text.rfind("\n", 0, 3900)
            cut = cut if cut > 0 else 3900
            await context.bot.send_message(chat_id=target, text=text[:cut])
            text = text[cut:].lstrip("\n")
        return True
    except Exception as e:
        log.warning("Не вдалось надіслати звіт (чи натиснув власник Start у боті?): %s", e)
        return False


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Для керівника: звіт за останні 7 днів, коли потрібно."""
    if not is_owner(update):
        return
    now = datetime.now(TZ)
    await send_report(context, now - timedelta(days=7), now, chat_id=update.effective_chat.id)


async def weekly_report_job(context: ContextTypes.DEFAULT_TYPE):
    """У понеділок після 09:30 надсилає власнику звіт за минулий тиждень (раз на тиждень).
    Перевірка щопівгодини, тому якщо бот був вимкнений у понеділок — звіт прийде, щойно ввімкнеться."""
    if not OWNER_ID:
        return
    now = datetime.now(TZ)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    key = monday.strftime("%Y-%m-%d")
    if STATE.get("last_report_week") is None:
        # перший запуск після оновлення — не шлемо порожній звіт, чекаємо понеділок
        STATE["last_report_week"] = key
        save_state(STATE)
        return
    if STATE["last_report_week"] == key or now < monday.replace(hour=9, minute=30):
        return
    if await send_report(context, monday - timedelta(days=7), monday):
        STATE["last_report_week"] = key
        save_state(STATE)


# ---------------- фонові перевірки ----------------

async def check_session(context: ContextTypes.DEFAULT_TYPE, slot: str, session: dict, now: datetime):
    """Автозавершення, нагадування 'ти на місці?' і питання про втому для однієї зміни."""
    chat_id = STATE["chat_id"]
    label = slot_label(slot)
    sfx = "2" if slot == "second" else ""

    # авто-завершення після 22:00 — працівники інколи забувають закрити зміну вручну
    if now.hour >= AUTO_END_HOUR:
        who = session["employee"]
        _, duration = do_end(slot)
        await announce(
            context,
            f"🔴 Зміну автоматично завершено о {AUTO_END_HOUR}:00 ({who}){label}. "
            f"Тривалість: {fmt_duration(duration)}\n"
            f"⚠️ Не забувай завершувати хантерство вручну наступного разу.",
        )
        await ensure_pinned_menu(context)
        return

    elapsed = (now - parse_iso(session["last_check_in"])).total_seconds()
    threshold = STATE["interval_hours"] * 3600
    if elapsed >= threshold:
        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("✅ Так, на місці й проінформував", callback_data="checkin" + sfx)]]
        )
        await context.bot.send_message(
            chat_id=chat_id,
            message_thread_id=STATE.get("thread_id"),
            text=(
                f"⏰ {tag(session['employee'])}, ти на місці?{label}\n"
                f"Активність: {html.escape(STATE['activity_text'])}\n\n"
                f"Проінформував покупців про нашу активність?"
            ),
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        # позначаємо момент відправки нагадування, щоб не дублювати його
        # щохвилини, поки хантер не підтвердить кнопкою
        session["last_check_in"] = now_iso()
        st = session.setdefault("stats", {}).setdefault(session["employee"], {"sent": 0, "ok": 0})
        st["sent"] += 1
        save_state(STATE)

    # питання про втому — кожні FATIGUE_INTERVAL_HOURS годин від початку зміни
    # (для першого хантера передача зміни цей відлік не скидає)
    if slot == "main":
        shift_start = STATE.get("shift_started_at")
        last_fslot = STATE.get("last_fatigue_slot", -1)
    else:
        shift_start = session.get("start_time")
        last_fslot = session.get("fatigue_slot", -1)
    if shift_start:
        elapsed_since_start = (now - parse_iso(shift_start)).total_seconds()
        fslot = int(elapsed_since_start // (FATIGUE_INTERVAL_HOURS * 3600))
        if fslot > 0 and fslot > last_fslot:
            fatigue_keyboard = InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("✅ Все ок, продовжую", callback_data="fatigue_ok" + sfx)],
                    [InlineKeyboardButton("🔄 Так, хочу передати зміну", callback_data="fatigue_transfer" + sfx)],
                ]
            )
            await context.bot.send_message(
                chat_id=chat_id,
                message_thread_id=STATE.get("thread_id"),
                text=(
                    f"⏰ {tag(session['employee'])}, ти вже {FATIGUE_INTERVAL_HOURS} год на хантерстві.{label}\n"
                    f"Не втомився? Можливо, час передати зміну наступному хантеру?"
                ),
                reply_markup=fatigue_keyboard,
                parse_mode="HTML",
            )
            if slot == "main":
                STATE["last_fatigue_slot"] = fslot
            else:
                session["fatigue_slot"] = fslot
            save_state(STATE)


async def periodic_check(context: ContextTypes.DEFAULT_TYPE):
    ensure_today_reset()
    if STATE["chat_id"] is None:
        return
    # якщо про відбій забули — знімаємо тривогу самі, інакше всі нагадування стоять на паузі
    await maybe_expire_air_raid(context)
    if STATE.get("air_raid_active"):
        return  # на час тривоги всі автоматичні нагадування призупинені
    chat_id = STATE["chat_id"]

    sessions = active_sessions()
    if sessions:
        now = datetime.now(TZ)
        for slot, session in sessions:
            await check_session(context, slot, session, now)
        return

    # немає жодного активного хантера — з 13:00 нагадуємо щоразу через 15 хв,
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
    if not any_active():
        # відразу відкриваємо вибір "хто починає" в закріпленому меню
        await push_pinned(
            context,
            "Хто починає хантерство? Обери зі списку:",
            build_employee_menu("start_emp", exclude=busy_names()),
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
        BotCommand("register", "Зареєструватись (теги й опитування)"),
        BotCommand("help", "Список команд"),
    ])


def main():
    if not BOT_TOKEN:
        raise SystemExit("Задай змінну середовища BOT_TOKEN з токеном від BotFather")

    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("register", cmd_register))
    app.add_handler(CommandHandler("myid", cmd_myid))
    app.add_handler(CommandHandler("who", cmd_who))
    app.add_handler(CommandHandler("unreg", cmd_unreg))
    app.add_handler(CommandHandler("report", cmd_report))
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
    app.add_handler(CallbackQueryHandler(on_checkin_button, pattern="^checkin2?$"))
    app.add_handler(CallbackQueryHandler(on_register_callback, pattern="^reg:"))
    app.add_handler(CallbackQueryHandler(on_survey_callback, pattern="^sv:"))
    app.add_handler(CallbackQueryHandler(on_menu_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_input))

    jq = app.job_queue
    jq.run_repeating(periodic_check, interval=300, first=10)  # кожні 5 хв
    jq.run_repeating(check_air_raid_api, interval=90, first=15)  # авто-тривога, кожні 90 сек
    jq.run_repeating(survey_dispatch, interval=30, first=20)  # опитування після зміни
    jq.run_repeating(weekly_report_job, interval=1800, first=60)  # тижневий звіт власнику
    jq.run_daily(morning_prompt, time=dtime(hour=9, minute=0, tzinfo=TZ))
    jq.run_daily(noon_broadcast, time=dtime(hour=12, minute=0, tzinfo=TZ))

    log.info("Bot starting...")
    app.run_polling()


if __name__ == "__main__":
    main()
