"""يولّد زوج مفاتيح VAPID لإشعارات المتصفح ويطبعهما بصيغة .env. يُشغَّل مرة واحدة لكل بيئة:

    venv/bin/python -m scripts.generate_vapid_keys

انسخ السطرين إلى .env ثم أعد تشغيل الخادم والعاملين. لا تولّد مفتاحين جديدين لبيئة قائمة: ذلك يُبطل اشتراكات
كل الأجهزة، فيعيد كل زبون تفعيل الإشعارات من لوحته."""
import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def generate() -> tuple[str, str]:
    """(المفتاح العام كما يمرَّر للمتصفح applicationServerKey، والخاص خاماً 32 بايتاً)."""
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    private = key.private_numbers().private_value.to_bytes(32, "big")
    return _b64url(public), _b64url(private)


if __name__ == "__main__":
    public_key, private_key = generate()
    print(f"VAPID_PUBLIC_KEY={public_key}")
    print(f"VAPID_PRIVATE_KEY={private_key}")
