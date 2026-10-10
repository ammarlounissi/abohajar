"""شجرة بوت المصنع: «إضافة منتج» و«تعديل منتج».

تعمل فقط لرقم مسجَّل كمصنع (البوابة في webhook_handler._find_factory قبل أي استدعاء هنا).
هوية المصنع تأتي من رقم المرسل في الـ webhook الموقَّع، ولا يُقرأ أي معرّف مصنع من نص الرسالة.

آلة الحالات: صف واحد لكل مصنع في bot_sessions (state + draft). مسودة الإضافة لا تُكتب في جدول
المنتجات إلا عند «تأكيد». كل استعلام عن منتج يشترط factory_id، فلا يصل مصنع إلى منتج غيره.
الرسائل المتتابعة (عدة صور دفعة واحدة) تُسلسَل بقفل لكل مصنع كي لا تضيع مسودة بسبب سباق (ضمن عملية
واحدة؛ إن شُغّل أكثر من worker فيلزم قفل في القاعدة).

أوامر عامة تعمل في أي خطوة:  إلغاء، رجوع، القائمة.
"""
import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Awaitable, Callable, Dict, List, Optional

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import models, schemas
from app.core.config import settings
from app.services import media
from app.services.bot_text import (
    BACK_WORDS, CANCEL_WORDS, MENU_WORDS, clean_description, clean_title,
    format_sku, next_number, norm_command, parse_index, parse_price,
)
from app.services.meta import sync_product_to_meta
from app.services.products import default_description
from app.services.whatsapp import (
    download_whatsapp_media, send_whatsapp_buttons, send_whatsapp_list, send_whatsapp_message,
)

logger = logging.getLogger(__name__)

SESSION_TTL = timedelta(minutes=30)     # الخمول: بعدها تُلغى المسودة وتعود القائمة
MAX_IMAGES = 8
PAGE_SIZE = 8                           # + صفّا السابق/التالي = 10 (حد قوائم واتساب)

SKU_HELP = "رمز المنتج حروف لاتينية كبيرة متبوعة بأرقام فقط، مثل B001 أو PTK012."

_locks: Dict[int, asyncio.Lock] = {}


@dataclass
class Event:
    kind: str                 # text | choice | image | video
    text: str = ""
    choice: str = ""
    media_id: str = ""


def parse_event(message: dict) -> Optional[Event]:
    """يحوّل رسالة واتساب الخام إلى حدث. الأزرار والقوائم التي لا تخصنا (بادئة fb:) تُهمَل."""
    kind = message.get("type")
    if kind == "text":
        return Event("text", text=message.get("text", {}).get("body", "") or "")
    if kind == "interactive":
        inter = message.get("interactive", {})
        reply = inter.get("button_reply") or inter.get("list_reply") or {}
        choice = reply.get("id", "")
        return Event("choice", choice=choice) if choice.startswith("fb:") else None
    if kind in ("image", "video"):
        media_id = message.get(kind, {}).get("id")
        return Event(kind, media_id=media_id) if media_id else None
    return None


@dataclass
class Ctx:
    db: Session
    factory: models.Factory
    phone: str
    session: Optional[models.BotSession] = None

    @property
    def draft(self) -> dict:
        return dict(self.session.draft or {}) if self.session else {}

    async def say(self, text: str) -> None:
        await send_whatsapp_message(self.phone, text)

    async def buttons(self, text: str, buttons) -> None:
        await send_whatsapp_buttons(self.phone, text[:1024], buttons)

    async def rows(self, text: str, label: str, rows, title: str) -> None:
        await send_whatsapp_list(self.phone, text, label, rows, title)


# ---------------------------------------------------------------- الجلسة

def get_session(db: Session, factory: models.Factory) -> Optional[models.BotSession]:
    session = db.query(models.BotSession).filter(models.BotSession.factory_id == factory.id).first()
    if session and datetime.utcnow() - session.updated_at > SESSION_TTL:
        db.delete(session)
        db.commit()
        return None
    return session


def has_session(db: Session, factory: models.Factory) -> bool:
    return get_session(db, factory) is not None


def _clear(ctx: Ctx) -> None:
    if ctx.session is not None:
        ctx.db.delete(ctx.session)
        ctx.db.commit()
        ctx.session = None


def _store(ctx: Ctx, state: Optional[str], updates: dict) -> None:
    draft = ctx.draft
    for key, value in updates.items():
        if value is None:
            draft.pop(key, None)
        else:
            draft[key] = value
    now = datetime.utcnow()
    if ctx.session is None:
        ctx.session = models.BotSession(
            factory_id=ctx.factory.id, state=state, draft=draft, updated_at=now,
        )
        ctx.db.add(ctx.session)
    else:
        if state:
            ctx.session.state = state
        ctx.session.draft = draft          # نعيّن قاموساً جديداً كي يُكتشف التغيير في عمود JSON
        ctx.session.updated_at = now
    ctx.db.commit()


def save_draft(ctx: Ctx, **updates) -> None:
    _store(ctx, None, updates)


async def enter(ctx: Ctx, state: str, **updates) -> None:
    _store(ctx, state, updates)
    await PROMPTS[state](ctx)


