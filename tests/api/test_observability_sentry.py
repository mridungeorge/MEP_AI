"""A real sentry_sdk.init with a capturing transport: nothing from the frames' local variables reaches an event."""
import pytest

sentry_sdk = pytest.importorskip("sentry_sdk")


def test_an_exception_event_carries_no_local_variables_and_no_secret(monkeypatch):
    from sentry_sdk.transport import Transport

    from mep.observability import init_sentry

    sent: list[dict] = []

    class Capture(Transport):
        def capture_envelope(self, envelope):
            for item in envelope.items:
                if item.payload.json:
                    sent.append(item.payload.json)

    monkeypatch.setenv("SENTRY_DSN", "https://public@o0.ingest.sentry.io/1")
    assert init_sentry("test")
    sentry_sdk.get_client().transport = Capture({})

    secret = "".join(["Zx9", "-LIVE", "_TOKEN-not-hex"])
    street = "".join(["12 Sec", "ret Street"])

    def handler(share_token=secret, address=street):
        raise RuntimeError("failed")

    try:
        handler()
    except RuntimeError:
        sentry_sdk.capture_exception()
    sentry_sdk.flush()
    blob = str(sent)
    assert sent and secret not in blob and street not in blob
