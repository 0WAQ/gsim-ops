#!/usr/bin/env bash
# redis-diag.sh — 只读诊断, 不改任何东西。在 server-160 真实终端跑:
#   sudo bash scripts/redis-diag.sh
# 把全部输出贴回给 Claude。
PORT=6380
PWFILE=/etc/juicefs/alphalib-jfs.env
PW=$(grep -E '^META_PASSWORD=' "$PWFILE" | head -1 | cut -d= -f2-)
rcli() { redis-cli -p $PORT -a "$PW" --no-auth-warning "$@"; }

echo "========== 1. redis 自报配置 =========="
echo -n "redis_version: "; rcli INFO server 2>/dev/null | grep -i 'redis_version' | tr -d '\r'
echo -n "config_file:   "; rcli INFO server 2>/dev/null | grep -i 'config_file' | tr -d '\r'
echo -n "dir:           "; rcli CONFIG GET dir | tail -1
echo -n "maxclients:    "; rcli CONFIG GET maxclients | tail -1
echo -n "connected:     "; rcli INFO clients 2>/dev/null | grep -E '^connected_clients:' | tr -d '\r'

echo "========== 2. 在跑的 redis-server 进程 (PPID 看谁拉起的) =========="
ps -eo pid,ppid,user,etimes,cmd | grep -E '[r]edis-server.*6380'
echo "--- 上面进程的 PPID 是谁 ---"
for p in $(pgrep -f 'redis-server.*6380'); do
  ppid=$(awk '/^PPid:/{print $2}' /proc/$p/status 2>/dev/null)
  echo "redis pid=$p  ppid=$ppid  parent=[$(ps -o comm= -p $ppid 2>/dev/null)]"
  echo "  cmdline: $(tr '\0' ' ' < /proc/$p/cmdline)"
  echo "  exe:     $(readlink /proc/$p/exe)"
  echo "  cwd:     $(readlink /proc/$p/cwd)"
done

echo "========== 3. systemd unit 模板 =========="
systemctl cat redis-server@6380.service 2>/dev/null | grep -iE '^\[|ExecStart|EnvironmentFile|LimitNOFILE|PIDFile|User=' || echo "(无此 unit)"
echo "--- 6380 unit 运行态 ---"
systemctl is-active redis-server@6380.service 2>/dev/null
systemctl is-enabled redis-server@6380.service 2>/dev/null

echo "========== 4. 候选配置文件 + 权限 (REWRITE 为何 Permission denied) =========="
ls -la /etc/redis/ 2>/dev/null
for f in /etc/redis/redis.conf /etc/redis/6380.conf /etc/redis/redis-6380.conf; do
  [ -f "$f" ] && { echo "--- $f ---"; ls -la "$f"; grep -iE '^\s*(maxclients|requirepass|port|dir)\b' "$f" 2>/dev/null; }
done
echo "redis 进程跑在哪个 user: $(ps -o user= -p $(pgrep -f 'redis-server.*6380' | head -1) 2>/dev/null)"

echo "========== 5. redis 进程的 fd 上限 (maxclients 能否真到 50000, 需 NOFILE>=maxclients+32) =========="
for p in $(pgrep -f 'redis-server.*6380'); do
  echo -n "pid=$p NOFILE soft/hard: "; grep -E 'Max open files' /proc/$p/limits 2>/dev/null | awk '{print $4" / "$5}'
done

echo "========== 6. 本机 6380 连接来源 (160 自己 vs 其它), 确认本机是否是泄漏大头 =========="
ss -Htn state established sport = :$PORT 2>/dev/null | awk '{print $4}' | sed -E 's/:[0-9]+$//' | sort | uniq -c | sort -rn

echo "========== 7. 本机 juicefs mount 进程 + 它持有多少 6380 连接 =========="
for p in $(pgrep -f 'juicefs mount'); do
  n=$(ls -l /proc/$p/fd 2>/dev/null | grep -c socket)
  echo "juicefs pid=$p  total_sockets=$n  etime=$(ps -o etimes= -p $p)s"
done
echo "--- juicefs 版本 ---"
juicefs version 2>/dev/null || /usr/local/bin/juicefs version 2>/dev/null || echo "(juicefs 不在 PATH)"
