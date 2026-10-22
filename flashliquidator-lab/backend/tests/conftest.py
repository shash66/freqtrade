import os

# Tests always run against their own database: TEST_DATABASE_URL, never whatever DATABASE_URL the shell happens
# to carry (a live/validation database must not receive fixture rows).
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "postgresql+psycopg://fll:fll_dev_password@127.0.0.1:5432/flashliquidator")
os.environ.setdefault("REDIS_URL", "redis://127.0.0.1:6379/0")
os.environ.setdefault("APP_ENV", "test")
