import requests

BASE_URL = "https://api.ticktick.com/open/v1"
HABIT_URL = f"{BASE_URL}/habit"
CHECKINS_URL = f"{BASE_URL}/habit/checkins"
CHECKIN_URL = BASE_URL + "/habit/{habit_id}/checkin"


class TickTickError(Exception):
    """Base para cualquier fallo al hablar con la API de TickTick."""


class TickTickAuthError(TickTickError):
    """Token invalido o caducado (401)."""


class TickTickNetworkError(TickTickError):
    """Fallo de conexion (timeout, DNS, etc.) hacia TickTick."""


class TickTickAPIError(TickTickError):
    """La API respondio algo distinto de lo esperado."""


class TickTickClient:
    def __init__(self, token):
        self._token = token

    def _headers(self):
        return {"Authorization": f"Bearer {self._token}"}

    def get_habits(self):
        try:
            resp = requests.get(HABIT_URL, headers=self._headers(), timeout=10)
        except requests.RequestException as exc:
            raise TickTickNetworkError(str(exc)) from exc

        if resp.status_code == 401:
            raise TickTickAuthError("Token invalido o expirado")
        if resp.status_code != 200:
            raise TickTickAPIError(f"GET habit -> status {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise TickTickAPIError("GET habit -> respuesta no es JSON valido") from exc

    def get_checkins_for(self, habit_ids, stamp):
        """Devuelve el set de habit_ids que YA tienen checkin para 'stamp'."""
        if not habit_ids:
            return set()

        try:
            resp = requests.get(
                CHECKINS_URL,
                headers=self._headers(),
                params={"habitIds": ",".join(habit_ids), "from": stamp, "to": stamp},
                timeout=10,
            )
        except requests.RequestException as exc:
            raise TickTickNetworkError(str(exc)) from exc

        if resp.status_code == 401:
            raise TickTickAuthError("Token invalido o expirado")
        if resp.status_code != 200:
            raise TickTickAPIError(f"GET habit/checkins -> status {resp.status_code}")

        try:
            entries = resp.json()
        except ValueError as exc:
            raise TickTickAPIError("GET habit/checkins -> respuesta no es JSON valido") from exc

        done = set()
        for entry in entries:
            if entry.get("checkins"):
                done.add(entry["habitId"])
        return done

    def create_checkin(self, habit_id, payload):
        try:
            resp = requests.post(
                CHECKIN_URL.format(habit_id=habit_id),
                headers={**self._headers(), "Content-Type": "application/json"},
                json=payload,
                timeout=10,
            )
        except requests.RequestException as exc:
            raise TickTickNetworkError(str(exc)) from exc

        if resp.status_code == 401:
            raise TickTickAuthError("Token invalido o expirado")
        if resp.status_code not in (200, 201):
            raise TickTickAPIError(f"POST habit/checkin -> status {resp.status_code}")
        return True
