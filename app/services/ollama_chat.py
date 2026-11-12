import asyncio
import logging
import random
import re
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
# سقف طول الوصف (طلب المستخدم 2026-11-12). رقم واتساب الزبون يُلحق بعده سطراً ثابتاً ولا يُحسب منه.
MAX_DESCRIPTION_WORDS = 30
SYSTEM_INSTRUCTION = (
    "أنت كاتب محتوى لـ«{factory_name}». اكتب وصفاً تسويقياً عربياً فقط، صالحاً لمنشور على مواقع "
    "التواصل، اعتماداً حصراً على النشاط المذكور في بيانات النشاط التجاري وسياق المحادثة، أياً كان "
    "هذا النشاط. استخدم العربية الفصحى السليمة من دون خلط لغات أو كلمات أجنبية (إلا اسم علامة "
    "تجارية وارد صراحةً). لا تخترع أسعاراً أو مواصفات أو منتجات أو خدمات أو مزايا غير مذكورة، ولا "
    "تضف موضوعات لا علاقة لها بهذا النشاط. لا يتجاوز النص {max_words} كلمة بأي حال. أعد نص المنشور "
    "فقط دون مقدمة أو شرح أو عناوين.\n\n"
    "الجملة الأولى أهم ما في المنشور، فاجعلها مبتكرة وغير متوقعة ومختلفة في كل مرة: لا تبدأ باسم النشاط "
    "التجاري، ولا بنوع النشاط (مثل «مخبزة» أو «متجر» أو «مصنع» أو «سوبرماركت»)، ولا بـ«نحن» أو «في». "
    "اذكر الاسم في الجملة الثانية أو بعدها."
).replace("{max_words}", str(MAX_DESCRIPTION_WORDS))

# وسوم يقترحها النموذج: تُطلب في نص المنشور للزبون حين لا وسوم في إعدادات صفحته (customer_captions)،
# وتُفصل عن النص عند تركيب المنشور. وصف الإدارة لا يطلبها (وسومه من factory_reel_posts.suggested_hashtags).
HASHTAG_INSTRUCTION = (
    "بعد نص المنشور اكتب سطراً أخيراً منفصلاً فيه من 3 إلى 5 وسوم (هاشتاقات) عربية مناسبة لهذا النشاط "
    "تحديداً، كل وسم يبدأ بـ # وكلماته موصولة بـ _ (مثل #ملابس_أطفال)، ولا شيء غيرها في ذلك السطر. "
    "لا تضع وسوماً داخل نص المنشور نفسه. الوسوم لا تُحسب من حد الكلمات."
)

# مصطلحات الحجر: تُضاف لمصانع المورّدين فقط (كلها حجر)، فتمنع ترجمات خاطئة (سجادة، أثاث، سقف...)
# وتعرّف النموذج أين يُستعمل الحجر. مصانع الزبائن (kind=customer) أنشطتها متنوعة فلا تُضاف لها.
STONE_TERMINOLOGY = (
    "المصطلحات الصحيحة (مهمة):\n"
    "- الحجر هنا مادة بناء وتكسية ديكورية، وليس أثاثاً ولا قماشاً ولا اشجارا ولا ورق جدران.\n"
    "- أماكن استعماله المعتادة: تكسية الجدران الداخلية والخارجية، واجهات المباني، "
    "المداخل، الأعمدة والأقواس، المدافئ، الأسوار، الأرضيات والممرات، الحدائق والمسابح.\n"
    "- المصطلحات المناسبة: تكسية، كسوة، واجهة، جدار، رصف، حجر طبيعي، حجر ديكور، "
    "واجهة حجرية، ملمس خشن أو ناعم، أحجار مقطّعة.\n"
    "- ممنوع استعمال: سجادة، بساط، أثاث، مفروشات، شجر ، سقف، ستائر، ورق جدران، "
    "ولا تصف الحجر بأنه ناعم كالقماش أو دافئ كالسجاد.\n"
    "- لا تذكر استعمالاً أو موضعاً لم يرد في نشاط المصنع؛ وإن لم يُحدَّد فاكتفِ بالجدران والواجهات."
)

