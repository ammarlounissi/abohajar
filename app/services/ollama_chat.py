import asyncio
import logging
import random
from datetime import datetime
from typing import Callable, Optional

import httpx
from sqlalchemy.orm import Session

from app import models
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.time import utc_now
from app.services import factories as factory_service
from app.services.errors import InvalidInputError, NotFoundError

logger = logging.getLogger(__name__)
MAX_CONTEXT_TURNS = 5
SessionFactory = Callable[[], Session]
SYSTEM_INSTRUCTION = (
    "أنت كاتب محتوى للمصنع «{factory_name}». اكتب وصفاً تسويقياً عربياً موجزاً يصلح لمنشور "
    "على مواقع التواصل اعتماداً على نشاط المصنع وسياق المحادثة فقط. لا تخترع أسعاراً أو "
    "مواصفات أو منتجات غير مذكورة، وأعد نص الوصف فقط دون مقدمة أو شرح."
)

# مصطلحات المجال: تمنع النموذج من ترجمات خاطئة (سجادة، أثاث، سقف...) وتعرّفه أين يُستعمل الحجر.
STONE_TERMINOLOGY = (
    "المصطلحات الصحيحة (مهمة):\n"
    "- الحجر هنا مادة بناء وتكسية ديكورية، وليس أثاثاً ولا قماشاً ولا ورق جدران.\n"
    "- أماكن استعماله المعتادة: تكسية الجدران الداخلية والخارجية، واجهات المباني، "
    "المداخل، الأعمدة والأقواس، المدافئ، الأسوار، الأرضيات والممرات، الحدائق والمسابح.\n"
    "- المصطلحات المناسبة: تكسية، كسوة، واجهة، جدار، رصف، حجر طبيعي، حجر ديكور، "
    "واجهة حجرية، ملمس خشن أو ناعم، أحجار مقطّعة.\n"
    "- ممنوع استعمال: سجادة، بساط، أثاث، مفروشات، سقف، ستائر، ورق جدران، "
    "ولا تصف الحجر بأنه ناعم كالقماش أو دافئ كالسجاد.\n"
    "- لا تذكر استعمالاً أو موضعاً لم يرد في نشاط المصنع؛ وإن لم يُحدَّد فاكتفِ بالجدران والواجهات."
)

# تنويع الأوصاف: يُختار عنصر عشوائي من كل قائمة عند كل طلب حتى لا يتكرر الناتج.
VARIATION_ANGLES = (
    "جمال الشكل والملمس الطبيعي",
    "المتانة وطول العمر",
    "الإحساس الذي يضيفه للمكان (دفء، أصالة، فخامة)",
    "فكرة استعماله في واجهة أو جدار",
    "الصنع المحلي والفخر بالمنتج الوطني",
    "دعوة مباشرة للتواصل وطلب المنتج",
)
VARIATION_OPENINGS = (
    "سؤال موجّه للقارئ",
    "جملة قصيرة قوية",
    "وصف مشهد",
    "عبارة تشويقية",
    "ذكر النشاط مباشرة",
)
VARIATION_TONES = ("راقية وهادئة", "حماسية", "دافئة وقريبة من الزبون", "عملية ومباشرة")
VARIATION_LENGTHS = ("جملتان", "ثلاث جمل", "أربع جمل قصيرة")
VARIATION_EMOJI = ("بدون إيموجي", "إيموجي واحد فقط", "إيموجيان كحد أقصى")
AVOID_PREVIOUS_LIMIT = 5
AVOID_PREVIOUS_CHARS = 220

# إعدادات توليد أعلى تنوعاً (لا seed ثابت عمداً).
GENERATION_OPTIONS = {"temperature": 0.9, "top_p": 0.95, "repeat_penalty": 1.15}


def make_client(transport: Optional[httpx.AsyncBaseTransport] = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=120, transport=transport)


class OllamaUnavailableError(Exception):
    """Ollama did not return a usable response."""


