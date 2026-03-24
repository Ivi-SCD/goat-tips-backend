import os
import certifi
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()

os.environ.setdefault("SSL_CERT_FILE", certifi.where())


class Config(BaseSettings):
    # BetsAPI
    BETSAPI_TOKEN: str = os.getenv("BETSAPI_TOKEN", "")
    PREMIER_LEAGUE_ID: int = int(os.getenv("PREMIER_LEAGUE_ID", "94"))

    # Groq (narrative service) — browse models: https://console.groq.com/docs/models
    GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")
    GROQ_MODEL: str = os.getenv("GROQ_MODEL", "moonshotai/kimi-k2-instruct")

    # Supabase / PostgreSQL
    SUPABASE_DB_URL: str = os.getenv("SUPABASE_DB_URL", "")
    SUPABASE_DB_URL_ASYNC: str = os.getenv("SUPABASE_DB_URL_ASYNC", "")

    # IBM Cloud Object Storage (model artifacts — goat-tips-bucket, us-south)
    IBM_COS_ACCESS_KEY_ID: str = os.getenv("IBM_COS_ACCESS_KEY_ID", "")
    IBM_COS_SECRET_ACCESS_KEY: str = os.getenv("IBM_COS_SECRET_ACCESS_KEY", "")
    IBM_COS_ENDPOINT: str = os.getenv("IBM_COS_ENDPOINT", "https://s3.us-south.cloud-object-storage.appdomain.cloud")
    IBM_COS_BUCKET: str = os.getenv("IBM_COS_BUCKET", "goat-tips-bucket")
    MODEL_BLOB_NAME: str = os.getenv("MODEL_BLOB_NAME", "poisson_model.pkl")

    # Google / Vertex AI Search (web_search tool for /ask endpoint)
    GOOGLE_SA_JSON_PATH: str = os.getenv("GOOGLE_SA_JSON_PATH", "config/gcp_service_account.json")

    # Telegram Bot
    TELEGRAM_TOKEN: str = os.getenv("TELEGRAM_TOKEN", "")
    TELEGRAM_CHANNEL_ID: str = os.getenv("TELEGRAM_CHANNEL_ID", "@goat_tips_32")

    # Misc
    KAGGLE_API_KEY: str = os.getenv("KAGGLE_API_KEY", "")

    class Config:
        env_file = ".env"
        extra = "ignore"


_instance: Config | None = None


def get_settings() -> Config:
    global _instance
    if _instance is None:
        _instance = Config()
    return _instance