# ---------------------------------------------------------------- مساعدات

def factory_categories(ctx: Ctx) -> List[str]:
    """أصناف المصنع: المعرَّفة له، وإلا المشتقة من منتجاته الحالية. فارغة = لا يُسمح بالإضافة."""
    declared = {c for c in (ctx.factory.categories or []) if isinstance(c, str) and re.fullmatch(r"[A-Z]+", c)}
    if declared:
        return sorted(declared)
    derived = set()
    for (sku,) in ctx.db.query(models.Product.sku).filter(models.Product.factory_id == ctx.factory.id):
        m = re.match(r"[A-Z]+", sku or "")
        if m:
            derived.add(m.group(0))
    return sorted(derived)


def owned_product(ctx: Ctx, sku: str) -> Optional[models.Product]:
    return (
        ctx.db.query(models.Product)
        .filter(models.Product.sku == (sku or "").strip().upper(),
                models.Product.factory_id == ctx.factory.id)
        .first()
    )


def availability_ar(value) -> str:
    return "متوفر" if value == models.Availability.IN_STOCK else "غير متوفر"


def suggest_sku(ctx: Ctx, category: str) -> str:
    skus = [s for (s,) in ctx.db.query(models.Product.sku).filter(models.Product.sku.like(f"{category}%"))]
    number = next_number(skus, category)
    while True:
        sku = format_sku(category, number)
        taken = ctx.db.query(models.Product.id).filter(models.Product.sku == sku).first()
        if not taken and not media.has_media(sku):
            return sku
        number += 1


def product_summary(product: models.Product) -> str:
    images = len(media.list_images(product.sku)) if media.is_valid_media_sku(product.sku) else 0
    return (
        f"📦 {product.title}\n🏷️ الرمز: {product.sku}\n💰 السعر: {product.price:g} دج\n"
        f"📍 التوفر: {availability_ar(product.availability)}\n"
        f"🖼️ الصور: {images} | 🎬 الفيديو: {'موجود' if product.video_url else 'لا يوجد'}"
    )


def draft_summary(d: dict) -> str:
    desc = d.get("desc")
    return (
        "📋 *ملخص المنتج الجديد*\n\n"
        f"🏷️ الرمز: {d.get('sku')}\n📦 الاسم: {d.get('title')}\n💰 السعر: {d.get('price'):g} دج\n"
        f"📝 الوصف: {desc if desc else '(تلقائي)'}\n"
        f"🖼️ الصور: {len(d.get('images', []))}\n🎬 الفيديو: {'نعم' if d.get('video') else 'لا'}\n"
        f"📍 التوفر: {'متوفر' if d.get('availability') == 'in stock' else 'غير متوفر'}"
    )


async def reprompt(ctx: Ctx, hint: Optional[str] = None) -> None:
    if hint:
        await ctx.say(hint)
    await PROMPTS[ctx.session.state](ctx)


async def show_menu(ctx: Ctx, note: Optional[str] = None) -> None:
    _clear(ctx)
    if note:
        text = f"{note}\n\nماذا تريد أن تفعل؟"
    else:
        text = (f"أهلاً بك مصنع ({ctx.factory.name}) 🏛️\nماذا تريد أن تفعل؟\n"
                "(اكتب «إلغاء» أو «رجوع» في أي خطوة)")
    await ctx.buttons(text, [("fb:add", "➕ إضافة منتج"), ("fb:edit", "✏️ تعديل منتج")])


# ---------------------------------------------------------------- شجرة «إضافة منتج»

async def start_add(ctx: Ctx) -> None:
    _clear(ctx)
    cats = factory_categories(ctx)
    if not cats:
        await ctx.say("لم تُحدَّد أصناف لمصنعك بعد. تواصل مع الإدارة لتحديدها ثم أعد المحاولة.")
        return
    if len(cats) == 1:
        await enter(ctx, "add_sku", category=cats[0], images=[])
    else:
        await enter(ctx, "add_cat", images=[])


async def p_add_cat(ctx: Ctx) -> None:
    cats = factory_categories(ctx)
    text = "➕ *إضافة منتج*\nاختر صنف المنتج:"
    if len(cats) <= 3:
        await ctx.buttons(text, [(f"fb:cat:{c}", f"الصنف {c}") for c in cats])
    else:
        await ctx.rows(text, "الأصناف", [(f"fb:cat:{c}", f"الصنف {c}", "") for c in cats[:10]], "أصنافك")


async def h_add_cat(ctx: Ctx, ev: Event) -> None:
    if ev.kind == "choice" and ev.choice.startswith("fb:cat:"):
        cat = ev.choice[len("fb:cat:"):]
        if cat in factory_categories(ctx):
            await enter(ctx, "add_sku", category=cat)
            return
    await reprompt(ctx)


async def p_add_sku(ctx: Ctx) -> None:
    cat = ctx.draft["category"]
    await ctx.buttons(
        f"🏷️ رمز المنتج (الصنف {cat})\nالرمز التلقائي المقترح: *{suggest_sku(ctx, cat)}*",
        [("fb:sku:auto", "رمز تلقائي"), ("fb:sku:manual", "إدخال رمز يدوي")],
    )


