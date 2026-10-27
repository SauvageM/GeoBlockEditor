# GeoBlockEditor

Interface web légère pour gérer la liste des pays autorisés dans la configuration GeoIP de Nginx Proxy Manager.

L'application écrit deux fichiers :
- `data/active_countries.json` : la source de vérité (liste des pays actifs) ;
- `geoip.conf` : le fichier de configuration consommé par Nginx, régénéré à chaque modification.

Ne modifiez jamais `geoip.conf` à la main, vos changements seraient écrasés.

## Fonctionnalités

- Ajout et suppression de pays autorisés.
- Rechargement de Nginx depuis l'interface (`nginx -s reload` dans le conteneur Nginx Proxy Manager).
- Arrêt du processus depuis l'interface (utilise le PID écrit dans un fichier).
- Déverrouillage de l'interface par un code PIN unique (`APP_PIN`), valable pendant une durée configurable.

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
GeoBlockEditor/
├── app.py
├── requirements.txt
├── data/
│ ├── available_countries.json # pays proposés (fourni)
│ └── active_countries.json # créé automatiquement
├── nginx/
│ └── geoip.conf # généré automatiquement
└── templates/
└── index.html

## Configuration

Toute la configuration passe par des variables d'environnement. Les valeurs par défaut fonctionnent sans rien définir, sauf `APP_PIN` : sans lui, le déverrouillage est impossible et aucune action ne peut être effectuée.

| Variable | Défaut | Rôle |
|---|---|---|
| `APP_PIN` | *(vide)* | Code PIN qui déverrouille l'interface. Si vide, aucune action n'est possible. |
| `UNLOCK_MINUTES` | `30` | Durée du déverrouillage avant reverrouillage automatique. |
| `FLASK_SECRET_KEY` | *(aléatoire)* | Clé de signature des sessions et tokens CSRF. Si elle n'est pas fixée, le déverrouillage est perdu à chaque démarrage. |
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
| `PID_FILE` | `/var/run/geoblockeditor.pid` | Fichier contenant le PID du processus, utilisé par le bouton d'arrêt. |

## Lancement sur Unraid (User Scripts)

```bash
#!/bin/bash
cd /mnt/user/projects/GeoBlockEditor

export NGINX_CONFIG_FILE=/mnt/user/appdata/NginxProxyManager/geo/geoip.conf
export FLASK_SECRET_KEY="valeur-fixe-stockee-ailleurs"
export APP_PIN="123456"
# export UNLOCK_MINUTES=60   # facultatif

python3 app.py > /var/log/geoblockeditor.log 2>&1 &
echo $! > /var/run/geoblockeditor.pid
```

- `echo $!` écrit le PID du dernier processus lancé en arrière-plan. C'est indispensable pour que le bouton d'arrêt fonctionne.
- Pour que `FLASK_SECRET_KEY` reste stable entre deux démarrages, définissez-la en dur (ou dans un fichier privé chargé par le script).

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

Sous Docker, le fichier PID n'existe pas dans le conteneur : le bouton d'arrêt affichera une erreur. Utilisez `docker stop GeoBlockEditor` à la place.

Si les droits du dossier de configuration posent problème :

```bash
sudo chown -R 1000:1000 /mnt/user/appdata/NginxProxyManager/geo
```

## Utilisation

1. Ouvrez l'interface dans votre navigateur.
2. Saisissez le code PIN dans la barre en haut de la page, puis cliquez sur **Déverrouiller**.
3. Les boutons d'action deviennent actifs pendant `UNLOCK_MINUTES` minutes. Cliquez sur **Verrouiller** pour fermer l'accès plus tôt.
4. Ajoutez ou supprimez des pays, puis cliquez sur **Recharger Nginx** pour appliquer la configuration.

Le bouton **Arrêter l'application** demande une confirmation. Une fois l'application arrêtée, elle doit être relancée depuis le script Unraid (ou `python3 app.py` en local).

Le bouton d'arrêt ne fonctionne que si le PID du fichier correspond au processus qui sert la page. Si le fichier est périmé, l'arrêt est annulé et un message d'erreur s'affiche.

## Dépannage

- **« Interface verrouillée »** : déverrouillez-la avec le code PIN, ou vérifiez que `APP_PIN` est bien défini et que l'application a été redémarrée après la modification.
- **« Code PIN incorrect »** : vérifiez la valeur de `APP_PIN`.
- **« Commande introuvable : docker »** : le client Docker n'est pas installé dans le conteneur de l'application.
- **« Permission denied » sur le socket Docker** : l'utilisateur du processus n'a pas accès à `/var/run/docker.sock`.
- **« Fichier PID introuvable »** : le script de lancement n'a pas écrit le fichier (vérifiez la ligne `echo $! > ...`), ou vous êtes sous Docker.
- **« Le PID du fichier ne correspond pas »** : le fichier est périmé, relancez l'application via le script pour le régénérer.
- **Les modifications n'apparaissent pas dans Nginx** : vérifiez que `geoip.conf` a bien été régénéré, puis rechargez Nginx.
- **Erreur `NameError: name 'env' is not defined`** : la fonction `env` doit être définie avant toute variable de configuration qui l'utilise, juste après `BASE_DIR`.
- **Logs** : sur Unraid, consultez `/var/log/geoblockeditor.log`. Avec Docker, utilisez `docker logs GeoBlockEditor`.

## Sécurité

- **PIN** : il est vérifié côté serveur à chaque action, en temps constant. Le déverrouillage est stocké dans la session du navigateur, signée avec `FLASK_SECRET_KEY`.
- **Pas de limitation d'essais** : un PIN court peut être testé par force brute. Gardez l'application dans votre réseau local, ou ajoutez un verrouillage si elle est exposée.
- **HTTP** : l'application n'utilise pas HTTPS. Le PIN circule en clair sur le réseau. Pour un accès hors LAN, passez par un reverse proxy avec HTTPS ou un VPN.
- **Clé secrète** : ne mettez jamais de clé dans le code. Si une clé a déjà été publiée, générez-en une nouvelle.
- **Proxy** : derrière Nginx Proxy Manager, l'adresse enregistrée dans les logs est celle du proxy, pas celle du client.

## Migration depuis l'ancienne version

- Le PIN n'est plus demandé séparément dans chaque section : il se saisit une seule fois, en haut de la page.
- `NGINX_CONFIG_DIR` et `NGINX_CONFIG_FILENAME` ont été remplacées par `NGINX_CONFIG_FILE`, qui désigne le fichier complet.
- Les messages de succès demandent de **recharger Nginx** et non plus de redémarrer Nginx Proxy Manager.
- Le script de lancement doit désormais écrire le PID avec `echo $! > /var/run/geoblockeditor.pid`.