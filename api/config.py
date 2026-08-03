from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional

class Settings(BaseSettings):
    # These variables will automatically pull their values from the .env file
    DATABASE_URL: str
    REDIS_URL: str

    # Optional: a webhook URL (Slack incoming webhook, Discord webhook, etc.)
    # If not set, DLQ alerts are just logged to the console instead of sent anywhere.
    ALERT_WEBHOOK_URL: Optional[str] = None

    # Optional: SMTP settings for sending real emails directly (no dependency on Slack).
    # For Gmail: SMTP_HOST=smtp.gmail.com, SMTP_PORT=587, SMTP_USERNAME=your Gmail address,
    # SMTP_PASSWORD=a 16-character "App Password" (NOT your normal Gmail password - Google
    # requires a separate App Password for programs like this to send mail on your behalf).
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USERNAME: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    ALERT_EMAIL_TO: Optional[str] = None  # The address that should receive DLQ alert emails

    model_config = SettingsConfigDict(env_file=".env")

# We create one instance of this class to use across our whole app
settings = Settings()