def _variation_block(
    history: list[models.FactoryChatMessage], rng: random.Random,
) -> str:
    lines = [
        "التنويع (إلزامي):",
        f"- زاوية الوصف: {rng.choice(VARIATION_ANGLES)}",
        f"- أسلوب الافتتاح: {rng.choice(VARIATION_OPENINGS)}",
        f"- النبرة: {rng.choice(VARIATION_TONES)}",
        f"- الطول: {rng.choice(VARIATION_LENGTHS)}",
        f"- {rng.choice(VARIATION_EMOJI)}",
    ]
    previous = [
        " ".join(m.response.split())[:AVOID_PREVIOUS_CHARS]
        for m in history[-AVOID_PREVIOUS_LIMIT:]
        if m.response and m.response.strip()
    ]
    if previous:
        lines.append(
            "- لا تبدأ بكلمة أو عبارة وردت في الأوصاف السابقة أدناه، ولا تعد استعمال "
            "جملها أو تراكيبها:"
        )
        lines.extend(f"  * {text}" for text in previous)
    return "\n".join(lines)


def _conversation_messages(
    factory: models.Factory,
    history: list[models.FactoryChatMessage],
    prompt: str,
    rng: Optional[random.Random] = None,
) -> list[dict[str, str]]:
    # الأوصاف السابقة تُرسل كقائمة «تجنّب» داخل رسالة النظام وليست أدواراً حوارية،
    # لأن تكرار نفس السؤال مع أجوبته السابقة يدفع النموذج لنسخ الجواب نفسه.
    rng = rng or random.Random()
    profile = (
        f"{SYSTEM_INSTRUCTION.replace('{factory_name}', factory.name)}\n\n{STONE_TERMINOLOGY}"
        f"\n\nبيانات المصنع:\nالاسم: {factory.name}\n"
        f"النشاط: {factory.activity or 'غير محدد'}\n\n{_variation_block(history, rng)}"
    )
    return [
        {"role": "system", "content": profile},
        {"role": "user", "content": prompt},
    ]


async def generate_reply(
    messages: list[dict[str, str]], model: Optional[str] = None,
) -> str:
    try:
        async with make_client() as client:
            response = await client.post(
                f"{settings.OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": model or settings.OLLAMA_MODEL,
                    "messages": messages,
                    "stream": False,
                    "options": GENERATION_OPTIONS,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Ollama request failed: %s", type(exc).__name__)
        raise OllamaUnavailableError from exc
    except ValueError as exc:
        logger.warning("Ollama returned invalid JSON")
        raise OllamaUnavailableError from exc

    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("message"), dict)
        or not isinstance(payload["message"].get("content"), str)
        or not payload["message"]["content"].strip()
    ):
        logger.warning("Ollama response is missing its generated message")
        raise OllamaUnavailableError
    return payload["message"]["content"]


def enqueue_description_job(db: Session, factory_id: int) -> models.FactoryDescriptionJob:
    factory = factory_service.get_factory(db, factory_id)
    prompt = (factory.activity or "").strip()
    if not prompt:
        raise InvalidInputError("نشاط المصنع مطلوب لتوليد الوصف")

    history = list(reversed(
        db.query(models.FactoryChatMessage)
        .filter(models.FactoryChatMessage.factory_id == factory_id)
        .order_by(models.FactoryChatMessage.id.desc())
        .limit(MAX_CONTEXT_TURNS)
        .all()
    ))
    messages = _conversation_messages(factory, history, prompt)
    job = models.FactoryDescriptionJob(
        factory_id=factory_id,
        prompt=prompt,
        messages=messages,
        model=settings.OLLAMA_MODEL,
        status="queued",
    )
    try:
        db.add(job)
        db.commit()
        db.refresh(job)
    except Exception:
        db.rollback()
        raise
    return job


