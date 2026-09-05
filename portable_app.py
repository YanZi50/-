import threading
import time
import webbrowser

from web_app import main


def open_browser() -> None:
    time.sleep(1)
    webbrowser.open("http://127.0.0.1:8765")


if __name__ == "__main__":
    threading.Thread(target=open_browser, daemon=True).start()
    main()
