#!/bin/sh
# Стартует от root: чинит права на bind-mount'ах /data (директории на хосте
# обычно принадлежат root и не доступны для записи юзеру app), затем
# дропается в непривилегированного пользователя app через gosu и запускает
# переданную команду (CMD из Dockerfile).
set -eu

DATA_DIR="${DATA_DIR:-/data}"

# Создаём подкаталоги, если ещё нет (на свежем bind-mount их не будет).
mkdir -p "$DATA_DIR/db" "$DATA_DIR/digital_content" "$DATA_DIR/product_images"

# Выравниваем владельца. Делаем только верхние уровни — рекурсивный chown
# на сотнях фотографий товара отрабатывал бы каждый рестарт долго и ничего
# полезного бы не делал, поэтому глубокую проверку оставляем за оператором
# при необходимости.
APP_UID="${APP_UID:-1000}"
APP_GID="${APP_GID:-1000}"
chown "$APP_UID:$APP_GID" "$DATA_DIR" \
                          "$DATA_DIR/db" \
                          "$DATA_DIR/digital_content" \
                          "$DATA_DIR/product_images" 2>/dev/null || true

exec gosu "$APP_UID:$APP_GID" "$@"