def get_description_job(
    db: Session, factory_id: int, job_id: int,
) -> models.FactoryDescriptionJob:
    job = db.query(models.FactoryDescriptionJob).filter(
        models.FactoryDescriptionJob.id == job_id,
        models.FactoryDescriptionJob.factory_id == factory_id,
    ).first()
    if job is None:
        raise NotFoundError("مهمة توليد الوصف غير موجودة لهذا المصنع")
    return job


def claim_next_description_job(session_factory: SessionFactory = SessionLocal) -> Optional[int]:
    with session_factory() as db:
        job_id = db.query(models.FactoryDescriptionJob.id).filter(
            models.FactoryDescriptionJob.status == "queued",
        ).order_by(models.FactoryDescriptionJob.id).limit(1).scalar()
        if job_id is None:
            return None
        now = utc_now()
        updated = db.query(models.FactoryDescriptionJob).filter(
            models.FactoryDescriptionJob.id == job_id,
            models.FactoryDescriptionJob.status == "queued",
        ).update(
            {
                models.FactoryDescriptionJob.status: "running",
                models.FactoryDescriptionJob.started_at: now,
                models.FactoryDescriptionJob.updated_at: now,
            },
            synchronize_session=False,
        )
        db.commit()
        return job_id if updated == 1 else None


def process_description_job(
    job_id: int, session_factory: SessionFactory = SessionLocal,
) -> bool:
    with session_factory() as db:
        job = db.get(models.FactoryDescriptionJob, job_id)
        if job is None or job.status != "running":
            return False
        messages = job.messages
        model = job.model

    try:
        description = asyncio.run(generate_reply(messages, model)).strip()
        if not description:
            raise OllamaUnavailableError("Ollama returned an empty description")
        now = utc_now()
        with session_factory() as db:
            job = db.get(models.FactoryDescriptionJob, job_id)
            if job is None or job.status != "running":
                return False
            job.response = description
            job.status = "succeeded"
            job.updated_at = now
            job.completed_at = now
            db.add(models.FactoryChatMessage(
                factory_id=job.factory_id,
                prompt=job.prompt,
                response=description,
                created_at=now,
            ))
            db.commit()
        logger.info("Factory description job %s completed", job_id)
        return True
    except OllamaUnavailableError:
        _fail_description_job(job_id, "تعذر الحصول على وصف صالح من نموذج Ollama", session_factory)
        logger.warning("Factory description job %s failed: Ollama returned no usable response", job_id)
        return False
    except Exception:
        logger.exception("Factory description job %s failed unexpectedly", job_id)
        _fail_description_job(job_id, "حدث خطأ أثناء توليد الوصف؛ راجع سجل العامل", session_factory)
        return False


def _fail_description_job(
    job_id: int, message: str, session_factory: SessionFactory,
) -> None:
    with session_factory() as db:
        job = db.get(models.FactoryDescriptionJob, job_id)
        if job is None:
            logger.error("Cannot record failure: description job %s disappeared", job_id)
            return
        job.status = "failed"
        job.error = message
        job.updated_at = utc_now()
        job.completed_at = utc_now()
        db.commit()


INTERRUPTED_DESCRIPTION_MESSAGE = "توقف عامل توليد الأوصاف أثناء تنفيذ المهمة؛ أعد الطلب"


def recover_interrupted_description_jobs(
    session_factory: SessionFactory = SessionLocal,
) -> int:
    now: datetime = utc_now()
    with session_factory() as db:
        count = db.query(models.FactoryDescriptionJob).filter(
            models.FactoryDescriptionJob.status == "running",
        ).update(
            {
                models.FactoryDescriptionJob.status: "failed",
                models.FactoryDescriptionJob.error: INTERRUPTED_DESCRIPTION_MESSAGE,
                models.FactoryDescriptionJob.updated_at: now,
                models.FactoryDescriptionJob.completed_at: now,
            },
            synchronize_session=False,
        )
        db.commit()
        return count
