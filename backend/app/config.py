from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_title: str = "SDG T1 API"
    cors_origins: list[str] = ["http://localhost:5173"]

    class Config:
        env_file = ".env"


settings = Settings()