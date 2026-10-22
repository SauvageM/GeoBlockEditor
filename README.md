# GeoBlockEditor

Interface web légère pour gérer la liste des pays autorisés dans la configuration GeoIP de Nginx Proxy Manager.

L'application écrit deux fichiers :
- `data/active_countries.json` : la source de vérité (liste des pays actifs) ;
- `geoip.conf` : le fichier de configuration consommé par Nginx, régénéré à chaque modification.

Ne modifiez jamais `geoip.conf` à la main, vos changements seraient écrasés.

## Fonctionnalités

- Ajout et suppression de pays autorisés.
- Rechargement de Nginx depuis l'interface (`nginx -s reload` dans le conteneur Nginx Proxy Manager).
- Protection des actions sensibles par un code PIN (`APP_PIN`) : ajout, suppression et rechargement.

## Prérequis

- Python 3.10 ou plus récent (ou Docker).
- Un accès au conteneur `NginxProxyManager` (voir la section Docker).
- La base GeoIP `GeoLite2-Country.mmdb` montée dans le conteneur Nginx.

## Installation locale

```bash
python -m pip install -r requirements.txt
python app.py
```

L'interface est alors accessible sur `http://localhost:5000`.

## Structure du projet

```
GeoBlockEditor/
├── app.py
├── requirements.txt
├── data/
│   ├── available_countries.json   # pays proposés (fourni)
│   └── active_countries.json      # créé automatiquement
├── nginx/
│   └── geoip.conf                 # généré automatiquement
└── templates/
    └── index.html
```

## Configuration

Toute la configuration passe par des variables d'environnement. Les valeurs par défaut fonctionnent sans rien définir, sauf `APP_PIN` qu'il faut fournir pour pouvoir ajouter, supprimer ou recharger.

| Variable | Défaut | Rôle |
|---|---|---|
| `APP_PIN` | *(vide)* | Code PIN demandé pour ajouter, supprimer ou recharger. Si vide, ces actions sont refusées. |
| `FLASK_SECRET_KEY` | *(aléatoire)* | Clé de signature des sessions et tokens CSRF. Si elle n'est pas fixée, elle change à chaque démarrage. |
| `HOST` | `0.0.0.0` | Adresse d'écoute. |
| `PORT` | `5000` | Port d'écoute. |
| `LOG_LEVEL` | `INFO` | Niveau de journalisation (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `DATA_DIR` | `./data` | Dossier de stockage des pays actifs. |
| `COUNTRIES_FILENAME` | `active_countries.json` | Nom du fichier de stockage. |
| `NGINX_CONFIG_FILE` | `./nginx/geoip.conf` | Chemin complet du fichier généré pour Nginx. |
| `GEOIP_DB_PATH` | `/geoip/db/GeoLite2-Country.mmdb` | Chemin de la base GeoIP vu depuis le conteneur Nginx. |
| `NGINX_CONTAINER` | `NginxProxyManager` | Nom du conteneur Nginx Proxy Manager. |
| `NGINX_RELOAD_COMMAND` | `docker exec <NGINX_CONTAINER> nginx -s reload` | Commande exécutée pour recharger Nginx. |
| `NGINX_RELOAD_TIMEOUT` | `30` | Délai maximum (en secondes) du rechargement. |

## Lancement sur Unraid (User Scripts)

```bash
#!/bin/bash
cd /mnt/user/projects/GeoBlockEditor

export NGINX_CONFIG_FILE=/mnt/user/appdata/NginxProxyManager/geo/geoip.conf
export FLASK_SECRET_KEY="valeur-fixe-stockee-ailleurs"
export APP_PIN="123456"

python3 app.py > /var/log/geoblockeditor.log 2>&1 &
```

Pour que `FLASK_SECRET_KEY` reste stable entre deux démarrages, définissez-la en dur (ou dans un fichier privé chargé par le script).

## Docker

Construction de l'image :

```bash
docker build -t geoblockeditor:latest .
```

Lancement :

```bash
docker run -d \
  --name GeoBlockEditor \
  -p 5000:5000 \
  -e FLASK_SECRET_KEY="valeur-fixe-stockee-ailleurs" \
  -e APP_PIN="123456" \
  -e NGINX_CONTAINER="NginxProxyManager" \
  -e LOG_LEVEL=INFO \
  -v geo-config-data:/app/data \
  -v /mnt/user/appdata/NginxProxyManager/geo:/app/nginx \
  -v /var/run/docker.sock:/var/run/docker.sock \
  --log-driver json-file \
  --log-opt max-size=10m \
  --log-opt max-file=7 \
  geoblockeditor:latest
```

Le bouton « Recharger Nginx » exécute `docker` **depuis le conteneur de l'application**. Cela suppose :
- que le client Docker est installé dans l'image (ajoutez-le au `Dockerfile`, par exemple avec `apt-get install -y docker.io`) ;
- que le socket Docker de l'hôte est monté (`-v /var/run/docker.sock:/var/run/docker.sock`).

Si vous préférez ne pas monter le socket, rechargez Nginx à la main :

```bash
docker exec NginxProxyManager nginx -s reload
```

Si les droits du dossier de configuration posent problème, il peut être nécessaire de lancer :

```bash
sudo chown -R 1000:1000 /mnt/user/appdata/NginxProxyManager/geo
```

## Utilisation

1. Ouvrez l'interface dans votre navigateur.
2. Saisissez le code PIN dans le champ de la section concernée, puis cliquez sur le bouton. Les boutons restent désactivés tant que le champ est vide.
3. Après un ajout ou une suppression, cliquez sur **Recharger Nginx** (avec le PIN) pour appliquer la configuration.

## Sécurité

- **PIN** : il est vérifié côté serveur à chaque action, en temps constant. Le blocage des boutons n'est qu'un confort d'usage.
- **Pas de limitation d'essais** : un PIN court peut être testé par force brute. Gardez l'application dans votre réseau local, ou ajoutez un verrouillage si elle est exposée.
- **HTTP** : l'application n'utilise pas HTTPS. Le PIN circule en clair sur le réseau. Pour un accès hors LAN, passez par un reverse proxy avec HTTPS ou un VPN.
- **Clé secrète** : ne mettez jamais de clé dans le code. Si une clé a déjà été publiée, générez-en une nouvelle.
- **Proxy** : derrière Nginx Proxy Manager, l'adresse enregistrée dans les logs est celle du proxy, pas celle du client.

## Dépannage

- **« APP_PIN n'est pas défini »** ou **« Code PIN incorrect »** : vérifiez la variable `APP_PIN` et redémarrez l'application après toute modification.
- **« Commande introuvable : docker »** : le client Docker n'est pas installé dans le conteneur de l'application.
- **« Permission denied » sur le socket Docker** : l'utilisateur du processus n'a pas accès à `/var/run/docker.sock`.
- **Les modifications n'apparaissent pas dans Nginx** : vérifiez que le fichier `geoip.conf` a bien été régénéré, puis rechargez Nginx.
- **Logs** : sur Unraid, consultez `/var/log/geoblockeditor.log`. Avec Docker, utilisez `docker logs GeoBlockEditor`.

## Migration depuis l'ancienne version

- `NGINX_CONFIG_DIR` et `NGINX_CONFIG_FILENAME` ont été remplacées par `NGINX_CONFIG_FILE`, qui désigne le fichier complet.
- Les messages de succès demandent de **recharger Nginx** et non plus de redémarrer Nginx Proxy Manager.
