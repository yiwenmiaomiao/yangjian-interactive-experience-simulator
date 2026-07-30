#!/bin/bash
# Room Tick 执行脚本
# cron 调用：bash ~/Documents/yangjian-room/room/runner.sh
set -e
cd ~/Documents/yangjian-room

# 使用 Hermes venv 的 Python 3.11（系统 Python 3.9 不兼容新版 urllib3）
PYTHON_BIN="${HOME}/.hermes/hermes-agent/venv/bin/python3"
if [ ! -x "$PYTHON_BIN" ]; then
    PYTHON_BIN="python3"
fi

# 加载 API key（从 .env 读取）
source .env 2>/dev/null || true
export DEEPSEEK_API_KEY
export http_proxy https_proxy no_proxy

# 运行 room tick
"$PYTHON_BIN" -c "
import sys; sys.path.insert(0, '.')
from room import room
result = room.tick(source='cron')
story = room.format_output(result)
print(story)
print('---ROOM_META---')
if result.get('ok'):
    print(f'场景: {result[\"decision\"].get(\"scene\", \"?\")}')
    print(f'排场: {\" → \".join(result[\"decision\"].get(\"order\", []))}')
else:
    print(f'错误: {result.get(\"error\", \"?\")}')
" 2>/dev/null