async def h_add_sku(ctx: Ctx, ev: Event) -> None:
    if ev.kind == "choice" and ev.choice == "fb:sku:auto":
        await enter(ctx, "add_title", sku=suggest_sku(ctx, ctx.draft["category"]))
    elif ev.kind == "choice" and ev.choice == "fb:sku:manual":
        await enter(ctx, "add_sku_manual")
    elif ev.kind == "text":
        await _validate_manual_sku(ctx, ev.text)     # كتابة الرمز مباشرة تكفي
    else:
        await reprompt(ctx)


async def p_add_sku_manual(ctx: Ctx) -> None:
    cat = ctx.draft["category"]
    await ctx.say(f"اكتب رمز المنتج، ويبدأ بحرف الصنف ({cat}). مثال: {format_sku(cat, 14)}")


async def h_add_sku_manual(ctx: Ctx, ev: Event) -> None:
    if ev.kind == "text":
        await _validate_manual_sku(ctx, ev.text)
    else:
        await reprompt(ctx)


async def _validate_manual_sku(ctx: Ctx, text: str) -> None:
    sku = (text or "").strip().upper()
    cat = ctx.draft["category"]
    if not media.is_valid_media_sku(sku):
        await ctx.say(f"⚠️ رمز غير صالح.\n{SKU_HELP}")
        return
    if media.category_of(sku) != cat:
        await ctx.say(f"⚠️ الرمز يجب أن يبدأ بحرف الصنف ({cat}).")
        return
    existing = ctx.db.query(models.Product).filter(models.Product.sku == sku).first()
    if existing and existing.factory_id == ctx.factory.id:
        await ctx.buttons(
            f"الرمز ({sku}) موجود عندك مسبقاً: {existing.title}.\nيمكنك تعديله، أو اكتب رمزاً آخر.",
            [(f"fb:ep:{sku}", "✏️ تعديل هذا المنتج")],
        )
        return
    if existing or media.has_media(sku):
        await ctx.say(f"⚠️ الرمز ({sku}) مستخدم مسبقاً، اكتب رمزاً آخر.")
        return
    await enter(ctx, "add_title", sku=sku)


async def p_add_title(ctx: Ctx) -> None:
    await ctx.say(f"📦 اكتب *اسم المنتج* (الرمز: {ctx.draft['sku']}):")


async def h_add_title(ctx: Ctx, ev: Event) -> None:
    title = clean_title(ev.text) if ev.kind == "text" else None
    if title is None:
        await reprompt(ctx, "⚠️ الاسم يجب أن يكون نصاً بين حرفين و255 حرفاً.")
        return
    await enter(ctx, "add_price", title=title)


async def p_add_price(ctx: Ctx) -> None:
    await ctx.say("💰 اكتب *السعر بالدينار* (رقم فقط). مثال: 3200")


async def h_add_price(ctx: Ctx, ev: Event) -> None:
    price = parse_price(ev.text) if ev.kind == "text" else None
    if price is None:
        await reprompt(ctx, "⚠️ السعر يجب أن يكون رقماً أكبر من الصفر.")
        return
    await enter(ctx, "add_desc", price=price)


async def p_add_desc(ctx: Ctx) -> None:
    await ctx.buttons("📝 اكتب *وصف المنتج* (اختياري)، أو اضغط تخطي لوصف تلقائي.", [("fb:skip", "تخطي")])


async def h_add_desc(ctx: Ctx, ev: Event) -> None:
    if ev.kind == "choice" and ev.choice == "fb:skip":
        await enter(ctx, "add_images", desc=None)
    elif ev.kind == "text" and clean_description(ev.text):
        await enter(ctx, "add_images", desc=clean_description(ev.text))
    else:
        await reprompt(ctx, "⚠️ الوصف يجب ألا يتجاوز 1500 حرف.")


async def p_add_images(ctx: Ctx) -> None:
    await ctx.buttons(
        f"🖼️ أرسل *صور المنتج* (حتى {MAX_IMAGES}). الصورة الأولى هي الرئيسية.\nاضغط «تم» عند الانتهاء.",
        [("fb:done", "✅ تم")],
    )


async def h_add_images(ctx: Ctx, ev: Event) -> None:
    images = list(ctx.draft.get("images", []))
    if ev.kind == "image":
        if ev.media_id in images:
            return
        if len(images) >= MAX_IMAGES:
            await ctx.buttons(f"⚠️ الحد الأقصى {MAX_IMAGES} صور.", [("fb:done", "✅ تم")])
            return
        images.append(ev.media_id)
        save_draft(ctx, images=images)
        await ctx.buttons(f"✅ استلمت {len(images)} صورة. أرسل المزيد أو اضغط «تم».", [("fb:done", "✅ تم")])
    elif ev.kind == "choice" and ev.choice == "fb:done":
        if not images:
            await reprompt(ctx, "⚠️ أرسل صورة واحدة على الأقل.")
        else:
            await enter(ctx, "add_video")
    else:
        await reprompt(ctx)


async def p_add_video(ctx: Ctx) -> None:
    await ctx.buttons(
        "🎬 أرسل *فيديو قصيراً* للمنتج (MP4، اختياري)، أو اضغط تخطي.", [("fb:skip", "تخطي")],
    )


