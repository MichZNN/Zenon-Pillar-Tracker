# Installatie en deployment

Dit document beschrijft de aanbevolen productie-installatie van Zenon Pillar
Tracker op een Linux-server met Docker. Debian ARM64 is het primaire
doelplatform, maar de applicatie gebruikt geen Ubuntu-specifieke commando's of
paden. De GitHub Workflow bouwt één multi-architecture image voor
`linux/amd64` en `linux/arm64`.

## Architectuur

De productie-installatie bestaat uit twee containers:

- `web` serveert het dashboard op poort 8080.
- `collector` verzamelt de Zenon-data in een aparte, door Docker beheerde
  achtergrondcontainer.

Beide containers gebruiken dezelfde hostdirectory voor SQLite en de
applicatielog. De runtimeconfiguratie staat in SQLite; er wordt tijdens runtime
geen JSON-configuratie gelezen. `.env` is alleen voor deploymentwaarden en
geheimen die niet in de database horen, zoals de optionele Telegram-bottoken.

De applicatie draait als UID/GID `10001` (`tracker`) zonder rootrechten. Alleen
de gemounte datadirectory is schrijfbaar. De applicatielog rouleert op basis van
de instellingen in het adminpanel. Daarnaast begrenst Compose de Docker
stdout/stderr-logs op 10 MB met maximaal drie bestanden per container.

Systemd draait niet in een container. Als de Linux-host systemd gebruikt,
beheert één host-unit de Compose-stack. Op een Linux-host zonder systemd kan
dezelfde stack rechtstreeks met `docker compose` worden beheerd.

## Vereisten

Installeer op de server een ondersteunde Docker Engine met Compose v2 volgens de
documentatie van de gekozen Linux-distributie. Er is geen Ubuntu-installatie of
Ubuntu-package nodig. Controleer daarna:

```sh
docker --version
docker compose version
```

De deployment gebruikt een rootful Docker daemon. De gebruiker die de
deployment uitvoert moet Docker mogen aanroepen, bijvoorbeeld via de
distributie-eigen Docker-groep of via een passend beheermodel. Lid zijn van de
`docker`-groep geeft in de praktijk root-equivalente rechten; gebruik daarom een
afzonderlijke deploymentgebruiker en beperk SSH-toegang.

## Serverdirectory aanmaken

Gebruik als vaste directory `/srv/zenon-pillar-tracker`. De meegeleverde
systemd-unit gebruikt dit pad. Wie een andere directory kiest, moet vóór het
installeren van de unit `WorkingDirectory` in
`deploy/systemd/zenon-pillar-tracker.service` aanpassen.

```sh
sudo mkdir -p /srv/zenon-pillar-tracker/deploy/bin
sudo mkdir -p /srv/zenon-pillar-tracker/deploy/systemd
sudo mkdir -p /srv/zenon-pillar-tracker/data_store
sudo chown -R "$(id -u):$(id -g)" /srv/zenon-pillar-tracker
```

Kopieer vervolgens vanuit deze repository:

```sh
cp compose.yaml /srv/zenon-pillar-tracker/compose.yaml
cp deploy/bin/deploy.sh /srv/zenon-pillar-tracker/deploy/bin/deploy.sh
cp deploy/systemd/zenon-pillar-tracker.service /srv/zenon-pillar-tracker/deploy/systemd/zenon-pillar-tracker.service
cp .env.example /srv/zenon-pillar-tracker/.env
```

## Environmentbestand en schrijfrechten

Open `/srv/zenon-pillar-tracker/.env` en vul de deploymentwaarden in. Gebruik
geen echte secrets in GitHub of in deze repository.

```dotenv
IMAGE=ghcr.io/michznn/zenon-pillar-tracker:main
WEB_BIND_ADDRESS=127.0.0.1
WEB_PORT=8080
DATA_DIR=/srv/zenon-pillar-tracker/data_store
TELEGRAM_BOT_API_KEY=
```

`TELEGRAM_BOT_API_KEY` is optioneel. Telegram- en Discord-instellingen die in
de database horen, configureert een administrator na het inloggen in het
portal. Zet het environmentbestand zo beperkt mogelijk:

```sh
sudo chmod 600 /srv/zenon-pillar-tracker/.env
sudo chown "$(id -u):$(id -g)" /srv/zenon-pillar-tracker/.env
```

De map die in `DATA_DIR` staat moet schrijfbaar zijn voor de containergebruiker
`10001:10001`. Dit is bewust nodig voor SQLite, SQLite-journalbestanden en de
roterende applicatielogs:

