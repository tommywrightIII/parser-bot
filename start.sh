#!/bin/bash
# Японский прокси туннель (Mercari/Rakuma)
SOCKS_JP="${PROXY_URL#socks5://}"
./gost -L http://127.0.0.1:8899 -F socks5://$SOCKS_JP &
JP_PID=$!

# Китайский/Гонконг прокси туннель (95App)
SOCKS_CN="${PROXY_CN#socks5://}"
./gost -L http://127.0.0.1:8900 -F socks5://$SOCKS_CN &
CN_PID=$!

sleep 2
python bot.py
kill $JP_PID $CN_PID
