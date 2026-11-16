"""Optional foreground crash recovery; Ctrl+C always stops the supervisor."""

import subprocess
import sys
import time


def supervise(args, run=subprocess.run, sleep=time.sleep):
    if "--sync" in args:
        print("Run command registration once with start-bot.cmd before using supervised mode.")
        return 2
    try:
        for attempt in range(6):
            result = run([sys.executable, "-m", "vesterian.bot", *args])
            if result.returncode == 0:
                return 0
            if attempt == 5:
                return result.returncode
            delay = min(5 * 2**attempt, 60)
            print(f"Bot exited unexpectedly; restarting in {delay}s ({attempt + 1}/5).", flush=True)
            sleep(delay)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(supervise(sys.argv[1:]))