# تنويع الأوصاف: يُختار عنصر عشوائي من كل قائمة عند كل طلب حتى لا يتكرر الناتج.
VARIATION_ANGLES = (
    "جودة المنتج أو الخدمة وما يميّزها",
    "المتانة أو الموثوقية",
    "الإحساس أو الفائدة التي يحصل عليها الزبون",
    "فكرة استعمال المنتج أو الخدمة في موقف حقيقي",
    "الصنع أو العمل المحلي والفخر بالمنتج الوطني",
    "دعوة مباشرة للتواصل وطلب المنتج",
)
# أساليب الافتتاح تُستعمل بالتناوب لا بالقرعة (turn في _variation_block)، فلا يتكرر أسلوب في وصفين متتاليين.
VARIATION_OPENINGS = (
    "سؤال قصير يلامس حاجة يعرفها الزبون",
    "مشهد قصير من الحياة اليومية يظهر فيه المنتج أو الخدمة",
    "فعل أمر يدعو القارئ إلى تجربة شيء",
    "تشبيه أو صورة بلاغية قصيرة",
    "عبارة تشويقية تثير الفضول ولا تكشف كل شيء",
    "مخاطبة فئة محددة من الزبائن يناسبها هذا النشاط",
    "كلمة أو كلمتان قويتان تليهما نقطة",
    "تعجّب قصير",
    "مقارنة سريعة بين قبل وبعد",
    "وعد واضح للزبون",
    "تفصيلة حسية (لون، ملمس، رائحة، صوت) مما يناسب النشاط",
    "لحظة أو وقت مناسب لاستعمال المنتج أو الخدمة",
)
VARIATION_TONES = ("راقية وهادئة", "حماسية", "دافئة وقريبة من الزبون", "عملية ومباشرة")
VARIATION_LENGTHS = ("جملة واحدة", "جملتان قصيرتان", "ثلاث جمل قصيرة جداً")
VARIATION_EMOJI = ("بدون إيموجي", "إيموجي واحد فقط", "إيموجيان كحد أقصى")
AVOID_PREVIOUS_LIMIT = 5
AVOID_PREVIOUS_CHARS = 220
OPENING_KEY_WORDS = 2          # بداية الوصف = أول كلمتين: لا تتكرر بين وصف وما قبله (opening_key)
REPEAT_CHECK_LIMIT = 10        # كم وصفاً سابقاً يُقارن به الجديد قبل حفظه

# إعدادات توليد أعلى تنوعاً (لا seed ثابت عمداً).
GENERATION_OPTIONS = {"temperature": 0.9, "top_p": 0.95, "repeat_penalty": 1.15}


def make_client(
    transport: Optional[httpx.AsyncBaseTransport] = None, timeout: Optional[int] = None,
) -> httpx.AsyncClient:
    # مهلة القراءة طويلة: Ollama على المعالج يبطؤ كثيراً أثناء رندر الفيديو في العامل الآخر
    request_timeout = httpx.Timeout(timeout or settings.OLLAMA_TIMEOUT, connect=15)
    return httpx.AsyncClient(timeout=request_timeout, transport=transport)


