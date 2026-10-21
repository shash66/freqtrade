import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://fll:fll_dev_password@127.0.0.1:5432/flashliquidator")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_ENV", "test")
