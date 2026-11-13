import factory.django
from factory.declarations import Sequence
from factory.faker import Faker

from apps.accounts.models import User

TEST_PASSWORD = "test-password-123"


class UserFactory(factory.django.DjangoModelFactory[User]):
    """Use `UserFactory.create(...)`: it is typed to return a `User`."""

    class Meta:
        model = User
        skip_postgeneration_save = True

    email = Sequence(lambda n: f"user{n}@example.nl")
    name = Faker("name", locale="nl_NL")
    password = factory.django.Password(TEST_PASSWORD)
