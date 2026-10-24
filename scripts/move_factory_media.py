"""نقل وسائط المصانع القديمة من  {media}/{factory_id}/  إلى المجلد المخصص  FACTORY_MEDIA_ROOT (factory/{factory_id}/).

كانت تُحفظ في مجلدات أرقام بجانب products مباشرة. الروابط العامة (/api/media/factories/{id}/{file})
لا تتغير ولا يُلمس سجل القاعدة، فالنقل على القرص فقط.

    python -m scripts.move_factory_media                       # عرض ما سيُنقل (لا يكتب شيئاً)
    python -m scripts.move_factory_media --apply               # التنفيذ
    python -m scripts.move_factory_media --old-root /home/ammar/media --apply

يُنقل فقط ما يشبه ملفات المكتبة: مجلد اسمه رقم، وملفاته  {32 خانة hex}.{امتداد وسائط}. أي شيء آخر يُترك كما هو.
لا يستبدل أي ملف موجود في الوجهة (يتخطاه ويبلّغ)، ويحذف المجلد القديم فقط إن فرغ تماماً.
أوقف خدمة FastAPI أثناء النقل أو نفّذه في وقت هادئ.
"""
import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import List, Tuple

from app.core.config import settings

_FACTORY_DIR = re.compile(r"[0-9]+")
_LIBRARY_FILE = re.compile(r"[0-9a-f]{32}\.(?:jpg|jpeg|png|webp|gif|ico|svg|mp4|mov|webm)")


def plan_moves(old_root: Path, new_root: Path) -> Tuple[List[Tuple[Path, Path]], List[str]]:
    """(قائمة (مصدر، وجهة)، تحذيرات). لا يلمس القرص."""
    moves: List[Tuple[Path, Path]] = []
    notes: List[str] = []
    old_root, new_root = old_root.resolve(), new_root.resolve()
    if old_root == new_root:
        return moves, ["المصدر والوجهة هما المجلد نفسه"]
    for folder in sorted(old_root.iterdir()):
        if not folder.is_dir() or folder.is_symlink() or not _FACTORY_DIR.fullmatch(folder.name):
            continue
        if folder.resolve() == new_root or new_root in folder.resolve().parents:
            continue
        for f in sorted(folder.iterdir()):
            if f.is_file() and not f.is_symlink() and _LIBRARY_FILE.fullmatch(f.name):
                moves.append((f, new_root / folder.name / f.name))
            else:
                notes.append(f"تُرك كما هو: {f}")
    return moves, notes


def apply_moves(moves: List[Tuple[Path, Path]]) -> Tuple[int, List[str]]:
    moved, problems = 0, []
    for src, dst in moves:
        if dst.exists():
            problems.append(f"الوجهة موجودة، تُرك المصدر: {src} -> {dst}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved += 1
    for old_dir in {src.parent for src, _ in moves}:
        try:
            old_dir.rmdir()                       # ينجح فقط إن فرغ تماماً
        except OSError:
            pass
    return moved, problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="نقل وسائط المصانع إلى مجلد factory المخصص")
    parser.add_argument("--old-root", type=Path, default=None,
                        help="المجلد الذي فيه مجلدات الأرقام القديمة (الافتراضي: المجلد الأب لـ MEDIA_ROOT)")
    parser.add_argument("--apply", action="store_true", help="نفّذ النقل فعلاً (بدونه: عرض فقط)")
    args = parser.parse_args(argv)

    old_root = args.old_root or (settings.MEDIA_ROOT.parent if settings.MEDIA_ROOT else None)
    if old_root is None or not old_root.is_dir():
        print("❌ حدّد --old-root (المجلد القديم غير معروف أو غير موجود)")
        return 1
    new_root = settings.FACTORY_MEDIA_ROOT
    print(f"من: {old_root}/{{رقم المصنع}}/   إلى: {new_root}/{{رقم المصنع}}/")

    moves, notes = plan_moves(old_root, new_root)
    for note in notes:
        print("•", note)
    for src, dst in moves:
        print(f"{'نقل' if args.apply else '[عرض]'} {src} -> {dst}")
    if not moves:
        print("لا ملفات تحتاج نقلاً.")
        return 0
    if not args.apply:
        print(f"\nسيُنقل {len(moves)} ملف. أضف --apply للتنفيذ.")
        return 0
    moved, problems = apply_moves(moves)
    for problem in problems:
        print("⚠️", problem)
    print(f"\n✅ نُقل {moved} ملف.")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
