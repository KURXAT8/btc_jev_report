import os
from pathlib import Path
import traceback

import pytest


def load(environ, project_env):
    from btc_jev.config import load_settings
    return load_settings(environ, project_env)


def test_mail_environment_does_not_import_old_project():
    from btc_jev.config import select_mail_settings
    values = {"GMAIL_ADDRESS": "sender@example.org", "RECIPIENT_EMAIL": "reader@example.org",
              "GMAIL_APP_PASSWORD": "fake-password", "GEMINI_API_KEY": "must-not-load",
              "DATA_DIR": "/wrong/project", "TYPESAFE_API_KEY": "old-key"}
    assert select_mail_settings(values) == {
        "GMAIL_ADDRESS": "sender@example.org", "RECIPIENT_EMAIL": "reader@example.org",
        "GMAIL_APP_PASSWORD": "fake-password"}
    assert values["GEMINI_API_KEY"] == "must-not-load"


def test_defaults_work_without_secrets_and_have_no_side_effects(project_env):
    before = dict(os.environ)
    settings = load({}, project_env)
    assert settings.data_dir == project_env.parent / "data"
    assert settings.timezone == "Asia/Taipei"
    assert settings.jev_model == "jev-1.13.0"
    assert settings.typesafe_api_key.get_secret_value() == ""
    assert settings.gmail_address.get_secret_value() == ""
    assert not settings.data_dir.exists()
    assert not project_env.exists()
    assert dict(os.environ) == before


def test_mail_file_is_explicit_and_only_mail_keys_are_loaded(project_env):
    old = project_env.parent / "mail.env"
    old.write_text('GMAIL_ADDRESS="sender@example.org"\nGMAIL_APP_PASSWORD=abcd efgh\n'
                   "RECIPIENT_EMAIL='reader@example.org'\nDATA_DIR=/wrong/project\n"
                   "GEMINI_API_KEY=must-not-load\nTYPESAFE_API_KEY=old-key\nJEV_MODEL=wrong\n")
    settings = load({"MAIL_ENV_FILE": str(old)}, project_env)
    assert settings.gmail_address.get_secret_value() == "sender@example.org"
    assert settings.gmail_app_password.get_secret_value() == "abcd efgh"
    assert settings.recipient_email.get_secret_value() == "reader@example.org"
    assert settings.typesafe_api_key.get_secret_value() == ""
    assert settings.data_dir == project_env.parent / "data"
    assert settings.jev_model == "jev-1.13.0"
    assert not hasattr(settings, "gemini_api_key")
    assert not hasattr(settings, "mail_env_file")


def test_process_over_project_over_mail_precedence(project_env):
    old = project_env.parent / "mail.env"
    old.write_text("GMAIL_ADDRESS=old@example.org\nGMAIL_APP_PASSWORD=old-pass\n"
                   "RECIPIENT_EMAIL=old-reader@example.org\n")
    project_env.write_text("MAIL_ENV_FILE=mail.env\nGMAIL_ADDRESS=project@example.org\n"
                           "RECIPIENT_EMAIL=project-reader@example.org\n"
                           "TYPESAFE_API_KEY=project-key\nDATA_DIR=local-data\n")
    settings = load({"GMAIL_ADDRESS": "env@example.org", "TYPESAFE_API_KEY": "env-key"}, project_env)
    assert settings.gmail_address.get_secret_value() == "env@example.org"
    assert settings.gmail_app_password.get_secret_value() == "old-pass"
    assert settings.recipient_email.get_secret_value() == "project-reader@example.org"
    assert settings.typesafe_api_key.get_secret_value() == "env-key"
    assert settings.data_dir == project_env.parent / "local-data"


def test_explicit_blank_overrides_old_secret_and_disables_mail_file(project_env):
    project_env.write_text("MAIL_ENV_FILE=missing.env\nTYPESAFE_API_KEY=old-key\n")
    settings = load({"MAIL_ENV_FILE": "", "TYPESAFE_API_KEY": ""}, project_env)
    assert settings.typesafe_api_key.get_secret_value() == ""


def test_environ_argument_is_not_merged_with_ambient_secrets(monkeypatch, project_env):
    monkeypatch.setenv("TYPESAFE_API_KEY", "ambient-secret")
    monkeypatch.setenv("MAIL_ENV_FILE", "/must/not/be/read")
    assert load({}, project_env).typesafe_api_key.get_secret_value() == ""


def test_quotes_comments_bom_export_and_no_shell_interpolation(project_env):
    marker = project_env.parent / "should-not-exist"
    project_env.write_text('\ufeff# comment\nexport TYPESAFE_API_KEY="fake # key" # tail\n'
                           "GMAIL_APP_PASSWORD='literal ${HOME} `id`'\n"
                           f'RECIPIENT_EMAIL="$(touch {marker})"\nDATA_DIR="a directory"\n')
    settings = load({}, project_env)
    assert settings.typesafe_api_key.get_secret_value() == "fake # key"
    assert settings.gmail_app_password.get_secret_value() == "literal ${HOME} `id`"
    assert settings.recipient_email.get_secret_value() == f"$(touch {marker})"
    assert settings.data_dir == project_env.parent / "a directory"
    assert not marker.exists()


