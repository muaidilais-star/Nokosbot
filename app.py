import os
import staypresent

staypresent.web.json({"status": "running"})

if __name__ == "__main__":
    staypresent.run("bot.py", port=int(os.getenv("PORT", 8080)))
