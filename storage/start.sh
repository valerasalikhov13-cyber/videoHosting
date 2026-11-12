#!/bin/sh
# Запуск SeaweedFS с S3-доступом только по ключу из .env (анонимный доступ выключен).
set -e
cat > /etc/seaweedfs-s3.json <<JSON
{
  "identities": [
    {
      "name": "vidsec",
      "credentials": [{ "accessKey": "${S3_ACCESS_KEY}", "secretKey": "${S3_SECRET_KEY}" }],
      "actions": ["Admin", "Read", "Write", "List", "Tagging"]
    }
  ]
}
JSON
chmod 644 /etc/seaweedfs-s3.json
exec /entrypoint.sh server -dir=/data -ip.bind=0.0.0.0 -s3 -s3.port=8333 -s3.config=/etc/seaweedfs-s3.json
