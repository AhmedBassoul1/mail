#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import re
import smtplib
import sys
import time
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

# Chargement optionnel d'un fichier .env (facultatif, ne casse rien si absent)
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

LOGGER = logging.getLogger("mailer")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465

# Regex de détection d'adresses e-mail (suffisante pour de l'extraction en
# texte libre : local-part @ domaine . tld).
EMAIL_REGEX = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)

DEFAULT_BODY = (
    "Bonjour,\n\n"
    "Veuillez trouver ci-joint mon CV.\n\n"
    "Cordialement,\n"
)


def setup_logging(verbose: bool = False) -> None:
    """Configure le logging console."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def extract_emails_from_file(path: str | Path) -> list[str]:
    """
    Lit un fichier texte brut et en extrait toutes les adresses e-mail
    valides et uniques, dans l'ordre d'apparition.

    Lève FileNotFoundError si le fichier n'existe pas.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Fichier introuvable : {path}")

    content = path.read_text(encoding="utf-8", errors="ignore")
    found = [addr.strip().lower() for addr in EMAIL_REGEX.findall(content)]

    # dict.fromkeys() déduplique tout en conservant l'ordre d'apparition.
    unique_emails = list(dict.fromkeys(found))

    nb_doublons = len(found) - len(unique_emails)
    if nb_doublons > 0:
        LOGGER.info(
            "%d doublon(s) détecté(s) et ignoré(s) dans %s (%d adresses brutes -> "
            "%d adresses uniques).",
            nb_doublons,
            path,
            len(found),
            len(unique_emails),
        )

    return unique_emails


def load_sent_log(path: str | Path) -> dict:
    """
    Charge le journal des destinataires déjà traités.

    Format : {"adresse@example.com": {"status": "sent"|"failed", "timestamp": "..."}}
    Retourne un dict vide si le fichier n'existe pas encore ou est corrompu.
    """
    path = Path(path)
    if not path.is_file():
        return {}

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        LOGGER.warning("Journal illisible (%s), il sera recréé : %s", path, exc)
        return {}


def save_sent_log(path: str | Path, log_data: dict) -> None:
    """Écrit le journal sur disque (appelé après chaque envoi pour être sûr)."""
    path = Path(path)
    try:
        with path.open("w", encoding="utf-8") as f:
            json.dump(log_data, f, indent=2, ensure_ascii=False)
    except OSError as exc:
        LOGGER.error("Impossible d'écrire le journal %s : %s", path, exc)


def build_message(
    sender: str, recipient: str, subject: str, body: str, attachment_path: str | Path
) -> MIMEMultipart:
    """Construit un e-mail MIME avec corps texte et une pièce jointe."""
    msg = MIMEMultipart()
    msg["From"] = sender
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    attachment_path = Path(attachment_path)
    with attachment_path.open("rb") as f:
        part = MIMEApplication(f.read(), Name=attachment_path.name)
    part["Content-Disposition"] = f'attachment; filename="{attachment_path.name}"'
    msg.attach(part)

    return msg


def connect_smtp(sender: str, app_password: str) -> smtplib.SMTP_SSL:
    """Ouvre une nouvelle connexion SMTP_SSL et s'authentifie."""
    smtp = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30)
    smtp.login(sender, app_password)
    return smtp


# Erreurs qui indiquent que la connexion/socket est morte (par opposition à un
# rejet de destinataire) et qui justifient une reconnexion + une nouvelle
# tentative plutôt qu'un simple échec.
CONNECTION_ERRORS = (
    smtplib.SMTPServerDisconnected,
    smtplib.SMTPConnectError,
    smtplib.SMTPResponseException,
    ConnectionError,
    OSError,
)


