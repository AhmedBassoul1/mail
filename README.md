# Mailer — envoi automatisé d'e-mails avec pièce jointe

## 1. Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## 2. Configuration Gmail (App Password)

1. Activer la validation en 2 étapes sur le compte Google : https://myaccount.google.com/security
2. Générer un mot de passe d'application : https://myaccount.google.com/apppasswords
3. Copier `.env.example` en `.env` et renseigner :
   ```
   GMAIL_ADDRESS=mon.compte@gmail.com
   GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx
   ```
   Le mot de passe principal du compte n'est jamais utilisé ni stocké.

## 3. Préparer les fichiers

- `sources.txt` : texte brut (peut contenir n'importe quoi) — les adresses e-mail valides sont extraites automatiquement par regex.
- `attachments/CV.pdf` : la pièce jointe à envoyer (placez votre fichier ici, ou passez un autre chemin via `--attachment`).
- `body.txt` : corps du message envoyé (texte libre).

## 4. Lancer un essai à blanc

```bash
python mailer.py --dry-run --verbose
```

Aucun e-mail n'est réellement envoyé ; cela vérifie l'extraction, la pièce jointe et le corps du message.

## 5. Envoi réel

```bash
python mailer.py --sources sources.txt --attachment attachments/CV.pdf --delay 8
```

## Anti-doublons

Chaque destinataire contacté avec succès est enregistré dans `sent_emails.json`.
Si le script est interrompu puis relancé, les adresses déjà marquées `"sent"` sont automatiquement ignorées.
Supprimez ou éditez ce fichier pour forcer un renvoi.

## Options disponibles

| Option | Défaut | Description |
|---|---|---|
| `--sources` | `sources.txt` | Fichier texte à scanner |
| `--attachment` | `attachments/CV.pdf` | Pièce jointe |
| `--subject` | `Candidature spontanée` | Objet de l'e-mail |
| `--body-file` | `body.txt` | Fichier du corps du message |
| `--delay` | `5` | Pause (secondes) entre deux envois |
| `--log-file` | `sent_emails.json` | Fichier de suivi anti-doublons |
| `--dry-run` | — | Simule sans envoyer |
| `--verbose` | — | Logs détaillés |

## Limites Gmail

Un compte Gmail standard est limité à ~500 e-mails/jour. Gardez un `--delay` raisonnable (plusieurs secondes) pour rester dans un usage normal et éviter un blocage temporaire du compte.