```sh
sudo chown -R 10001:10001 /srv/zenon-pillar-tracker/data_store
sudo chmod 750 /srv/zenon-pillar-tracker/data_store
```

Als `DATA_DIR` naar een andere bestaande directory wijst, pas dan dezelfde
rechten op precies die directory toe. Verwijder geen bestanden om dit op te
lossen.

## Eerste start

Controleer eerst de Compose-configuratie en start alleen de webcontainer:

```sh
cd /srv/zenon-pillar-tracker
docker compose config
docker compose pull web
docker compose up -d web
```

Initialiseer de database expliciet. Het script is idempotent en bewaart
bestaande gegevens:

```sh
docker compose run --rm web python tools/setup_database.py --database /app/data_store/pillar_tracker.sqlite3
```

Start daarna de collector:

```sh
docker compose up -d collector
docker compose ps
docker compose logs --tail=100 web collector
```

Open het dashboard via de reverse proxy of lokaal via
`http://127.0.0.1:8080`. Wanneer de database nog geen accounts bevat, stuurt
`/portal` automatisch door naar `/setup`. Maak daar het eerste adminaccount en
configureer vervolgens de node- en notificatie-instellingen in SQLite.

Bind de webcontainer standaard aan localhost en gebruik voor internettoegang
een reverse proxy met HTTPS. Zet `WEB_BIND_ADDRESS` alleen op een publiek
adres als firewall- en TLS-beveiliging elders correct geregeld zijn.

## Systemd op de Linux-host

Gebruik systemd alleen op hosts die het daadwerkelijk draaien. De unit start
en stopt de Compose-stack; systemd wordt niet in de image geïnstalleerd.

```sh
sudo cp /srv/zenon-pillar-tracker/deploy/systemd/zenon-pillar-tracker.service /etc/systemd/system/zenon-pillar-tracker.service
sudo systemctl daemon-reload
sudo systemctl enable --now zenon-pillar-tracker.service
sudo systemctl status zenon-pillar-tracker.service
```

Handige beheercommando's:

```sh
sudo systemctl restart zenon-pillar-tracker.service
sudo systemctl stop zenon-pillar-tracker.service
sudo systemctl start zenon-pillar-tracker.service
docker compose -f /srv/zenon-pillar-tracker/compose.yaml ps
```

De Compose `restart: unless-stopped`-policy vangt een gecrashte container op.
De systemd-unit zorgt voor starten na een hostreboot en voor het beheren van de
volledige stack. `docker compose down` verwijdert de containers en het netwerk,
maar niet de bind-mounted `data_store`.

Op een distributie waar de Docker systemd-unit anders heet dan
`docker.service`, pas je alleen `Requires=` en `After=` in de meegeleverde unit
aan. De Docker- en applicatiecommando's blijven hetzelfde.

## Lokaal Docker testen

Dezelfde image werkt op Windows voor ontwikkeling als Docker Desktop draait.
De productie-aanname blijft Linux; Windows is in de Workflow alleen een extra
testomgeving voor Python.

```sh
docker build --tag zenon-pillar-tracker:local .
IMAGE=zenon-pillar-tracker:local docker compose up -d web
docker compose run --rm web python tools/setup_database.py --database /app/data_store/pillar_tracker.sqlite3
IMAGE=zenon-pillar-tracker:local docker compose up -d collector
```

Op Windows kan `DATA_DIR` in `.env` naar een door Docker Desktop gedeelde map
wijzen. Controleer daar expliciet de file-sharing- en volume-permissies.

## Automatische GitHub deployment

`.github/workflows/ci-cd.yml` doet het volgende:

1. Bij pull requests en pushes naar `development` en `main` draait de testmatrix
   op `ubuntu-latest` en `windows-latest`.
2. Alleen een push naar `main` (dus ook een merge van `development` naar
   `main`) bouwt een image voor `linux/amd64` en `linux/arm64`.
3. De image wordt naar GHCR gepubliceerd met zowel `main` als een immutable
   volledige commit-SHA als tag.
4. De deploymentjob uploadt `compose.yaml`, het deploymentscript en de systemd-
   template naar de server.
5. Het script haalt de SHA-getagde image op en voert `docker compose up -d` uit.
   Gewijzigde containers worden daardoor vervangen/herstart; SQLite en logs
   blijven in `DATA_DIR` behouden.

