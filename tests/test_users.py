"""جدول users: الدخول بالبريد وكلمة السر أو بفيسبوك."""
import unittest

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.core.database import Base
from app.services import users
from app.services.errors import DuplicateError, InvalidInputError


class UsersTests(unittest.TestCase):
    def setUp(self):
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
        self.db = sessionmaker(bind=engine)()

    def tearDown(self):
        self.db.close()

    def test_password_is_hashed_and_verified(self):
        user = users.create_user_with_password(self.db, " Ali@Example.com ", "secret-pass")
        self.assertEqual(user.email, "ali@example.com")
        self.assertNotIn("secret-pass", user.password_hash)
        self.assertEqual(users.login_with_password(self.db, "ALI@example.com", "secret-pass").id, user.id)
        self.assertIsNotNone(user.last_login_at)

    def test_wrong_password_unknown_email_and_inactive_share_one_error(self):
        user = users.create_user_with_password(self.db, "a@b.co", "secret-pass")
        messages = set()
        for email, pw in (("a@b.co", "wrong-pass1"), ("nobody@b.co", "secret-pass"), ("bad", "x")):
            with self.assertRaises(InvalidInputError) as ctx:
                users.login_with_password(self.db, email, pw)
            messages.add(str(ctx.exception))
        user.is_active = False
        self.db.commit()
        with self.assertRaises(InvalidInputError) as ctx:
            users.login_with_password(self.db, "a@b.co", "secret-pass")
        messages.add(str(ctx.exception))
        self.assertEqual(len(messages), 1)

    def test_short_password_and_duplicate_email_rejected(self):
        with self.assertRaises(InvalidInputError):
            users.create_user_with_password(self.db, "a@b.co", "short")
        users.create_user_with_password(self.db, "a@b.co", "secret-pass")
        with self.assertRaises(DuplicateError):
            users.create_user_with_password(self.db, "A@B.co", "another-pass")

    def test_facebook_login_creates_then_reuses(self):
        first = users.login_with_facebook(self.db, "555", "Omar", "omar@x.com")
        again = users.login_with_facebook(self.db, "555", "Omar")
        self.assertEqual(first.id, again.id)
        self.assertEqual(first.email, "omar@x.com")
        self.assertIsNone(first.password_hash)
        with self.assertRaises(InvalidInputError):  # حساب فيسبوك فقط لا يدخل بكلمة سر
            users.login_with_password(self.db, "omar@x.com", "anything-here")

    def test_facebook_does_not_take_over_existing_email(self):
        owner = users.create_user_with_password(self.db, "a@b.co", "secret-pass")
        fb = users.login_with_facebook(self.db, "777", "Stranger", "a@b.co")
        self.assertNotEqual(fb.id, owner.id)
        self.assertIsNone(fb.email)

    def test_db_requires_a_login_method(self):
        self.db.add(models.User(name="no method"))
        with self.assertRaises(IntegrityError):
            self.db.commit()


if __name__ == "__main__":
    unittest.main()
