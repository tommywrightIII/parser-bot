import os

BOT_TOKEN = os.getenv("BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# Proxy (опционально, для обхода гео-блокировок)
PROXY_URL = os.getenv("PROXY_URL", None)  # например: "socks5://user:pass@host:port"

# Интервал авто-трекинга в секундах
TRACK_INTERVAL = int(os.getenv("TRACK_INTERVAL", "300"))  # 5 минут по умолчанию

# Максимум результатов на запрос
MAX_RESULTS = 10

# Поддерживаемые платформы
PLATFORMS = {
    "mercari": "Mercari Japan 🇯🇵",
    "rakuma": "Rakuma 🇯🇵",
    "95app": "95App 🇨🇳",
}

# Состояния товара (общие)
CONDITIONS = {
    "new": "Новый",
    "like_new": "Как новый",
    "good": "Хорошее",
    "fair": "Среднее",
    "poor": "Плохое",
}