@pytest.mark.parametrize("operation,names", [
    ("jev", {"TYPESAFE_API_KEY"}),
    ("mail", {"GMAIL_ADDRESS", "GMAIL_APP_PASSWORD", "RECIPIENT_EMAIL"}),
])
def test_secrets_required_only_by_relevant_operation(project_env, operation, names):
    from btc_jev.config import ConfigurationError, require_secrets
    settings = load({}, project_env)
    with pytest.raises(ConfigurationError) as exc:
        require_secrets(settings, operation)
    for name in names:
        assert name in str(exc.value)
    values = {name: "synthetic-only" for name in names}
    require_secrets(load(values, project_env), operation)


def test_blank_secret_is_missing(project_env):
    from btc_jev.config import ConfigurationError, require_secrets
    with pytest.raises(ConfigurationError, match="TYPESAFE_API_KEY"):
        require_secrets(load({"TYPESAFE_API_KEY": "  "}, project_env), "jev")


def test_unknown_operation_cannot_bypass_secret_validation(project_env):
    from btc_jev.config import require_secrets
    with pytest.raises(ValueError):
        require_secrets(load({}, project_env), "anything")


def test_repr_and_json_do_not_expose_any_secret(project_env):
    secrets = {"TYPESAFE_API_KEY": "fake-key-unique", "GMAIL_ADDRESS": "private@example.org",
               "GMAIL_APP_PASSWORD": "fake-pass-unique", "RECIPIENT_EMAIL": "target@example.org"}
    settings = load(secrets, project_env)
    rendered = repr(settings) + str(settings) + settings.model_dump_json()
    for value in secrets.values():
        assert value not in rendered


@pytest.mark.parametrize("value", ["Not/A_Zone", "", "../secret", "fake-sensitive-value"])
def test_invalid_timezone_is_redacted(project_env, value):
    from btc_jev.config import ConfigurationError
    environ = {"TIMEZONE": value, "TYPESAFE_API_KEY": "secret-key"}
    with pytest.raises(ConfigurationError) as exc:
        load(environ, project_env)
    trace = "".join(traceback.format_exception(exc.value))
    assert "TIMEZONE" in trace
    assert "secret-key" not in trace
    if value:
        assert value not in trace


def test_supported_timezone_and_explicit_model_version(project_env):
    settings = load({"TIMEZONE": "America/New_York", "JEV_MODEL": "jev-1.13.0"}, project_env)
    assert settings.timezone == "America/New_York"


def test_binance_hmac_credentials_accept_project_names_and_legacy_aliases(project_env):
    canonical=load({"BINANCE_API_KEY":"key-value","BINANCE_API_SECRET":"secret-value"},project_env)
    assert canonical.binance_api_key.get_secret_value()=="key-value"
    assert canonical.binance_api_secret.get_secret_value()=="secret-value"
    aliases=load({"API_KEY":"alias-key","secret_KEY":"alias-secret"},project_env)
    assert aliases.binance_api_key.get_secret_value()=="alias-key"
    assert aliases.binance_api_secret.get_secret_value()=="alias-secret"
    preferred=load({"API_KEY":"alias-key","BINANCE_API_KEY":"canonical-key",
                    "secret_KEY":"alias-secret","BINANCE_API_SECRET":"canonical-secret"},project_env)
    assert preferred.binance_api_key.get_secret_value()=="canonical-key"
    assert preferred.binance_api_secret.get_secret_value()=="canonical-secret"


def test_binance_credentials_are_secret_and_do_not_load_from_legacy_mail_file(project_env):
    old=project_env.parent/'mail.env'
    old.write_text('API_KEY=mail-file-key\nsecret_KEY=mail-file-secret\n')
    settings=load({"MAIL_ENV_FILE":str(old)},project_env)
    assert settings.binance_api_key.get_secret_value()==""
    assert settings.binance_api_secret.get_secret_value()==""
    settings=load({"BINANCE_API_KEY":"private-key","BINANCE_API_SECRET":"private-secret"},project_env)
    rendered=repr(settings)+settings.model_dump_json()
    assert "private-key" not in rendered and "private-secret" not in rendered


@pytest.mark.parametrize("model", ["", "latest", "jev-latest", "gemini", "jev-1.13.0\n"])
def test_model_must_be_an_explicit_pinned_jev_version(project_env, model):
    from btc_jev.config import ConfigurationError
    with pytest.raises(ConfigurationError, match="JEV_MODEL"):
        load({"JEV_MODEL": model}, project_env)


@pytest.mark.parametrize("text", ['TYPESAFE_API_KEY="secret-unterminated\n',
                                 "TYPESAFE_API_KEY secret-invalid\n", "=secret-invalid\n"])
def test_invalid_env_fails_without_echoing_contents(project_env, text, caplog):
    from btc_jev.config import ConfigurationError
    project_env.write_text(text)
    with pytest.raises(ConfigurationError) as exc:
        load({}, project_env)
    assert "secret-" not in "".join(traceback.format_exception(exc.value)) + caplog.text


def test_explicit_missing_mail_file_is_reported_safely(project_env):
    from btc_jev.config import ConfigurationError
    environ = {"MAIL_ENV_FILE": "private-file-name"}
    with pytest.raises(ConfigurationError, match="MAIL_ENV_FILE") as exc:
        load(environ, project_env)
    assert "private-file-name" not in "".join(traceback.format_exception(exc.value))


def test_blank_data_directory_is_rejected(project_env):
    from btc_jev.config import ConfigurationError
    with pytest.raises(ConfigurationError, match="DATA_DIR"):
        load({"DATA_DIR": ""}, project_env)
