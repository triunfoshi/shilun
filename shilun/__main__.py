import uvicorn

from shilun.common.config import load_config


def main() -> None:
    config = load_config()
    uvicorn.run(
        "shilun.api:app",
        host=config.api_host,
        port=config.api_port,
        log_level=config.api_log_level,
        reload=False,
    )


if __name__ == "__main__":
    main()
