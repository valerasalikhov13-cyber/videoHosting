#!/bin/bash
# Firewall VidSec: белый список соединений между микросервисами.
#
#   fw run              загрузить правила и работать (команда контейнера по умолчанию)
#   fw show             все правила, их состояние и счётчики пакетов
#   fw disable <имя>    запретить легитимное правило (для демонстрации ошибок)
#   fw enable <имя>     вернуть правило
#   fw reset            заново загрузить все правила (все включены)
#   fw drops            последние запрещённые пакеты (из журнала ядра)
set -u
RULES=/etc/vidsec/rules.conf
STATE=/run/vidsec-disabled       # имена отключённых правил
CHAIN=VIDSEC
NET=10.77.0.0/16
touch "$STATE"

# Docker может использовать nf_tables или legacy-бэкенд iptables — выбираем тот, где есть его правила
pick_backend() {
  local best="" best_n=-1
  for b in iptables-nft iptables-legacy iptables; do
    command -v "$b" >/dev/null 2>&1 || continue
    local n
    n=$($b -S FORWARD 2>/dev/null | grep -c DOCKER)
    if [ "$n" -gt "$best_n" ]; then best=$b; best_n=$n; fi
  done
  if [ "$best_n" -le 0 ]; then
    echo "ОШИБКА: не найдены правила Docker (цепочка DOCKER-USER). Контейнер должен работать с network_mode: host." >&2
    exit 1
  fi
  IPT=$best
}

rules() { grep -vE '^\s*(#|$)' "$RULES"; }
is_disabled() { grep -qx "$1" "$STATE"; }

build_chain() {
  $IPT -N $CHAIN 2>/dev/null || $IPT -F $CHAIN
  # 1. Отключённые правила: явный DROP до ESTABLISHED, чтобы оборвать и уже открытые соединения
  while read -r name src dst port desc; do
    is_disabled "$name" || continue
    local s=(); [ "$src" != any ] && s=(-s "$src")
    $IPT -A $CHAIN "${s[@]}" -d "$dst" -p tcp -m multiport --dports "$port" -m comment --comment "DISABLED $name" -j DROP
    $IPT -A $CHAIN -s "$dst" "${s[@]/-s/-d}" -p tcp -m multiport --sports "$port" -m comment --comment "DISABLED $name (ответ)" -j DROP
  done < <(rules)
  # 2. Ответы на разрешённые соединения
  $IPT -A $CHAIN -m conntrack --ctstate ESTABLISHED,RELATED -m comment --comment "ответные пакеты" -j ACCEPT
  # 3. Белый список
  while read -r name src dst port desc; do
    is_disabled "$name" && continue
    local s=(); [ "$src" != any ] && s=(-s "$src")
    $IPT -A $CHAIN "${s[@]}" -d "$dst" -p tcp -m multiport --dports "$port" -m conntrack --ctstate NEW \
      -m comment --comment "ALLOW $name" -j ACCEPT
  done < <(rules)
  # 4. Всё остальное — в журнал и DROP
  $IPT -A $CHAIN -m limit --limit 20/min -j LOG --log-prefix "VIDSEC-DROP " --log-level 4
  $IPT -A $CHAIN -m comment --comment "всё остальное запрещено" -j DROP
}

hook() {
  # Весь трафик сегментов 10.77.0.0/16 (внутри сегмента и наружу) проходит через цепочку VIDSEC
  $IPT -C DOCKER-USER -s $NET -j $CHAIN 2>/dev/null || $IPT -I DOCKER-USER 1 -s $NET -j $CHAIN
  $IPT -C DOCKER-USER -d $NET -j $CHAIN 2>/dev/null || $IPT -I DOCKER-USER 1 -d $NET -j $CHAIN
}

unhook() {
  $IPT -D DOCKER-USER -s $NET -j $CHAIN 2>/dev/null
  $IPT -D DOCKER-USER -d $NET -j $CHAIN 2>/dev/null
  $IPT -F $CHAIN 2>/dev/null; $IPT -X $CHAIN 2>/dev/null
}

check_bridge() {
  # Чтобы iptables видел трафик между контейнерами одной сети, нужен br_netfilter
  local f=/proc/sys/net/bridge/bridge-nf-call-iptables
  if [ "$(cat $f 2>/dev/null)" != 1 ]; then
    echo 1 > $f 2>/dev/null || echo "ВНИМАНИЕ: bridge-nf-call-iptables выключен, трафик внутри сегмента не фильтруется" >&2
  fi
}

show() {
  printf "\n%-17s %-12s %-12s %-6s %-9s %s\n" "ПРАВИЛО" "ИСТОЧНИК" "НАЗНАЧЕНИЕ" "ПОРТ" "СОСТОЯНИЕ" "ОПИСАНИЕ"
  while read -r name src dst port desc; do
    local st="разрешено"; is_disabled "$name" && st="ОТКЛЮЧЕНО"
    printf "%-17s %-12s %-12s %-6s %-9s %s\n" "$name" "$src" "$dst" "$port" "$st" "$desc"
  done < <(rules)
  printf "%-17s %-12s %-12s %-6s %-9s %s\n" "(по умолчанию)" "any" "any" "any" "ЗАПРЕЩЕНО" "всё, что не перечислено выше"
  echo
  echo "Цепочка iptables $CHAIN ($IPT), счётчики пакетов и байт:"
  $IPT -L $CHAIN -v -n --line-numbers 2>/dev/null
}

pick_backend
case "${1:-show}" in
  run)
    check_bridge; : > "$STATE"; build_chain; hook
    echo "Firewall VidSec включён ($IPT), правил: $(rules | wc -l)"
    trap 'unhook; echo "Firewall VidSec выключен"; exit 0' TERM INT
    while true; do sleep 3600 & wait $!; done ;;
  show)    show ;;
  disable) [ -n "${2:-}" ] && rules | awk '{print $1}' | grep -qx "$2" || { echo "Нет правила '${2:-}'"; exit 1; }
           is_disabled "$2" || echo "$2" >> "$STATE"; build_chain; echo "Правило $2 ОТКЛЮЧЕНО" ;;
  enable)  grep -vx "${2:-}" "$STATE" > "$STATE.tmp"; mv "$STATE.tmp" "$STATE"; build_chain; echo "Правило ${2:-} включено" ;;
  reset)   : > "$STATE"; build_chain; echo "Все правила включены" ;;
  drops)   dmesg 2>/dev/null | grep VIDSEC-DROP | tail -n "${2:-20}" || echo "Журнал ядра недоступен, смотрите счётчики: fw show" ;;
  *)       sed -n '2,10p' "$0" ;;
esac
