from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    ANTHROPIC_API_KEY: str = ""
    GOOGLE_OAUTH_CLIENT_ID: str = ""
    GOOGLE_OAUTH_CLIENT_SECRET: str = ""
    GOOGLE_OAUTH_REFRESH_TOKEN: str = ""
    SLACK_BOT_TOKEN: str = ""
    SLACK_SIGNING_SECRET: str = ""
    SLACK_NOTIFY_CHANNEL: str = "C0AQN1FNXNE"
    DATABASE_URL: str = ""
    BRAND_DEV_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    API_AUTH_TOKEN: str = "bpo-ops-dash-2026"
    SHEET_WEBHOOK_SECRET: str = "sheet-sync-2026"
    PUBLIC_URL: str = ""
    CORS_ORIGINS: str = "*"
    POLL_INTERVAL_SECONDS: int = 300
    APPROVAL_TIMEOUT_HOURS: int = 4
    DRY_RUN: bool = False
    TEMP_DIR: str = "/tmp/bpo-ops"
    BPO_DOMAINS: str = "resultscx.com,esal.com,esalglobal.com,startek.com,cgsinc.com,cp360.com"
    ATTIO_API_KEY: str = ""
    ATTIO_SYNC_ENABLED: bool = True

    @property
    def bpo_domain_list(self) -> list[str]:
        return [d.strip() for d in self.BPO_DOMAINS.split(",") if d.strip()]

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