class OllamaUnavailableError(Exception):
    """Selected model provider did not return a usable response."""

    def __init__(self, message: str = "", *, retryable: bool = True, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


_SENTENCE_END = re.compile(r"[.!؟?؛\n]")
_NOT_WORD = re.compile(r"[^\w\s]")


def first_sentence(text: str) -> str:
    """الجملة الأولى من الوصف (حتى أول . ! ؟ ؛ أو سطر جديد)."""
    return _SENTENCE_END.split((text or "").strip(), maxsplit=1)[0].strip()


def opening_key(text: str) -> str:
    """أول كلمتين من الوصف بلا ترقيم ولا إيموجي ولا «»: ما يُقارن به تكرار البداية."""
    words = _NOT_WORD.sub(" ", (text or "").strip()).split()
    return " ".join(words[:OPENING_KEY_WORDS])


def _variation_block(
    history: list[models.FactoryChatMessage], rng: random.Random, turn: Optional[int] = None,
) -> str:
    """turn: ترتيب هذا الوصف بين أوصاف المصنع، يختار أسلوب الافتتاح بالتناوب (بلا turn: بالقرعة)."""
    opening = VARIATION_OPENINGS[turn % len(VARIATION_OPENINGS)] if turn is not None else rng.choice(VARIATION_OPENINGS)
    lines = [
        "التنويع (إلزامي):",
        f"- زاوية الوصف: {rng.choice(VARIATION_ANGLES)}",
        f"- أسلوب الجملة الأولى: {opening}",
        f"- النبرة: {rng.choice(VARIATION_TONES)}",
        f"- الطول: {rng.choice(VARIATION_LENGTHS)}، وفي كل الأحوال {MAX_DESCRIPTION_WORDS} كلمة على الأكثر",
        f"- {rng.choice(VARIATION_EMOJI)}",
    ]
    recent = [m.response for m in history[-AVOID_PREVIOUS_LIMIT:] if m.response and m.response.strip()]
    if recent:
        openings = list(dict.fromkeys(first_sentence(text) for text in recent if first_sentence(text)))
        keys = list(dict.fromkeys(opening_key(text) for text in recent if opening_key(text)))
        lines.append("- الجمل الأولى في الأوصاف السابقة؛ ممنوع تكرار أي منها أو صياغة قريبة منها:")
        lines.extend(f"  - {text}" for text in openings)
        lines.append("- ممنوع أن يبدأ الوصف بأي من: " + "، ".join(f"«{key}»" for key in keys))
        lines.append("- ولا تعد استعمال جمل الأوصاف السابقة أدناه أو تراكيبها:")
        lines.extend(f"  * {' '.join(text.split())[:AVOID_PREVIOUS_CHARS]}" for text in recent)
    return "\n".join(lines)


def _conversation_messages(
    factory: models.Factory,
    history: list[models.FactoryChatMessage],
    prompt: str,
    rng: Optional[random.Random] = None,
    *,
    hashtags: bool = False,
    turn: Optional[int] = None,
) -> list[dict[str, str]]:
    """hashtags: يُطلب من النموذج سطر وسوم أخير (HASHTAG_INSTRUCTION). turn: انظر _variation_block."""
    # الأوصاف السابقة تُرسل كقائمة «تجنّب» داخل رسالة النظام وليست أدواراً حوارية،
    # لأن تكرار نفس السؤال مع أجوبته السابقة يدفع النموذج لنسخ الجواب نفسه.
    rng = rng or random.Random()
    instruction = SYSTEM_INSTRUCTION.replace("{factory_name}", factory.name)
    if (factory.kind or models.FactoryKind.SUPPLIER.value) == models.FactoryKind.SUPPLIER.value:
        instruction = f"{instruction}\n\n{STONE_TERMINOLOGY}"
    if hashtags:
        instruction = f"{instruction}\n\n{HASHTAG_INSTRUCTION}"
    profile = (
        f"{instruction}\n\nبيانات النشاط التجاري:\nالاسم: {factory.name}\n"
        f"النشاط: {factory.activity or 'غير محدد'}\n\n{_variation_block(history, rng, turn)}"
    )
    return [
        {"role": "system", "content": profile},
        {"role": "user", "content": prompt},
    ]


def _selected_provider(model: str) -> str:
    model = model.removeprefix("models/").strip()
    if not model:
        raise OllamaUnavailableError("يجب تحديد اسم النموذج في الطلب")
    return "gemini" if model.lower().startswith("gemini-") else "ollama"


async def _request_ollama(messages: list[dict[str, str]], model: str) -> str:
    try:
        async with make_client() as client:
            response = await client.post(
                f"{settings.OLLAMA_BASE_URL}/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": GENERATION_OPTIONS,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        logger.warning("Ollama request failed: %s %s", type(exc).__name__, exc)
        raise OllamaUnavailableError(type(exc).__name__) from exc
    except ValueError as exc:
        logger.warning("Ollama returned invalid JSON")
        raise OllamaUnavailableError("InvalidJSON") from exc

    if (
        not isinstance(payload, dict)
        or not isinstance(payload.get("message"), dict)
        or not isinstance(payload["message"].get("content"), str)
        or not payload["message"]["content"].strip()
    ):
        logger.warning("Ollama response is missing its generated message")
        raise OllamaUnavailableError("EmptyResponse")
    return payload["message"]["content"]


async def _request_gemini(messages: list[dict[str, str]], model: str) -> str:
    if not settings.GEMINI_API_KEY:
        raise OllamaUnavailableError("GEMINI_API_KEY غير مضبوط", retryable=False)
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user",
         "parts": [{"text": m["content"]}]}
        for m in messages if m["role"] != "system"
    ]
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model.removeprefix('models/')}:generateContent"
    )
    try:
        async with make_client(timeout=settings.GEMINI_TIMEOUT) as client:
            response = await client.post(
                url,
                headers={"x-goog-api-key": settings.GEMINI_API_KEY},
                json={
                    "systemInstruction": {"parts": [{"text": system}]},
                    "contents": contents,
                    "generationConfig": {
                        "temperature": 1.0,     # تنويع أعلى؛ القيمة الافتراضية الموصى بها لـ Gemini
                        "topP": 0.9,
                        "maxOutputTokens": 300,
                    },
                },
            )
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        try:
            error_body = exc.response.json().get("error", {})
            error_status = error_body.get("status")
            error_message = error_body.get("message")
            reason = ": ".join(str(item) for item in (error_status, error_message) if item)
            reason = reason or "رفضت الخدمة الطلب"
            reason = str(reason).replace(settings.GEMINI_API_KEY, "[مخفي]")[:500]
        except (ValueError, AttributeError, TypeError):
            reason = (exc.response.text or "رفضت الخدمة الطلب")
            if settings.GEMINI_API_KEY:
                reason = reason.replace(settings.GEMINI_API_KEY, "[مخفي]")
            reason = reason[:300]
        detail = f"Gemini أعاد HTTP {exc.response.status_code}: {reason}"
        logger.warning("Gemini request failed with HTTP %s", exc.response.status_code)
        retry_after = exc.response.headers.get("Retry-After")
        try:
            retry_after_seconds = min(max(float(retry_after), 0.0), 120.0) if retry_after else None
        except ValueError:
            retry_after_seconds = None
        retryable = exc.response.status_code in {408, 429, 500, 502, 503, 504}
        raise OllamaUnavailableError(
            detail, retryable=retryable, retry_after=retry_after_seconds,
        ) from exc
    except httpx.TimeoutException as exc:
        detail = f"انتهت مهلة الاتصال بـ Gemini بعد {settings.GEMINI_TIMEOUT} ثانية"
        logger.warning("Gemini request timed out")
        raise OllamaUnavailableError(detail) from exc
    except httpx.ConnectError as exc:
        logger.warning("Could not connect to Gemini: %s", type(exc).__name__)
        raise OllamaUnavailableError("تعذر الاتصال بخدمة Gemini؛ تحقق من اتصال الخادم بالإنترنت") from exc
    except httpx.HTTPError as exc:
        logger.warning("Gemini request failed: %s", type(exc).__name__)
        raise OllamaUnavailableError(f"فشل طلب Gemini بسبب خطأ شبكة ({type(exc).__name__})") from exc
    except ValueError as exc:
        logger.warning("Gemini returned invalid JSON")
        raise OllamaUnavailableError("InvalidJSON") from exc
    try:
        text = "".join(
            part["text"] for part in payload["candidates"][0]["content"]["parts"]
            if isinstance(part.get("text"), str)
        ).strip()
    except (KeyError, IndexError, TypeError):
        text = ""
    if not text:
        logger.warning("Gemini response is missing generated text")
        raise OllamaUnavailableError("EmptyResponse")
    return text


