from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]  # repo root, where .env lives


class Settings(BaseSettings):
    # Every setting is read as DESK_<NAME>. The prefix matters: machines often have a
    # global DATABASE_URL from another project, and this app must never pick that up.
    model_config = SettingsConfigDict(env_prefix="DESK_", env_file=ROOT / ".env", extra="ignore")

    environment: str = "dev"  # dev | prod
    database_url: str = "postgresql+psycopg://desk:desk@127.0.0.1:5432/desk"
    session_secret: str  # no default on purpose: the app must not start without one
    public_url: str = "http://localhost:5173"  # address users type in the browser

    google_client_id: str = ""
    google_client_secret: str = ""
    allowed_domains: str = ""  # comma-separated Google Workspace domains
    bootstrap_admin_email: str = ""  # becomes the first admin on first sign-in

    token_key: str = ""  # Fernet key (DESK_TOKEN_KEY) that encrypts stored Gmail refresh tokens
    reconcile_window_days: int = 14  # how far back the nightly safety-net check looks

    dev_login_enabled: bool = False

    @property
    def domains(self) -> set[str]:
        return {d.strip().lower() for d in self.allowed_domains.split(",") if d.strip()}

    @property
    def dev_login(self) -> bool:
        # Password-less login for local work only. Never active in prod, whatever the flag says.
        return self.dev_login_enabled and self.environment == "dev"


settings = Settings()
