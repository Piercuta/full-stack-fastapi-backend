from sqlmodel import Session, create_engine, select

from app import crud
from app.core.config import settings
from app.models import User, UserCreate
# create_engine
# engine = (str(settings.SQLALCHEMY_DATABASE_URI))


# Crée l'engine avec une configuration de pool explicite
engine = create_engine(
    str(settings.SQLALCHEMY_DATABASE_URI),
    pool_size=20,           # nombre max de connexions persistantes
    max_overflow=30,        # connexions supplémentaires temporaires si pool saturé
    pool_timeout=30,        # temps max pour attendre une connexion avant timeout (en sec)
    pool_recycle=1800,      # recycle les connexions après X secondes pour éviter timeout réseau
    echo=False              # passe à True si tu veux loguer toutes les requêtes SQL
)

# make sure all SQLModel models are imported (app.models) before initializing DB
# otherwise, SQLModel might fail to initialize relationships properly
# for more details: https://github.com/fastapi/full-stack-fastapi-template/issues/28


def init_db(session: Session) -> None:
    # Tables should be created with Alembic migrations
    # But if you don't want to use migrations, create
    # the tables un-commenting the next lines
    # from sqlmodel import SQLModel

    # This works because the models are already imported and registered from app.models
    # SQLModel.metadata.create_all(engine)

    user = session.exec(
        select(User).where(User.email == settings.FIRST_SUPERUSER)
    ).first()
    if not user:
        user_in = UserCreate(
            email=settings.FIRST_SUPERUSER,
            password=settings.FIRST_SUPERUSER_PASSWORD,
            is_superuser=True,
        )
        user = crud.create_user(session=session, user_create=user_in)
