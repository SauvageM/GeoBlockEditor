import hmac
import json
import logging
import os
import re
import shlex
import signal
import subprocess
import threading
import time

from flask import Flask, Response, flash, redirect, render_template, request, session, url_for
from flask_wtf import CSRFProtect
from waitress import serve

# ---------------------------------------------------------------------------
# Configuration (toutes les valeurs peuvent être surchargées par variables
# d'environnement, voir README)
# ---------------------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def env(name, default):
    """Retourne la variable d'environnement ou la valeur par défaut si vide."""
    value = os.environ.get(name)
    return value if value else default


LOG_LEVEL = env("LOG_LEVEL", "INFO").upper()
HOST = env("HOST", "0.0.0.0")
PORT = int(env("PORT", "5000"))
APP_PIN = os.environ.get("APP_PIN", "")
UNLOCK_MINUTES = int(env("UNLOCK_MINUTES", "10"))

def pin_is_valid(pin):
    """Vérifie le code PIN en temps constant. Refuse tout si APP_PIN n'est pas défini."""
    if not APP_PIN:
        return False
    return hmac.compare_digest(pin.strip().encode("utf-8"), APP_PIN.encode("utf-8"))


def is_unlocked():
    """Vrai si la session a été déverrouillée et que la durée n'est pas écoulée."""
    until = session.get("unlocked_until", 0)
    return isinstance(until, (int, float)) and until > time.time()

# Dossier contenant les données de l'application (pays actifs)
DATA_DIR = env("DATA_DIR", os.path.join(BASE_DIR, "data"))
COUNTRIES_STORE = os.path.join(DATA_DIR, env("COUNTRIES_FILENAME", "active_countries.json"))

# Liste des pays disponibles (fournie avec le projet)
AVAILABLE_COUNTRIES_FILE = os.path.join(BASE_DIR, "data", "available_countries.json")

# Fichier généré, consommé par Nginx. Ne jamais l'éditer à la main.
NGINX_CONFIG_FILE = env(
    "NGINX_CONFIG_FILE",
    os.path.join(BASE_DIR, "nginx", "geoip.conf"),
)

# Chemin de la base GeoIP vu depuis le conteneur Nginx
GEOIP_DB_PATH = env("GEOIP_DB_PATH", "/geoip/db/GeoLite2-Country.mmdb")

# Conteneur Nginx Proxy Manager et commande de rechargement
NGINX_CONTAINER = env("NGINX_CONTAINER", "NginxProxyManager")
NGINX_RELOAD_COMMAND = env(
    "NGINX_RELOAD_COMMAND",
    f"docker exec {NGINX_CONTAINER} nginx -s reload",
)
NGINX_RELOAD_TIMEOUT = int(env("NGINX_RELOAD_TIMEOUT", "30"))

PID_FILE = env("PID_FILE", "/var/run/geoblockeditor.pid")
def read_pid_file():
    """Lit le PID dans PID_FILE. Retourne (pid, erreur)."""
    try:
        with open(PID_FILE, "r", encoding="utf-8") as file:
            raw = file.read().strip()
    except FileNotFoundError:
        return None, f"Fichier PID introuvable : {PID_FILE}"
    except OSError as exc:
        return None, f"Lecture du fichier PID impossible : {exc}"

    if not raw.isdigit():
        return None, "Le contenu du fichier PID est invalide."

    return int(raw), None


def _terminate_self():
    """Envoie SIGTERM au processus courant (appelé avec un léger délai)."""
    os.kill(os.getpid(), signal.SIGTERM)

# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("geo-config-app")

# ---------------------------------------------------------------------------
# Application Flask
# ---------------------------------------------------------------------------
app = Flask(__name__)

SECRET_KEY = os.environ.get("FLASK_SECRET_KEY")
if not SECRET_KEY:
    # Clé aléatoire : les sessions et tokens CSRF seront invalidés au
    # redémarrage. Définir FLASK_SECRET_KEY pour éviter ce comportement.
    SECRET_KEY = secrets.token_urlsafe(32)
    logger.warning("FLASK_SECRET_KEY non définie : clé temporaire générée.")
app.secret_key = SECRET_KEY

# Protection CSRF sur toutes les routes POST (champ "csrf_token" dans les formulaires)
csrf = CSRFProtect(app)

with open(AVAILABLE_COUNTRIES_FILE, "r", encoding="utf-8") as f:
    COUNTRIES = json.load(f)

