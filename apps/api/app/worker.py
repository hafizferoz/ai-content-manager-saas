import time

from app.publishing import dispatch_next


def main() -> None:
    while True:
        try:
            dispatched = dispatch_next()
        except Exception as error:
            print(f"Worker dispatch failed: {type(error).__name__}", flush=True)
            dispatched = False
        if not dispatched:
            time.sleep(2)


if __name__ == "__main__":
    main()