async def h_add_video(ctx: Ctx, ev: Event) -> None:
    if ev.kind == "video":
        await enter(ctx, "add_avail", video=ev.media_id)
    elif ev.kind == "choice" and ev.choice == "fb:skip":
        await enter(ctx, "add_avail", video=None)
    else:
        await reprompt(ctx)


async def p_add_avail(ctx: Ctx) -> None:
    await ctx.buttons("📍 هل المنتج متوفر الآن؟", [("fb:av:in", "✅ متوفر"), ("fb:av:out", "⛔ غير متوفر")])


async def h_add_avail(ctx: Ctx, ev: Event) -> None:
    if ev.kind == "choice" and ev.choice in ("fb:av:in", "fb:av:out"):
        value = "in stock" if ev.choice == "fb:av:in" else "out of stock"
        await enter(ctx, "add_confirm", availability=value)
    else:
        await reprompt(ctx)


async def p_add_confirm(ctx: Ctx) -> None:
    await ctx.buttons(draft_summary(ctx.draft), [("fb:ok", "✅ تأكيد"), ("fb:cancel", "❌ إلغاء")])


async def h_add_confirm(ctx: Ctx, ev: Event) -> None:
    if ev.kind == "choice" and ev.choice == "fb:ok":
        await commit_new_product(ctx)
    else:
        await reprompt(ctx)


async def commit_new_product(ctx: Ctx) -> None:
    d = ctx.draft
    sku = d["sku"]
    if ctx.db.query(models.Product.id).filter(models.Product.sku == sku).first() or media.has_media(sku):
        await show_menu(ctx, f"⚠️ الرمز ({sku}) أُخذ قبل لحظات ولم يُحفظ شيء. ابدأ من جديد.")
        return

    # نحمّل كل الوسائط أولاً: لو فشلت إحداها تبقى المسودة كما هي ولا يُكتب شيء على القرص
    contents = []
    for media_id in d.get("images", []):
        content = await download_whatsapp_media(media_id)
        if not content or media.detect_image_extension(content) is None:
            await ctx.say("⚠️ تعذر تحميل إحدى الصور أو أنها بصيغة غير مدعومة (المقبول JPEG وPNG وWebP). أعد إرسال الصور.")
            await enter(ctx, "add_images", images=[])
            return
        contents.append(content)

    video, warning = None, ""
    if d.get("video"):
        video = await download_whatsapp_media(d["video"])
        if not video or media.detect_video_extension(video) is None:
            video, warning = None, "\n⚠️ تعذر حفظ الفيديو، يمكنك إضافته من «تعديل منتج»."

    try:
        for index, content in enumerate(contents, start=1):
            media.save_image(sku, index, content)
        video_url = media.save_video(sku, video) if video else None
        urls = media.image_urls(sku)
        data = schemas.ProductBase(
            sku=sku, title=d["title"], price=d["price"],
            description=d.get("desc") or default_description(d["title"], ctx.factory),
            availability=models.Availability(d["availability"]),
            primary_media_url=urls[0], additional_media_urls=urls[1:], video_url=video_url,
        )
        product = models.Product(factory_id=ctx.factory.id, **data.model_dump())
        ctx.db.add(product)
        ctx.db.commit()
    except (ValueError, OSError, ValidationError, IntegrityError, IndexError):
        ctx.db.rollback()
        logger.exception("Failed creating product %s", sku)
        try:
            media.quarantine_product_dir(sku)
        except OSError:
            logger.exception("Failed quarantining folder of %s", sku)
        await ctx.say("⚠️ تعذر حفظ المنتج ولم يُحفظ شيء. حاول مجدداً.")
        await enter(ctx, "add_confirm")
        return

    _clear(ctx)
    result = await sync_product_to_meta(product.id, ctx.db)
    ctx.db.refresh(product)
    synced = result.get("status") == "success"
    link = settings.PRODUCT_LINK_TEMPLATE.format(sku=sku)
    note = (f"✅ تمت إضافة المنتج ومزامنته مع الكتالوج!\n\n{product.title}\n🏷️ {sku}\n🔗 {link}"
            if synced else
            f"✅ حُفظ المنتج ({sku}) لكن تعذرت مزامنته مع ميتا الآن.")
    await show_menu(ctx, note + warning)


# ---------------------------------------------------------------- شجرة «تعديل منتج»

async def start_edit(ctx: Ctx) -> None:
    _clear(ctx)
    await enter(ctx, "edit_pick", page=0)