NGINX_CONFIG_TEMPLATE = """# Fichier généré automatiquement par app.py à partir de {source}.
# Ne pas éditer directement : vos modifications seraient écrasées.
# Chargement de la base GeoIp
geoip2 {geoip_db} {{
    $geoip2_data_country_code source=$remote_addr country iso_code;
}}

# Custom log
log_format blocked_geo '$remote_addr - $geoip2_data_country_code';

# Pour bloquer dans les proxy hosts
map $geoip2_data_country_code $allowed_country {{
    default 0;
    '' 1;
{countries}
}}
"""


# ---------------------------------------------------------------------------
# Validation et stockage
# ---------------------------------------------------------------------------
def is_valid_iso2(code):
    """Vérifie uniquement le format : exactement deux lettres majuscules."""
    return bool(re.fullmatch(r"[A-Z]{2}", code))


def _normalize(countries):
    """Nettoie, déduplique et trie une liste de codes pays."""
    return sorted(
        {
            code.strip().upper()
            for code in countries
            if is_valid_iso2(code.strip().upper())
        }
    )


def read_countries():
    """Lit les codes pays actifs depuis COUNTRIES_STORE."""
    try:
        with open(COUNTRIES_STORE, "r", encoding="utf-8") as file:
            data = json.load(file)
    except FileNotFoundError:
        return []
    except json.JSONDecodeError:
        logger.error("Fichier %s corrompu, liste vide utilisée.", COUNTRIES_STORE)
        return []

    if not isinstance(data, list):
        return []

    return _normalize(data)


def _write_nginx_config(countries):
    """Régénère le fichier de configuration Nginx."""
    countries_block = "".join(f"    {country} 1;\n" for country in countries)

    content = NGINX_CONFIG_TEMPLATE.format(
        source=COUNTRIES_STORE,
        geoip_db=GEOIP_DB_PATH,
        countries=countries_block,
    )

    config_directory = os.path.dirname(NGINX_CONFIG_FILE)
    if config_directory:
        os.makedirs(config_directory, exist_ok=True)

    with open(NGINX_CONFIG_FILE, "w", encoding="utf-8") as file:
        file.write(content)


def write_countries(countries):
    """Enregistre la liste des pays (JSON) puis régénère la config Nginx."""
    valid_countries = _normalize(countries)

    data_directory = os.path.dirname(COUNTRIES_STORE)
    if data_directory:
        os.makedirs(data_directory, exist_ok=True)

    with open(COUNTRIES_STORE, "w", encoding="utf-8") as file:
        json.dump(valid_countries, file, indent=2, ensure_ascii=False)
        file.write("\n")

    _write_nginx_config(valid_countries)


# ---------------------------------------------------------------------------
# Rechargement de Nginx
# ---------------------------------------------------------------------------
def reload_nginx():
    """
    Exécute NGINX_RELOAD_COMMAND. Retourne (succès, message).
    La commande est découpée avec shlex et exécutée sans shell.
    """
    try:
        args = shlex.split(NGINX_RELOAD_COMMAND)
    except ValueError as exc:
        logger.error("Commande de rechargement invalide : %s", exc)
        return False, f"Commande de rechargement invalide : {exc}"

    if not args:
        return False, "Aucune commande de rechargement configurée."

    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=NGINX_RELOAD_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        logger.error("Commande introuvable : %s", args[0])
        return False, f"Commande introuvable : {args[0]}"
    except subprocess.TimeoutExpired:
        logger.error("Délai dépassé lors du rechargement de Nginx.")
        return False, f"Le rechargement de Nginx a dépassé le délai ({NGINX_RELOAD_TIMEOUT} s)."

    if result.returncode != 0:
        details = (result.stderr or result.stdout or "").strip()
        logger.error("Échec du rechargement de Nginx (code %s) : %s", result.returncode, details)
        return False, f"Échec du rechargement de Nginx (code {result.returncode}) : {details}"

    logger.info("Nginx rechargé avec succès.")
    return True, "Nginx a été rechargé avec succès. La nouvelle configuration est active."


# ---------------------------------------------------------------------------
# Journalisation des requêtes (waitress ne journalise pas par défaut)
# ---------------------------------------------------------------------------
@app.after_request
def log_request(response):
    logger.info(
        "%s %s %s -> %s",
        request.remote_addr,
        request.method,
        request.path,
        response.status_code,
    )
    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/", methods=["GET"])
def index():
    active_codes = read_countries()

    active_countries = [
        {"code": code, "name": COUNTRIES.get(code, "Pays non répertorié")}
        for code in active_codes
    ]

    available_countries = sorted(
        [
            (code, name)
            for code, name in COUNTRIES.items()
            if code not in active_codes
        ],
        key=lambda country: country[1],
    )

    return render_template(
        "index.html",
        active_countries=active_countries,
        available_countries=available_countries,
        config_file=NGINX_CONFIG_FILE,
        reload_command=NGINX_RELOAD_COMMAND,
        unlocked=is_unlocked(),
        unlock_minutes=UNLOCK_MINUTES,
    )

