#!/bin/bash
# Генерация sing-box rule-set geoip-ru.json из ru_cidrs.txt
# Вызывается после update_ru_routes.py

DATA_DIR="/home/alvik/vpn-service/data"
INPUT="$DATA_DIR/ru_cidrs.txt"
OUTPUT="$DATA_DIR/ruleset-geoip-ru.json"

if [ ! -f "$INPUT" ]; then
    echo "Error: $INPUT not found"
    exit 1
fi

# Формируем JSON: берём все CIDR и собираем в массив ip_cidr
{
    echo '{'
    echo '  "version": 2,'
    echo '  "rules": ['
    echo '    {'
    echo '      "ip_cidr": ['

    # Читаем все строки, добавляем запятые кроме последней
    awk 'NF {lines[NR]=$1} END {for(i=1;i<NR;i++) printf "        \"%s\",\n", lines[i]; printf "        \"%s\"\n", lines[NR]}' "$INPUT"

    echo '      ]'
    echo '    }'
    echo '  ]'
    echo '}'
} > "$OUTPUT"

echo "Generated $OUTPUT ($(wc -l < "$INPUT") CIDRs)"