async def p_edit_pick(ctx: Ctx) -> None:
    query = ctx.db.query(models.Product).filter(models.Product.factory_id == ctx.factory.id)
    total = query.count()
    if total == 0:
        await show_menu(ctx, "ليس لديك منتجات بعد.")
        return
    page = min(max(int(ctx.draft.get("page", 0)), 0), (total - 1) // PAGE_SIZE)
    items = query.order_by(models.Product.sku).offset(page * PAGE_SIZE).limit(PAGE_SIZE).all()
    rows = [
        (f"fb:ep:{p.sku}", f"{p.sku} {p.title}", f"{p.price:g} دج · {availability_ar(p.availability)}")
        for p in items
    ]
    if page > 0:
        rows.append((f"fb:pg:{page - 1}", "⬅️ السابق", f"الصفحة {page}"))
    if (page + 1) * PAGE_SIZE < total:
        rows.append((f"fb:pg:{page + 1}", "التالي ➡️", f"الصفحة {page + 2}"))
    await ctx.rows(
        f"✏️ *تعديل منتج* ({total} منتج)\nاختر المنتج من القائمة، أو اكتب رمزه مباشرة (مثل B001).",
        "المنتجات", rows, "منتجاتك",
    )


async def h_edit_pick(ctx: Ctx, ev: Event) -> None:
    if ev.kind == "choice" and ev.choice.startswith("fb:pg:"):
        page = ev.choice[len("fb:pg:"):]
        await enter(ctx, "edit_pick", page=int(page) if page.isdigit() else 0)
    elif ev.kind == "text":
        await open_product(ctx, ev.text)
    else:
        await reprompt(ctx)


async def open_product(ctx: Ctx, sku: str) -> None:
    product = owned_product(ctx, sku)
    if product is None:
        # الرسالة نفسها لرمز غير موجود ولرمز يخص مصنعاً آخر، فلا نكشف وجود منتجات الغير
        if ctx.session is not None:
            await reprompt(ctx, "⚠️ لم أجد منتجاً بهذا الرمز ضمن منتجاتك.")
        else:
            await ctx.say("⚠️ لم أجد منتجاً بهذا الرمز ضمن منتجاتك.")
        return
    _clear(ctx)                            # مسودة جديدة نظيفة للمنتج المفتوح
    await enter(ctx, "edit_field", sku=product.sku)


def _current(ctx: Ctx) -> Optional[models.Product]:
    return owned_product(ctx, ctx.draft.get("sku", ""))


async def p_edit_field(ctx: Ctx) -> None:
    product = _current(ctx)
    if product is None:
        await show_menu(ctx, "⚠️ المنتج لم يعد متاحاً.")
        return
    rows = [
        ("fb:f:title", "✏️ الاسم", product.title),
        ("fb:f:price", "💰 السعر", f"{product.price:g} دج"),
        ("fb:f:desc", "📝 الوصف", (product.description or "")[:72]),
        ("fb:f:avail", "📍 التوفر", availability_ar(product.availability)),
        ("fb:f:images", "🖼️ الصور", ""),
        ("fb:f:video", "🎬 الفيديو", "موجود" if product.video_url else "لا يوجد"),
        ("fb:f:other", "🔁 منتج آخر", ""),
        ("fb:menu", "🏠 إنهاء", ""),
    ]
    await ctx.rows(f"{product_summary(product)}\n\nماذا تريد أن تعدّل؟", "الحقول", rows, "الحقول")


async def h_edit_field(ctx: Ctx, ev: Event) -> None:
    product = _current(ctx)
    if product is None:
        await show_menu(ctx, "⚠️ المنتج لم يعد متاحاً.")
        return
    choice = ev.choice if ev.kind == "choice" else ""
    if choice in ("fb:f:title", "fb:f:price", "fb:f:desc"):
        await enter(ctx, "edit_text", field=choice[len("fb:f:"):])
    elif choice == "fb:f:avail":
        await enter(ctx, "edit_avail")
    elif choice == "fb:f:images":
        await enter(ctx, "edit_img_menu")
    elif choice == "fb:f:video":
        await enter(ctx, "edit_video")
    elif choice == "fb:f:other":
        await enter(ctx, "edit_pick", page=0)
    else:
        await reprompt(ctx)


FIELD_LABELS = {"title": "الاسم", "price": "السعر", "desc": "الوصف"}


async def apply_changes(ctx: Ctx, product: models.Product, changes: dict, done_msg: str) -> None:
    """يحفظ التعديل فوراً (لا موافقة من الإدارة) ثم يزامن الكتالوج."""
    for key, value in changes.items():
        setattr(product, key, value)
    product.sync_status = models.SyncStatus.PENDING
    ctx.db.commit()
    result = await sync_product_to_meta(product.id, ctx.db)
    ctx.db.refresh(product)
    note = "" if result.get("status") == "success" else "\n⚠️ حُفظ التعديل لكن تعذرت مزامنة ميتا الآن."
    await ctx.say(done_msg + note)


def refresh_media_fields(product: models.Product) -> None:
    """روابط الصور والفيديو تُبنى دائماً من القرص (مصدر الحقيقة الوحيد)."""
    urls = media.image_urls(product.sku)
    if urls:
        product.primary_media_url = urls[0]
        product.additional_media_urls = urls[1:]
    product.video_url = media.video_url(product.sku)


async def p_edit_text(ctx: Ctx) -> None:
    product, field = _current(ctx), ctx.draft.get("field")
    if product is None or field not in FIELD_LABELS:
        await show_menu(ctx, "⚠️ حدث خطأ، ابدأ من جديد.")
        return
    current = {"title": product.title, "price": f"{product.price:g} دج",
               "desc": product.description or "(لا يوجد)"}[field]
    await ctx.say(f"{FIELD_LABELS[field]} الحالي:\n{current}\n\nاكتب القيمة الجديدة:")


async def h_edit_text(ctx: Ctx, ev: Event) -> None:
    product, field = _current(ctx), ctx.draft.get("field")
    if product is None or field not in FIELD_LABELS:
        await show_menu(ctx, "⚠️ حدث خطأ، ابدأ من جديد.")
        return
    if ev.kind != "text":
        await reprompt(ctx)
        return
    if field == "title":
        value, column = clean_title(ev.text), "title"
    elif field == "price":
        value, column = parse_price(ev.text), "price"
    else:
        value, column = clean_description(ev.text), "description"
    if value is None:
        await reprompt(ctx, "⚠️ القيمة غير صالحة.")
        return
    await apply_changes(ctx, product, {column: value}, f"✅ تم تحديث {FIELD_LABELS[field]}.")
    await enter(ctx, "edit_field")


async def p_edit_avail(ctx: Ctx) -> None:
    product = _current(ctx)
    state = availability_ar(product.availability) if product else ""
    await ctx.buttons(f"📍 التوفر الحالي: {state}\nاختر الحالة الجديدة:",
                      [("fb:av:in", "✅ متوفر"), ("fb:av:out", "⛔ غير متوفر")])


async def h_edit_avail(ctx: Ctx, ev: Event) -> None:
    product = _current(ctx)
    if product is None or ev.kind != "choice" or ev.choice not in ("fb:av:in", "fb:av:out"):
        await reprompt(ctx)
        return
    value = models.Availability.IN_STOCK if ev.choice == "fb:av:in" else models.Availability.OUT_OF_STOCK
    await apply_changes(ctx, product, {"availability": value}, f"✅ التوفر الآن: {availability_ar(value)}.")
    await enter(ctx, "edit_field")


async def p_edit_img_menu(ctx: Ctx) -> None:
    count = len(media.list_images(ctx.draft["sku"]))
    await ctx.buttons(
        f"🖼️ عدد الصور: {count} (الأولى هي الرئيسية)\nماذا تريد؟",
        [("fb:img:add", "➕ إضافة"), ("fb:img:rep", "🔄 استبدال"), ("fb:img:del", "🗑️ حذف")],
    )


async def h_edit_img_menu(ctx: Ctx, ev: Event) -> None:
    count = len(media.list_images(ctx.draft.get("sku", "")))
    choice = ev.choice if ev.kind == "choice" else ""
    if choice == "fb:img:add":
        if count >= MAX_IMAGES:
            await ctx.say(f"⚠️ وصلت الحد الأقصى ({MAX_IMAGES} صور). احذف صورة أو استبدل واحدة.")
            await reprompt(ctx)
        else:
            await enter(ctx, "edit_img_add", images=[])
    elif choice == "fb:img:rep":
        if count == 0:
            await reprompt(ctx, "⚠️ لا توجد صور محفوظة لهذا المنتج؛ استعمل «إضافة».")
        else:
            await enter(ctx, "edit_img_rep_n")
    elif choice == "fb:img:del":
        if count <= 1:
            await reprompt(ctx, "⚠️ للمنتج صورة واحدة فقط ولا يمكن حذفها؛ يمكنك استبدالها.")
        else:
            await enter(ctx, "edit_img_del_n")
    else:
        await reprompt(ctx)


async def p_edit_img_add(ctx: Ctx) -> None:
    room = MAX_IMAGES - len(media.list_images(ctx.draft["sku"]))
    await ctx.buttons(f"➕ أرسل الصور الجديدة (حتى {room})، ثم اضغط «تم».", [("fb:done", "✅ تم")])


async def h_edit_img_add(ctx: Ctx, ev: Event) -> None:
    product = _current(ctx)
    if product is None:
        await show_menu(ctx, "⚠️ المنتج لم يعد متاحاً.")
        return
    existing = len(media.list_images(product.sku))
    pending = list(ctx.draft.get("images", []))
    if ev.kind == "image":
        if ev.media_id in pending:
            return
        if existing + len(pending) >= MAX_IMAGES:
            await ctx.buttons(f"⚠️ الحد الأقصى {MAX_IMAGES} صور.", [("fb:done", "✅ تم")])
            return
        pending.append(ev.media_id)
        save_draft(ctx, images=pending)
        await ctx.buttons(f"✅ استلمت {len(pending)} صورة. أرسل المزيد أو اضغط «تم».", [("fb:done", "✅ تم")])
        return
    if not (ev.kind == "choice" and ev.choice == "fb:done"):
        await reprompt(ctx)
        return
    if not pending:
        await reprompt(ctx, "⚠️ لم تُرسل أي صورة.")
        return

    contents = []
    for media_id in pending:
        content = await download_whatsapp_media(media_id)
        if not content or media.detect_image_extension(content) is None:
            await ctx.say("⚠️ تعذر تحميل إحدى الصور أو أنها بصيغة غير مدعومة. أعد إرسالها.")
            await enter(ctx, "edit_img_add", images=[])
            return
        contents.append(content)
    try:
        for offset, content in enumerate(contents, start=1):
            media.save_image(product.sku, existing + offset, content)
    except (ValueError, OSError):
        logger.exception("Failed adding images to %s", product.sku)
        await ctx.say("⚠️ تعذر حفظ بعض الصور.")
    refresh_media_fields(product)
    await apply_changes(ctx, product, {}, f"✅ أصبح عدد الصور {len(media.list_images(product.sku))}.")
    await enter(ctx, "edit_field")


async def p_edit_img_rep_n(ctx: Ctx) -> None:
    count = len(media.list_images(ctx.draft["sku"]))
    await ctx.say(f"🔄 اكتب *رقم* الصورة التي تريد استبدالها (من 1 إلى {count}).")


async def h_edit_img_rep_n(ctx: Ctx, ev: Event) -> None:
    count = len(media.list_images(ctx.draft.get("sku", "")))
    index = parse_index(ev.text, count) if ev.kind == "text" else None
    if index is None:
        await reprompt(ctx, "⚠️ رقم غير صحيح.")
        return
    await enter(ctx, "edit_img_rep_wait", index=index)


async def p_edit_img_rep_wait(ctx: Ctx) -> None:
    await ctx.say(f"أرسل الصورة الجديدة لتحل محل الصورة رقم {ctx.draft['index']} (تُحفظ القديمة نسخةً احتياطية).")


async def h_edit_img_rep_wait(ctx: Ctx, ev: Event) -> None:
    product = _current(ctx)
    if product is None or ev.kind != "image":
        await reprompt(ctx)
        return
    content = await download_whatsapp_media(ev.media_id)
    if not content or media.detect_image_extension(content) is None:
        await reprompt(ctx, "⚠️ تعذر تحميل الصورة أو أنها بصيغة غير مدعومة (JPEG أو PNG أو WebP). أعد إرسالها.")
        return
    index = int(ctx.draft["index"])
    try:
        media.save_image(product.sku, index, content)
    except (ValueError, OSError):
        logger.exception("Failed replacing image %s of %s", index, product.sku)
        await reprompt(ctx, "⚠️ تعذر حفظ الصورة، أعد إرسالها.")
        return
    refresh_media_fields(product)
    await apply_changes(ctx, product, {}, f"✅ تم استبدال الصورة رقم {index}.")
    await enter(ctx, "edit_field")


async def p_edit_img_del_n(ctx: Ctx) -> None:
    count = len(media.list_images(ctx.draft["sku"]))
    await ctx.say(f"🗑️ اكتب *رقم* الصورة التي تريد حذفها (من 1 إلى {count}). تُحفظ نسخة احتياطية منها.")


async def h_edit_img_del_n(ctx: Ctx, ev: Event) -> None:
    product = _current(ctx)
    count = len(media.list_images(ctx.draft.get("sku", "")))
    index = parse_index(ev.text, count) if ev.kind == "text" else None
    if product is None or index is None or count <= 1:
        await reprompt(ctx, "⚠️ رقم غير صحيح.")
        return
    if not media.delete_image(product.sku, index):
        await reprompt(ctx, "⚠️ الصورة غير موجودة.")
        return
    refresh_media_fields(product)
    await apply_changes(ctx, product, {}, f"✅ تم حذف الصورة رقم {index} وأُعيد الترقيم.")
    await enter(ctx, "edit_field")


async def p_edit_video(ctx: Ctx) -> None:
    product = _current(ctx)
    has = bool(product and product.video_url)
    buttons = ([("fb:vid:rm", "🗑️ إزالة الفيديو")] if has else []) + [("fb:back", "↩️ رجوع")]
    await ctx.buttons(
        f"🎬 الفيديو: {'موجود' if has else 'لا يوجد'}\nأرسل فيديو MP4 قصيراً لإضافته أو استبداله.", buttons,
    )


async def h_edit_video(ctx: Ctx, ev: Event) -> None:
    product = _current(ctx)
    if product is None:
        await show_menu(ctx, "⚠️ المنتج لم يعد متاحاً.")
        return
    if ev.kind == "video":
        content = await download_whatsapp_media(ev.media_id)
        if not content or media.detect_video_extension(content) is None:
            await reprompt(ctx, "⚠️ تعذر تحميل الفيديو أو أنه ليس MP4. أعد إرساله.")
            return
        try:
            media.save_video(product.sku, content)
        except (ValueError, OSError):
            logger.exception("Failed saving video of %s", product.sku)
            await reprompt(ctx, "⚠️ تعذر حفظ الفيديو، أعد إرساله.")
            return
        refresh_media_fields(product)
        await apply_changes(ctx, product, {}, "✅ تم حفظ الفيديو.")
        await enter(ctx, "edit_field")
    elif ev.kind == "choice" and ev.choice == "fb:vid:rm":
        media.remove_video(product.sku)
        refresh_media_fields(product)
        await apply_changes(ctx, product, {}, "✅ أُزيل الفيديو (نسخة احتياطية محفوظة).")
        await enter(ctx, "edit_field")
    else:
        await reprompt(ctx)


# ---------------------------------------------------------------- التوجيه

PROMPTS: Dict[str, Callable[[Ctx], Awaitable[None]]] = {
    "add_cat": p_add_cat, "add_sku": p_add_sku, "add_sku_manual": p_add_sku_manual,
    "add_title": p_add_title, "add_price": p_add_price, "add_desc": p_add_desc,
    "add_images": p_add_images, "add_video": p_add_video, "add_avail": p_add_avail,
    "add_confirm": p_add_confirm,
    "edit_pick": p_edit_pick, "edit_field": p_edit_field, "edit_text": p_edit_text,
    "edit_avail": p_edit_avail, "edit_img_menu": p_edit_img_menu, "edit_img_add": p_edit_img_add,
    "edit_img_rep_n": p_edit_img_rep_n, "edit_img_rep_wait": p_edit_img_rep_wait,
    "edit_img_del_n": p_edit_img_del_n, "edit_video": p_edit_video,
}

HANDLERS: Dict[str, Callable[[Ctx, Event], Awaitable[None]]] = {
    "add_cat": h_add_cat, "add_sku": h_add_sku, "add_sku_manual": h_add_sku_manual,
    "add_title": h_add_title, "add_price": h_add_price, "add_desc": h_add_desc,
    "add_images": h_add_images, "add_video": h_add_video, "add_avail": h_add_avail,
    "add_confirm": h_add_confirm,
    "edit_pick": h_edit_pick, "edit_field": h_edit_field, "edit_text": h_edit_text,
    "edit_avail": h_edit_avail, "edit_img_menu": h_edit_img_menu, "edit_img_add": h_edit_img_add,
    "edit_img_rep_n": h_edit_img_rep_n, "edit_img_rep_wait": h_edit_img_rep_wait,
    "edit_img_del_n": h_edit_img_del_n, "edit_video": h_edit_video,
}

# الخطوة السابقة عند «رجوع» (None = القائمة الرئيسية)
PREV: Dict[str, Optional[str]] = {
    "add_cat": None, "add_sku": "add_cat", "add_sku_manual": "add_sku", "add_title": "add_sku",
    "add_price": "add_title", "add_desc": "add_price", "add_images": "add_desc",
    "add_video": "add_images", "add_avail": "add_video", "add_confirm": "add_avail",
    "edit_pick": None, "edit_field": "edit_pick", "edit_text": "edit_field",
    "edit_avail": "edit_field", "edit_img_menu": "edit_field", "edit_img_add": "edit_img_menu",
    "edit_img_rep_n": "edit_img_menu", "edit_img_rep_wait": "edit_img_rep_n",
    "edit_img_del_n": "edit_img_menu", "edit_video": "edit_field",
}


async def go_back(ctx: Ctx) -> None:
    if ctx.session is None:
        await show_menu(ctx)
        return
    state = ctx.session.state
    prev = PREV.get(state)
    if state == "add_sku" and len(factory_categories(ctx)) == 1:
        prev = None                        # لا خطوة اختيار صنف عند صنف واحد
    if prev is None:
        await show_menu(ctx)
    else:
        await enter(ctx, prev)


async def handle(db: Session, factory: models.Factory, phone: str, message: dict) -> bool:
    """تعالج رسالة من مصنع مسجَّل. تُعيد False فقط حين لا شأن للبوت بها (صورة بلا جلسة نشطة،
    وهي مسار الصورة+الشرح القديم)."""
    event = parse_event(message)
    if event is None:
        return False
    lock = _locks.setdefault(factory.id, asyncio.Lock())
    async with lock:
        db.commit()        # معاملة جديدة ترى ما كتبته رسالة سابقة انتظرت دورها
        ctx = Ctx(db, factory, phone, get_session(db, factory))
        return await _dispatch(ctx, event)


async def _dispatch(ctx: Ctx, ev: Event) -> bool:
    if ev.kind == "text":
        command = norm_command(ev.text)
        if command in CANCEL_WORDS:
            await show_menu(ctx, "تم الإلغاء ولم يُحفظ شيء." if ctx.session else None)
            return True
        if command in MENU_WORDS:
            await show_menu(ctx)
            return True
        if command in BACK_WORDS:
            await go_back(ctx)
            return True

    if ev.kind == "choice":
        choice = ev.choice
        if choice == "fb:add":
            await start_add(ctx)
        elif choice == "fb:edit":
            await start_edit(ctx)
        elif choice == "fb:menu":
            await show_menu(ctx)
        elif choice == "fb:cancel":
            await show_menu(ctx, "تم الإلغاء ولم يُحفظ شيء." if ctx.session else None)
        elif choice == "fb:back":
            await go_back(ctx)
        elif choice.startswith("fb:ep:"):
            await open_product(ctx, choice[len("fb:ep:"):])
        elif ctx.session is not None and ctx.session.state in HANDLERS:
            await HANDLERS[ctx.session.state](ctx, ev)
        else:
            await show_menu(ctx)           # زر قديم من محادثة منتهية
        return True

    if ctx.session is None:
        if ev.kind == "image":
            return False                   # مسار الصورة + الشرح القديم
        await show_menu(ctx)
        return True
    handler = HANDLERS.get(ctx.session.state)
    if handler is None:
        await show_menu(ctx)
    else:
        await handler(ctx, ev)
    return True