@app.route("/unlock", methods=["POST"])
def unlock():
    if not pin_is_valid(request.form.get("pin", "")):
        logger.warning("Code PIN incorrect depuis %s", request.remote_addr)
        flash("Code PIN incorrect.", "error")
        return redirect(url_for("index"))

    session["unlocked_until"] = time.time() + UNLOCK_MINUTES * 60
    flash(f"Interface déverrouillée pour {UNLOCK_MINUTES} minutes.", "success")
    return redirect(url_for("index"))


@app.route("/lock", methods=["POST"])
def lock():
    session.pop("unlocked_until", None)
    flash("Interface verrouillée.", "success")
    return redirect(url_for("index"))

@app.route("/countries/add", methods=["POST"])
def add_country():
    if not is_unlocked():
        flash("Interface verrouillée : déverrouillez-la avec le code PIN.", "error")
        return redirect(url_for("index"))

    code = request.form.get("country_code", "").strip().upper()

    if not is_valid_iso2(code):
        flash("Le code pays sélectionné est invalide.", "error")
        return redirect(url_for("index"))

    if code not in COUNTRIES:
        flash("Ce pays n'est pas présent dans la liste disponible.", "error")
        return redirect(url_for("index"))

    countries = read_countries()

    if code in countries:
        flash(f"Le pays {code} est déjà actif.", "error")
        return redirect(url_for("index"))

    countries.append(code)
    write_countries(countries)

    flash(
        f"{COUNTRIES[code]} ({code}) a été ajouté. "
        "Rechargez Nginx pour appliquer les modifications.",
        "success",
    )
    return redirect(url_for("index"))


@app.route("/countries/<code>/remove", methods=["POST"])
def remove_country(code):
    if not is_unlocked():
        flash("Interface verrouillée : déverrouillez-la avec le code PIN.", "error")
        return redirect(url_for("index"))

    code = code.strip().upper()

    if not is_valid_iso2(code):
        flash("Le code pays à supprimer est invalide.", "error")
        return redirect(url_for("index"))

    countries = read_countries()

    if code not in countries:
        flash(f"Le pays {code} n'est pas actif.", "error")
        return redirect(url_for("index"))

    countries.remove(code)
    write_countries(countries)

    country_name = COUNTRIES.get(code, "Le pays")

    flash(
        f"{country_name} ({code}) a été supprimé. "
        "Rechargez Nginx pour appliquer les modifications.",
        "success",
    )
    return redirect(url_for("index"))


@app.route("/nginx/reload", methods=["POST"])
def reload_nginx_route():
    if not is_unlocked():
        flash("Interface verrouillée : déverrouillez-la avec le code PIN.", "error")
        return redirect(url_for("index"))

    ok, message = reload_nginx()
    flash(message, "success" if ok else "error")
    return redirect(url_for("index"))

@app.route("/app/stop", methods=["POST"])
def stop_app():
    if not is_unlocked():
        flash("Interface verrouillée : déverrouillez-la avec le code PIN.", "error")
        return redirect(url_for("index"))

    pid, error = read_pid_file()
    if error:
        logger.error("Arrêt impossible : %s", error)
        flash(error, "error")
        return redirect(url_for("index"))

    # Ce processus sert la requête : son PID doit correspondre à celui du fichier.
    # Sinon, le fichier est périmé et on ne tue rien.
    current_pid = os.getpid()
    if pid != current_pid:
        message = (
            f"Le PID du fichier ({pid}) ne correspond pas à ce processus ({current_pid}). "
            "Arrêt annulé."
        )
        logger.error(message)
        flash(message, "error")
        return redirect(url_for("index"))

    try:
        os.remove(PID_FILE)
    except OSError:
        pass

    logger.warning("Arrêt de l'application demandé depuis %s", request.remote_addr)

    # Léger délai pour que la réponse HTTP parte avant l'arrêt
    threading.Timer(1.0, _terminate_self).start()

    html = """<!DOCTYPE html>
<html lang="fr"><head><meta charset="UTF-8"><title>GeoIP - Arrêt</title></head>
<body style="font-family: system-ui, sans-serif; padding: 40px;">
<h1>Application arrêtée</h1>
<p>GeoBlockEditor s'arrête. Relancez-la depuis le script utilisateur Unraid.</p>
</body></html>"""
    return Response(html, mimetype="text/html")

if __name__ == "__main__":
    logger.info("Démarrage du serveur waitress sur %s:%s", HOST, PORT)
    serve(app, host=HOST, port=PORT)