Het script wordt dus bij elke deployment opnieuw naar de server gekopieerd. De
applicatiecode hoeft niet als git-checkout op de server te staan; die zit in de
containerimage. De systemd-unit hoeft alleen bij de eerste installatie te
worden geïnstalleerd. De workflow verandert geen secrets en schrijft `.env`
niet over. Het deploymentscript bewaart de actieve image-tag in
`.deploy-image.env`, zodat dezelfde versie ook na een hostreboot actief blijft.

### Benodigde GitHub Secrets

Maak deze repository- of environment-secrets aan:

| Secret | Waarde |
| --- | --- |
| `DEPLOY_HOST` | DNS-naam of IP-adres van de server |
| `DEPLOY_PORT` | SSH-poort; leeg betekent `22` |
| `DEPLOY_USER` | Afzonderlijke SSH/deploymentgebruiker |
| `DEPLOY_PATH` | Exact `/srv/zenon-pillar-tracker`, zonder spaties |
| `DEPLOY_SSH_KEY` | Private ed25519 SSH-key voor de deploymentgebruiker |
| `DEPLOY_KNOWN_HOSTS` | Vooraf geverifieerde host-keyregel(s) |

Als de GHCR-package private is, voeg dan ook toe:

| Secret | Waarde |
| --- | --- |
| `GHCR_DEPLOY_USERNAME` | GitHub-gebruikersnaam van de package-login |
| `GHCR_DEPLOY_TOKEN` | Token met minimaal `read:packages` |

De workflow accepteert geen onbekende host-key automatisch: `DEPLOY_KNOWN_HOSTS`
moet vooraf door de beheerder worden gevuld met een gecontroleerde SSH-hostkey.
Zo voorkom je dat een deployment stilzwijgend naar een verkeerde host gaat.

De deploymentgebruiker moet `docker compose` kunnen uitvoeren. Voor de eerste
installatie zijn rootrechten nodig voor `/srv`, de systemd-unit en de
datamaprechten; daarna is voor de workflow alleen Docker-toegang nodig.

## Handmatige update en rollback

Een handmatige update gebruikt hetzelfde script als GitHub Actions:

```sh
cd /srv/zenon-pillar-tracker
sh deploy/bin/deploy.sh ghcr.io/michznn/zenon-pillar-tracker:<commit-sha>
```

Gebruik voor rollback de volledige SHA van de vorige bekende goede deployment.
Omdat iedere image een eigen SHA-tag heeft, hoef je geen code op de server terug
te zetten. De gekozen rollback-tag wordt in `.deploy-image.env` bewaard en blijft
daarmee ook na een reboot actief. Controleer daarna:

```sh
docker compose ps
docker compose logs --tail=100 web collector
```

Als de build- of testjob faalt, start de deployjob niet. Als een deploymentjob
faalt vóór `docker compose up`, blijven de draaiende containers ongemoeid.

## Logs en backups

Bekijk containerlogs met:

```sh
cd /srv/zenon-pillar-tracker
docker compose logs --tail=200 web collector
```

De applicatielog staat standaard in
`/srv/zenon-pillar-tracker/data_store/pillar_tracker.log`. De maximale grootte
en het aantal backups zijn aanpasbaar door een administrator in het portal.
De Docker stdout/stderr-logs hebben aanvullend een vaste limiet van 10 MB × 3
per container in `compose.yaml`.

Maak een backup van SQLite en logs bij voorkeur terwijl de containers kort
gestopt zijn:

```sh
cd /srv/zenon-pillar-tracker
docker compose stop web collector
tar -czf "zenon-pillar-tracker-backup-$(date +%Y%m%d-%H%M%S).tar.gz" data_store
docker compose start web collector
```

Bewaar backups buiten de deploymentdirectory en test periodiek of een backup
daadwerkelijk kan worden teruggezet.

## Dashboardbesturing van de collector

De stack kan betrouwbaar met Docker en systemd worden gestart, gestopt en
herstart. Het dashboard krijgt bewust geen Docker socket en geen onbeperkte
systemd-rechten: dat zou een webrequest in de praktijk host-rootcontrole geven.
De veilige dashboardknoppen voor Start/Stop/Restart van alleen de collector
vereisen daarom een afzonderlijke, allowlisted host-control bridge. Die kan
later als beperkt hostproces worden toegevoegd zonder de webcontainer
privileged te maken. Tot die tijd gebruik je voor collectorbeheer de
systemd/Compose-commando's hierboven; de dashboardstatus toont wel of de
collector nog recent een succesvolle heartbeat heeft gemeld.