def send_emails(
    recipients: list[str],
    sender: str,
    app_password: str,
    subject: str,
    body: str,
    attachment_path: str | Path,
    delay_min: float,
    delay_max: float,
    log_path: str | Path,
    dry_run: bool = False,
) -> None:
    """
    Envoie les e-mails un par un, avec pause entre chaque envoi, en mettant
    à jour le journal anti-doublons après chaque tentative.

    Gmail ferme les connexions SMTP restées inactives trop longtemps (ce qui
    arrive facilement avec des pauses de plusieurs dizaines de secondes entre
    deux envois) : on vérifie donc la connexion avant chaque envoi et on
    reconnecte automatiquement si besoin.
    """
    sent_log = load_sent_log(log_path)

    smtp = None
    if not dry_run:
        try:
            smtp = connect_smtp(sender, app_password)
        except smtplib.SMTPAuthenticationError as exc:
            LOGGER.error(
                "Authentification Gmail refusée. Vérifiez GMAIL_ADDRESS/"
                "GMAIL_APP_PASSWORD (App Password requis, pas le mot de passe "
                "principal) : %s",
                exc,
            )
            sys.exit(1)
        except (smtplib.SMTPException, OSError) as exc:
            LOGGER.error("Connexion SMTP impossible : %s", exc)
            sys.exit(1)

    total = len(recipients)
    try:
        for index, recipient in enumerate(recipients, start=1):
            LOGGER.info("[%d/%d] Envoi à %s...", index, total, recipient)
            try:
                msg = build_message(sender, recipient, subject, body, attachment_path)
            except OSError as exc:
                LOGGER.error("Pièce jointe illisible pour %s : %s", recipient, exc)
                sent_log[recipient] = {
                    "status": "failed",
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "reason": str(exc),
                }
                save_sent_log(log_path, sent_log)
                continue

            if dry_run:
                LOGGER.info(
                    "[DRY-RUN] Envoi simulé pour %s (non enregistré dans le journal)",
                    recipient,
                )
                if index < total:
                    delay = random.uniform(delay_min, delay_max)
                    LOGGER.info("Pause de %.1fs avant le prochain envoi...", delay)
                    time.sleep(delay)
                continue

            try:
                smtp.noop()
            except Exception:
                LOGGER.warning("Connexion SMTP inactive/fermée, reconnexion...")
                try:
                    smtp.close()
                except Exception:
                    pass
                smtp = connect_smtp(sender, app_password)

            try:
                try:
                    smtp.sendmail(sender, recipient, msg.as_string())
                except CONNECTION_ERRORS as exc:
                    LOGGER.warning(
                        "Connexion perdue en envoyant à %s (%s), reconnexion et "
                        "nouvelle tentative...",
                        recipient,
                        exc,
                    )
                    smtp = connect_smtp(sender, app_password)
                    smtp.sendmail(sender, recipient, msg.as_string())

                LOGGER.info("Envoyé avec succès à %s", recipient)
                sent_log[recipient] = {
                    "status": "sent",
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                }

            except smtplib.SMTPRecipientsRefused:
                LOGGER.warning("Adresse refusée par le serveur : %s", recipient)
                sent_log[recipient] = {
                    "status": "failed",
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "reason": "recipient_refused",
                }
            except (smtplib.SMTPException, OSError) as exc:
                LOGGER.error(
                    "Erreur SMTP pour %s (échec après reconnexion) : %s",
                    recipient,
                    exc,
                )
                sent_log[recipient] = {
                    "status": "failed",
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "reason": str(exc),
                }
            finally:
                # Sauvegarde immédiate : en cas d'arrêt brutal, rien n'est perdu.
                save_sent_log(log_path, sent_log)

            if index < total:
                delay = random.uniform(delay_min, delay_max)
                LOGGER.info("Pause de %.1fs avant le prochain envoi...", delay)
                time.sleep(delay)
    finally:
        if smtp is not None:
            try:
                smtp.quit()
            except (smtplib.SMTPException, OSError):
                pass


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Envoi automatisé d'e-mails avec pièce jointe via Gmail."
    )
    parser.add_argument(
        "--sources", default="sources.txt", help="Fichier texte source à scanner (défaut: sources.txt)"
    )
    parser.add_argument(
        "--attachment",
        default="attachments/CV.pdf",
        help="Chemin de la pièce jointe (défaut: attachments/CV.pdf)",
    )
    parser.add_argument(
        "--subject", default="Candidature spontanée", help="Objet de l'e-mail"
    )
    parser.add_argument(
        "--body-file",
        default="body.txt",
        help="Fichier texte contenant le corps du message (défaut: body.txt)",
    )
    parser.add_argument(
        "--delay-min",
        type=float,
        default=30.0,
        help="Pause minimale en secondes entre deux envois (défaut: 30)",
    )
    parser.add_argument(
        "--delay-max",
        type=float,
        default=120.0,
        help="Pause maximale en secondes entre deux envois (défaut: 120)",
    )
    parser.add_argument(
        "--log-file",
        default="sent_emails.json",
        help="Fichier de suivi anti-doublons (défaut: sent_emails.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simule l'envoi sans réellement contacter le serveur SMTP",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Logs plus détaillés"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    setup_logging(args.verbose)

    if args.delay_min < 0 or args.delay_max < args.delay_min:
        LOGGER.error(
            "Intervalle de délai invalide : --delay-min=%s --delay-max=%s",
            args.delay_min,
            args.delay_max,
        )
        sys.exit(1)

    sender = os.environ.get("GMAIL_ADDRESS")
    app_password = os.environ.get("GMAIL_APP_PASSWORD")
    if not sender or not app_password:
        LOGGER.error(
            "Variables d'environnement manquantes : définissez GMAIL_ADDRESS "
            "et GMAIL_APP_PASSWORD (voir README.md)."
        )
        sys.exit(1)

    try:
        all_recipients = extract_emails_from_file(args.sources)
    except FileNotFoundError as exc:
        LOGGER.error(str(exc))
        sys.exit(1)

    if not all_recipients:
        LOGGER.warning("Aucune adresse e-mail valide trouvée dans %s", args.sources)
        sys.exit(0)

    sent_log = load_sent_log(args.log_file)
    already_sent = {addr for addr, info in sent_log.items() if info.get("status") == "sent"}
    to_send = [addr for addr in all_recipients if addr not in already_sent]

    LOGGER.info(
        "%d adresse(s) trouvée(s), %d déjà contactée(s) (ignorée(s)), %d à traiter.",
        len(all_recipients),
        len(all_recipients) - len(to_send),
        len(to_send),
    )

    if not to_send:
        LOGGER.info("Rien à envoyer, tous les destinataires ont déjà été contactés.")
        sys.exit(0)

    attachment_path = Path(args.attachment)
    if not attachment_path.is_file():
        LOGGER.error("Pièce jointe introuvable : %s", attachment_path)
        sys.exit(1)

    body_path = Path(args.body_file)
    if body_path.is_file():
        body = body_path.read_text(encoding="utf-8")
    else:
        LOGGER.warning(
            "Fichier de corps %s introuvable, utilisation du corps par défaut.",
            body_path,
        )
        body = DEFAULT_BODY

    send_emails(
        recipients=to_send,
        sender=sender,
        app_password=app_password,
        subject=args.subject,
        body=body,
        attachment_path=attachment_path,
        delay_min=args.delay_min,
        delay_max=args.delay_max,
        log_path=args.log_file,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
