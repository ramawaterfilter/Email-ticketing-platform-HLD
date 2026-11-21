"""Thin wrapper over the Gmail API. Sync/ingest depend only on this small surface,
so tests can swap in an in-memory fake (tests/fakes.py) with the same methods.
"""

from __future__ import annotations

from typing import Protocol

DESK_LABEL = "Desk/Ticketed"
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


class HistoryExpired(Exception):
    """Gmail returned 404 for history.list: the cursor is too old. Caller does a full sync."""


class AuthRevoked(Exception):
    """Gmail returned 401 / invalid_grant: the mailbox's access was revoked."""


class GmailClient(Protocol):
    def history_list(self, start_history_id: str) -> list[str]: ...
    def messages_list(self, query: str) -> list[str]: ...
    def message_get(self, message_id: str) -> dict: ...
    def message_add_label(self, message_id: str, label_id: str) -> None: ...
    def attachment_get(self, message_id: str, attachment_id: str) -> bytes: ...
    def label_ensure(self, name: str) -> str: ...
    def profile_get(self) -> dict: ...
    def send_as_list(self) -> list[dict]: ...
    def message_send(self, raw: str, thread_id: str | None) -> dict: ...


class RealGmailClient:
    """Wraps googleapiclient. Only exercised with real credentials, so it stays thin."""

    def __init__(self, service, user_id: str = "me"):
        self._svc = service
        self._uid = user_id

    @classmethod
    def from_refresh_token(cls, refresh_token: str) -> "RealGmailClient":
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        from ..config import settings

        creds = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=settings.google_client_id,
            client_secret=settings.google_client_secret,
            scopes=SCOPES,
        )
        service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        return cls(service)

    def _run(self, request):
        from google.auth.exceptions import RefreshError
        from googleapiclient.errors import HttpError

        try:
            return request.execute()
        except RefreshError as exc:
            raise AuthRevoked(str(exc)) from exc
        except HttpError as exc:
            if exc.resp.status == 404:
                raise HistoryExpired(str(exc)) from exc
            if exc.resp.status in (401, 403):
                raise AuthRevoked(str(exc)) from exc
            raise

    def history_list(self, start_history_id: str) -> list[str]:
        ids: list[str] = []
        page = None
        while True:
            res = self._run(
                self._svc.users().history().list(
                    userId=self._uid,
                    startHistoryId=start_history_id,
                    historyTypes=["messageAdded"],
                    pageToken=page,
                )
            )
            for h in res.get("history", []):
                ids.extend(m["message"]["id"] for m in h.get("messagesAdded", []))
            page = res.get("nextPageToken")
            if not page:
                return list(dict.fromkeys(ids))

    def messages_list(self, query: str) -> list[str]:
        ids: list[str] = []
        page = None
        while True:
            res = self._run(
                self._svc.users().messages().list(
                    userId=self._uid, q=query, pageToken=page, includeSpamTrash="in:spam" in query
                )
            )
            ids.extend(m["id"] for m in res.get("messages", []))
            page = res.get("nextPageToken")
            if not page:
                return ids

    def message_get(self, message_id: str) -> dict:
        return self._run(
            self._svc.users().messages().get(userId=self._uid, id=message_id, format="full")
        )

    def message_add_label(self, message_id: str, label_id: str) -> None:
        self._run(
            self._svc.users().messages().modify(
                userId=self._uid, id=message_id, body={"addLabelIds": [label_id]}
            )
        )

    def attachment_get(self, message_id: str, attachment_id: str) -> bytes:
        import base64

        res = self._run(
            self._svc.users().messages().attachments().get(
                userId=self._uid, messageId=message_id, id=attachment_id
            )
        )
        return base64.urlsafe_b64decode(res["data"])

    def label_ensure(self, name: str) -> str:
        res = self._run(self._svc.users().labels().list(userId=self._uid))
        for label in res.get("labels", []):
            if label["name"] == name:
                return label["id"]
        created = self._run(
            self._svc.users().labels().create(userId=self._uid, body={"name": name})
        )
        return created["id"]

    def profile_get(self) -> dict:
        return self._run(self._svc.users().getProfile(userId=self._uid))

    def send_as_list(self) -> list[dict]:
        res = self._run(self._svc.users().settings().sendAs().list(userId=self._uid))
        return res.get("sendAs", [])

    def message_send(self, raw: str, thread_id: str | None) -> dict:
        body = {"raw": raw}
        if thread_id:
            body["threadId"] = thread_id
        return self._run(self._svc.users().messages().send(userId=self._uid, body=body))


def client_for(mailbox) -> GmailClient:
    """Production factory. The worker and API call this; tests inject a fake instead."""
    from ..crypto import decrypt

    if not mailbox.refresh_token_enc:
        raise AuthRevoked("mailbox has no stored token")
    return RealGmailClient.from_refresh_token(decrypt(mailbox.refresh_token_enc))