async def generate_reply(
    messages: list[dict[str, str]], model: str, provider: Optional[str] = None,
) -> str:
    if not model or not model.strip():
        raise OllamaUnavailableError("يجب تحديد اسم النموذج في الطلب")
    model = model.strip().removeprefix("models/")
    selected_provider = (provider or _selected_provider(model)).lower()
    if selected_provider not in {"ollama", "gemini"}:
        raise OllamaUnavailableError(f"المزوّد المستنتج من النموذج غير مدعوم: {selected_provider}")
    if selected_provider == "gemini" and not model.lower().startswith("gemini-"):
        raise OllamaUnavailableError("اسم نموذج Gemini يجب أن يبدأ بـ gemini-")
    last: Optional[OllamaUnavailableError] = None
    attempts = max(1, settings.AI_MODEL_ATTEMPTS)
    for attempt in range(1, attempts + 1):
        try:
            if selected_provider == "gemini":
                return await _request_gemini(messages, model)
            return await _request_ollama(messages, model)
        except OllamaUnavailableError as exc:
            last = exc
            if not exc.retryable or attempt >= attempts:
                break
            wait_seconds = exc.retry_after
            if wait_seconds is None:
                wait_seconds = min(3 * (2 ** (attempt - 1)), 30)
            logger.info(
                "Retrying %s model after %s seconds (attempt %d/%d failed)",
                selected_provider, wait_seconds, attempt, attempts,
            )
            await asyncio.sleep(wait_seconds)
    raise last


