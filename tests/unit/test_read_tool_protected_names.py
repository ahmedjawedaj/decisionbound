"""Which file names the read-only tools refuse, and which ordinary names they still serve.

The tools are a bounded inspection policy, not a sandbox. These tests check the
name policy only: a casefolded set of secret names, a few patterns, excluded
directories matched without regard to case and noncanonical path parts.
"""
import sys
from pathlib import Path

import pytest

from decisionbound import agent_loop
from decisionbound.agent_loop import repository_readonly_tools

BODY = "needle_body"

# Names a checkout can hold that carry keys or tokens. Each one has a supported text suffix,
# so the suffix filter alone does not stop it.
SECRET_FILES = [
    "id_rsa.txt", "id_ed25519.json", "id_ecdsa.txt", "ID_RSA.TXT",
    "credentials.yaml", "credentials.yml", "credentials.txt", "aws_credentials.json",
    "secrets.json", "Secrets.JSON", "secrets.yaml", "client_secrets.json", "secret.json",
    "client_secret_123.apps.json", "token.json",
    "service-account.json", "prod_service_account.json", "serviceaccount-ci.json",
    "kubeconfig.yaml", "kube-config.yml",
    "prod.pem.txt", "server.key.txt", "private.key.json", "cert.p12.txt", "cert.pfx.md",
    "prod.env.txt", "secrets.env.md", "prod.tfvars.json", "state.tfstate.json",
    "config/secrets.json", "deploy/keys/id_rsa.txt",
]

# Ordinary files whose names only resemble a secret name. None of them may be refused.
ORDINARY_FILES = [
    "README.md", "environment.md", "src/key_manager.py", "docs/keys.md", "secrets_policy.md",
    "credentials_guide.md", "secrets_manager.py", "id_utils.py", "keyboard.py", "monkey.py",
    "pem_notes.md", "envelope.txt", "tokenizer.py", "service_accounts.md", "kubernetes.md",
    "terraform.md", "config.yaml", "package.json",
]


def make_tree(root, names, body=BODY):
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)


def read(tools, path):
    return tools.call("read_file", {"path": path}, {"read_file"}, allow_writes=False)


def search(tools, query=BODY):
    return tools.call("search_project", {"query": query}, {"search_project"}, allow_writes=False)


@pytest.mark.parametrize("name", SECRET_FILES)
def test_secret_like_names_are_refused_for_read_and_skipped_by_search(tmp_path, name):
    make_tree(tmp_path, [name, "plain_notes.md"])
    tools = repository_readonly_tools(tmp_path)
    with pytest.raises(PermissionError):
        read(tools, name)
    assert [hit["file"] for hit in search(tools)["results"]] == ["plain_notes.md"]


@pytest.mark.parametrize("name", [
    ".env", ".env.local", ".env.production", ".env.staging", ".envrc", ".npmrc", ".pypirc",
    ".netrc", "_netrc", "prod.kubeconfig", ".pgpass", ".git-credentials", ".htpasswd", "kubeconfig", "kube_config",
    "id_rsa", "id_rsa.pub", "id_ed25519", "credentials.json", "prod.env", "server.pem", "tls.key",
    "bundle.p12", "bundle.pfx", "KUBECONFIG", ".ENV.Staging",
])
def test_secret_name_policy_covers_names_the_suffix_filter_already_stops(name):
    # Read and search also stop these through the suffix or hidden name rules.
    # The policy itself names them so that it does not depend on those rules.
    assert agent_loop._is_secret_name(name)


@pytest.mark.parametrize("name", [n.rsplit("/", 1)[-1] for n in ORDINARY_FILES])
def test_secret_name_policy_does_not_match_ordinary_names(name):
    assert not agent_loop._is_secret_name(name)


@pytest.mark.parametrize("name", ORDINARY_FILES)
def test_ordinary_names_stay_readable_and_searchable(tmp_path, name):
    make_tree(tmp_path, [name], body="body of " + name)
    tools = repository_readonly_tools(tmp_path)
    assert read(tools, name)["text"] == "body of " + name
    # Search reports the platform separator, so compare the path and not the spelling.
    assert [Path(hit["file"]).as_posix() for hit in search(tools, "body of " + name)["results"]] == [name]


# A colon starts a Windows alternate data stream, and Windows drops a trailing dot or space,
# so `node_modules.` and `node_modules ` name the excluded `node_modules` there.
NONCANONICAL = [
    "credentials.json:x.json", "notes:backup.md", "a.md:b.md",
    "node_modules./x.py", "node_modules /x.py", "pkg./y.py", "docs /guide.md", "sub:dir/z.py",
    "notes.md.", "notes.md ", "config.json.",
]


@pytest.mark.parametrize("name", NONCANONICAL)
def test_noncanonical_names_are_refused_even_when_no_file_exists(tmp_path, name):
    """The refusal is a name rule, so it holds on Windows where these files cannot be created."""
    with pytest.raises(PermissionError):
        read(repository_readonly_tools(tmp_path), name)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows cannot create files with these names")
@pytest.mark.parametrize("name", NONCANONICAL)
def test_noncanonical_names_are_refused_for_read_and_skipped_by_search(tmp_path, name):
    make_tree(tmp_path, [name, "plain_notes.md"])
    tools = repository_readonly_tools(tmp_path)
    with pytest.raises(PermissionError):
        read(tools, name)
    assert [hit["file"] for hit in search(tools)["results"]] == ["plain_notes.md"]


@pytest.mark.parametrize("name", [
    "Node_Modules/x.py", "NODE_MODULES/x.py", "__PyCache__/x.py", "src/.VENV/x.py", "Build/.Git/x.py"])
def test_excluded_directories_match_without_regard_to_case(tmp_path, name):
    make_tree(tmp_path, [name, "plain_notes.md"])
    tools = repository_readonly_tools(tmp_path)
    with pytest.raises(PermissionError):
        read(tools, name)
    found = search(tools)
    assert [hit["file"] for hit in found["results"]] == ["plain_notes.md"]
    assert found["files_scanned"] == 1

