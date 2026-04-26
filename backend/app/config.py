from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Weather Market Analyzer"
    database_url: str = "postgresql://postgres:postgres@localhost:5432/weather_analyzer"
    tomorrow_api_key: str = ""
    tracked_cities: str = (
        "tokyo,munich,london,seoul,shanghai,ankara,mexico-city,austin,"
        "hong-kong,chengdu,nyc,toronto,singapore,paris,madrid,warsaw,miami,denver,atlanta,chicago"
    )
    max_cities: int = 20
    scheduler_interval_seconds: int = 3600
    tomorrow_rps_limit: int = 3
    external_api_retries: int = 4
    run_scheduler_in_api: bool = False


settings = Settings()
