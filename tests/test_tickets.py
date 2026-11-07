from referee.tickets import acknowledgement, parse_ticket, ticket_id, usage


def test_title_and_description():
    ticket = parse_ticket(
        "@bench-referee ticket: Invoice totals round the wrong way\nSeen in invoice.py.\nSince v2.",
        "ana",
        "sw_1",
    )
    assert ticket.title == "Invoice totals round the wrong way"
    assert ticket.description == "Seen in invoice.py.\nSince v2."
    assert ticket.reporter == "ana"
    assert ticket.id == ticket_id("sw_1")


def test_prefix_variants():
    for body in [
        "ticket Fix login",
        "Ticket: Fix login",
        "TICKET - Fix login",
        "@a @b, ticket: Fix login",
        "  @bench-referee:   ticket:Fix login  ",
    ]:
        assert parse_ticket(body, "ana", "m").title == "Fix login", body


def test_not_a_ticket():
    for body in ["hello", "@bench-referee what can you do?", "tickets are great", "ticket:", "ticket:   \nbody", "", None]:
        assert parse_ticket(body, "ana", "m") is None, body


def test_mention_after_text_is_not_stripped():
    assert parse_ticket("please @bench-referee ticket: x", "ana", "m") is None


def test_id_is_stable_and_short():
    assert ticket_id("sw_abc") == ticket_id("sw_abc")
    assert ticket_id("sw_abc") != ticket_id("sw_abd")
    assert len(ticket_id("sw_abc")) == 8


def test_replies():
    ticket = parse_ticket("ticket: Fix login\nline one\nline two", "ana", "m")
    text = acknowledgement(ticket)
    assert text.startswith(f"Logged {ticket.id}: Fix login")
    assert "2 line(s)" in text
    assert "@bench-referee ticket:" in usage("bench-referee")


def test_dev_env_only_applies_to_dev_server(monkeypatch):
    from referee import config

    monkeypatch.setenv("SWITCH_URL", "http://127.0.0.1:52839")
    monkeypatch.setenv("SWITCH_REGISTRATION_TOKEN", "t")
    monkeypatch.delenv("BENCH_USER", raising=False)
    settings = config.settings()
    assert settings["dev"] is False and settings["user"] is None
    assert config.open_addressing(settings, "agent-id") is False
