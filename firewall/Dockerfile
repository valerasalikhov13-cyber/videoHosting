# netshoot: Alpine с iptables, conntrack, tcpdump, nc
FROM nicolaka/netshoot:v0.13
# Вариант iptables-legacy нужен, если Docker на машине использует legacy-бэкенд
RUN apk add --no-cache iptables-legacy 2>/dev/null || echo "iptables-legacy не установлен, будет использован только nf_tables"
COPY fw.sh /usr/local/bin/fw
RUN chmod +x /usr/local/bin/fw
ENTRYPOINT ["/usr/local/bin/fw"]
CMD ["run"]
