"""حراس الفصل بين الطبقات. تفشل عند أول استيراد يكسر الاتجاه المعتمد:

    routers, channels  ->  services  ->  models/core        (لا يصعد أي سهم للأعلى)

لذلك تبقى services/ بلا أي معرفة بواتساب أو تيليجرام أو HTTP، وتعمل مع أي قناة جديدة كما هي.
"""
import ast
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"


def imported_modules(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            yield base
            for alias in node.names:
                yield f"{base}.{alias.name}"


def violations(folder: str, forbidden: tuple):
    found = []
    for path in sorted((APP / folder).rglob("*.py")):
        for module in imported_modules(path):
            if any(module == f or module.startswith(f + ".") for f in forbidden):
                found.append(f"{path.relative_to(APP)} يستورد {module}")
    return found


class LayeringTests(unittest.TestCase):
    def test_services_know_no_channel_or_http(self):
        self.assertEqual(violations("services", (
            "app.channels", "app.routers", "app.jobs", "fastapi", "starlette",
        )), [])

    def test_routers_go_through_services(self):
        # posts.py استثناء معلن: CRUD بسيط لم يُنقل إلى خدمة بعد
        found = [v for v in violations("routers", ("app.models",)) if not v.startswith("routers/posts.py")]
        self.assertEqual(found, [])

    def test_conversation_core_is_channel_neutral(self):
        core = [APP / "channels" / "factory_flow.py", APP / "channels" / "texts.py", APP / "channels" / "base.py"]
        for path in core:
            for module in imported_modules(path):
                with self.subTest(file=path.name, module=module):
                    self.assertFalse(module.startswith("app.channels.whatsapp"), module)
                    self.assertNotIn(module, ("httpx", "fastapi"))


if __name__ == "__main__":
    unittest.main()