def enqueue_description_job(
    db: Session, factory_id: int, model: str, *, hashtags: bool = False,
) -> models.FactoryDescriptionJob:
    """hashtags: يطلب من النموذج وسوماً مناسبة في سطر أخير (لمنشور الزبون بلا وسوم في إعداداته)."""
    factory = factory_service.get_factory(db, factory_id)
    prompt = (factory.activity or "").strip()
    if not prompt:
        raise InvalidInputError("نشاط المصنع مطلوب لتوليد الوصف")
    model = (model or "").strip().removeprefix("models/")
    if not model:
        raise InvalidInputError("يجب تحديد اسم النموذج في الطلب")
    provider = _selected_provider(model)

    history = list(reversed(
        db.query(models.FactoryChatMessage)
        .filter(models.FactoryChatMessage.factory_id == factory_id)
        .order_by(models.FactoryChatMessage.id.desc())
        .limit(MAX_CONTEXT_TURNS)
        .all()
    ))
    turn = db.query(models.FactoryChatMessage).filter(models.FactoryChatMessage.factory_id == factory_id).count()
    messages = _conversation_messages(factory, history, prompt, hashtags=hashtags, turn=turn)
    job = models.FactoryDescriptionJob(
        factory_id=factory_id,
        prompt=prompt,
        messages=messages,
        model=model,
        provider=provider,
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
        provider = job.provider or "ollama"

    try:
        description = asyncio.run(generate_reply(messages, model, provider=provider)).strip()
        if not description:
            raise OllamaUnavailableError("Ollama returned an empty description")
        description = _rewrite_if_opening_repeats(job_id, messages, model, provider, description, session_factory)
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
    except OllamaUnavailableError as exc:
        detail = f" ({exc})" if str(exc) else ""
        provider_label = "Ollama" if provider == "ollama" else "Gemini"
        _fail_description_job(job_id, f"تعذر الحصول على وصف صالح من نموذج {provider_label}{detail}", session_factory)
        logger.warning("Factory description job %s failed: %s returned no usable response", job_id, provider)
        return False
    except Exception:
        logger.exception("Factory description job %s failed unexpectedly", job_id)
        _fail_description_job(job_id, "حدث خطأ أثناء توليد الوصف؛ راجع سجل العامل", session_factory)
        return False


def _previous_opening_keys(job_id: int, session_factory: SessionFactory) -> set:
    with session_factory() as db:
        job = db.get(models.FactoryDescriptionJob, job_id)
        if job is None:
            return set()
        rows = (
            db.query(models.FactoryChatMessage.response)
            .filter(models.FactoryChatMessage.factory_id == job.factory_id)
            .order_by(models.FactoryChatMessage.id.desc())
            .limit(REPEAT_CHECK_LIMIT)
            .all()
        )
    return {opening_key(response) for (response,) in rows if response and opening_key(response)}


def _rewrite_if_opening_repeats(
    job_id: int, messages: list, model: str, provider: str, description: str, session_factory: SessionFactory,
) -> str:
    """إن بدأ الوصف بكلمتي وصف سابق للمصنع يُطلب من النموذج إعادة كتابته مرة واحدة بافتتاحية أخرى.
    فشل إعادة الكتابة أو تكرارها لا يُفشل المهمة: يبقى الأفضل المتاح."""
    previous = _previous_opening_keys(job_id, session_factory)
    if opening_key(description) not in previous:
        return description
    logger.info("Description job %s repeated an earlier opening; asking for a rewrite", job_id)
    retry = list(messages) + [
        {"role": "assistant", "content": description},
        {"role": "user", "content": (
            f"هذا الوصف يبدأ بـ«{opening_key(description)}» كما بدأ وصف سابق. أعد كتابته كاملاً بجملة أولى "
            "مختلفة تماماً ومبتكرة، بالقواعد نفسها، ولا تبدأ بأي من: "
            + "، ".join(f"«{key}»" for key in sorted(previous))
        )},
    ]
    try:
        rewritten = asyncio.run(generate_reply(retry, model, provider=provider)).strip()
    except OllamaUnavailableError:
        return description
    return rewritten or description


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
