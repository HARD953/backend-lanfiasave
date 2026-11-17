"""
Points de branchement vers les partenaires extérieurs (lot 3). Chacun fonctionne « à vide » tant que la
convention n'est pas signée : rien n'est envoyé hors de la plateforme sans configuration explicite.

- SMS / numéro court : LANFIA_SMS_BACKEND = "log" (défaut, journal seulement) ou "http" (LANFIA_SMS_URL,
  LANFIA_SMS_TOKEN) ; réception : LANFIA_SMS_INBOUND_TOKEN.
- Registre social unique : LANFIA_RSU_URL et LANFIA_RSU_TOKEN, seulement après accord du gestionnaire et de l'ARTCI.
- Paiements : l'ordre de paiement est exporté pour le payeur, qui confirme chaque remise avec sa preuve.
"""
import json
import logging
import os
import urllib.request

logger = logging.getLogger("lanfiasave.integrations")


def sms_configured() -> bool:
    return os.getenv("LANFIA_SMS_BACKEND", "log") == "http" and bool(os.getenv("LANFIA_SMS_URL"))


def send_sms(phone: str, text: str) -> dict:
    """Envoie un SMS au ménage. Sans opérateur configuré, le message est seulement journalisé."""
    if not phone:
        return {"sent": False, "reason": "no_phone"}
    if not sms_configured():
        logger.debug("SMS (non envoyé, aucun opérateur configuré) vers %s…: %s", phone[:4], text)
        return {"sent": False, "reason": "not_configured"}
    req = urllib.request.Request(
        os.environ["LANFIA_SMS_URL"], data=json.dumps({"to": phone, "text": text}).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.getenv('LANFIA_SMS_TOKEN', '')}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (URL fixée par la configuration)
            return {"sent": 200 <= resp.status < 300}
    except Exception as exc:  # l'échec d'un SMS ne bloque jamais le dossier
        logger.warning("Échec d'envoi SMS : %s", exc)
        return {"sent": False, "reason": "error"}


def rsu_configured() -> bool:
    return bool(os.getenv("LANFIA_RSU_URL"))


def rsu_lookup(claim) -> dict:
    """Vérifie l'inscription au RSU, seulement si l'accès a été autorisé et configuré."""
    if not rsu_configured():
        return {"configured": False,
                "message": "Échange avec le RSU non activé : il faut l'accord du gestionnaire et de l'ARTCI."}
    payload = {"name": claim.claimant_name, "birth_year": claim.claimant_birth_year, "village": claim.village}
    req = urllib.request.Request(
        os.environ["LANFIA_RSU_URL"], data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.getenv('LANFIA_RSU_TOKEN', '')}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            data = json.loads(resp.read() or b"{}")
            return {"configured": True, "registered": bool(data.get("registered"))}
    except Exception as exc:
        logger.warning("Échec de la vérification RSU : %s", exc)
        return {"configured": True, "error": True, "message": "RSU injoignable, réessayer plus tard."}
