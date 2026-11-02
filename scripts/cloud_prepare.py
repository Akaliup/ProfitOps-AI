"""Prepare non-Git cloud deployment material for the Compose overlay.

Creates or updates:
  * an env file with random database, JWT, demo-token and demo-password values
  * an Nginx htpasswd file for the independent HTTPS access gate
  * a self-signed TLS certificate for local verification

Existing values are preserved unless --force is passed. The generated files
are covered by .gitignore; never commit them.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import ipaddress
import os
import secrets
import shutil
import subprocess
from pathlib import Path

DEFAULT_KEYS = (
    "PROFITOPS_POSTGRES_USER",
    "PROFITOPS_POSTGRES_DB",
    "PROFITOPS_POSTGRES_PASSWORD",
    "PROFITOPS_AUTH_SECRET",
    "PROFITOPS_ANALYST_TOKEN",
    "PROFITOPS_APPROVER_TOKEN",
    "PROFITOPS_DEMO_ANALYST_PASSWORD",
    "PROFITOPS_DEMO_APPROVER_PASSWORD",
    "PROFITOPS_ACCESS_USER",
    "PROFITOPS_ACCESS_PASSWORD",
)


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _secret() -> str:
    return secrets.token_hex(24)


def _mount_path(path: Path) -> str:
    text = str(path).replace("\\", "/")
    if not text.startswith((".", "/")):
        text = "./" + text
    return text


def _htpasswd_line(user: str, password: str) -> str:
    # Nginx accepts the {SHA} scheme without an external crypt library.
    # The password is a generated 48-character hex value, so an unsalted
    # digest is acceptable for this access gate and avoids an OpenSSL runtime
    # dependency on the host that generates the file.
    digest = base64.b64encode(hashlib.sha1(password.encode("utf-8")).digest()).decode("ascii")
    return f"{user}:{{SHA}}{digest}\n"


def _make_cert(tls_dir: Path, domain: str, force: bool) -> None:
    cert = tls_dir / "fullchain.pem"
    key = tls_dir / "privkey.pem"
    if cert.exists() and key.exists() and not force:
        return
    openssl = shutil.which("openssl")
    if not openssl:
        raise SystemExit("openssl is required to create a self-signed certificate; install it or use --tls-mode existing")
    tls_dir.mkdir(parents=True, exist_ok=True)
    try:
        address = ipaddress.ip_address(domain)
    except ValueError:
        san = f"DNS:{domain},IP:127.0.0.1"
    else:
        san = f"IP:{address},IP:127.0.0.1"
    subprocess.run(
        [
            openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "365",
            "-keyout", str(key), "-out", str(cert), "-subj", f"/CN={domain}",
            "-addext", f"subjectAltName={san}",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        os.chmod(key, 0o600)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare ProfitOps cloud secrets, htpasswd and TLS material")
    parser.add_argument("--env-file", default=".env.cloud", help="non-Git env file (default: .env.cloud)")
    parser.add_argument("--auth-dir", default="deploy/auth", help="htpasswd directory (default: deploy/auth)")
    parser.add_argument("--tls-dir", default="deploy/tls", help="TLS certificate directory (default: deploy/tls)")
    parser.add_argument("--domain", default="localhost", help="certificate common name / DNS name")
    parser.add_argument("--https-bind", default="127.0.0.1", help="host bind address for the HTTPS port")
    parser.add_argument("--https-port", default="8443", help="host HTTPS port (default: 8443; use 443 in cloud)")
    parser.add_argument("--access-user", default=None, help="Nginx Basic Auth user")
    parser.add_argument("--access-password", default="", help="override the generated Basic Auth password")
    parser.add_argument("--tls-mode", choices=("self-signed", "existing"), default="self-signed")
    parser.add_argument("--force", action="store_true", help="rotate secrets and replace existing TLS material")
    args = parser.parse_args(argv)
    if not args.domain or any(char in args.domain for char in "/\\\n\r"):
        parser.error("--domain must be a DNS name or IP address without path separators")

    env_path = Path(args.env_file)
    if env_path.name == ".env":
        parser.error("--env-file must not be the development .env; use .env.cloud")
    auth_dir = Path(args.auth_dir)
    tls_dir = Path(args.tls_dir)
    existing = {} if args.force else _read_env(env_path)

    values = {
        "PROFITOPS_POSTGRES_USER": existing.get("PROFITOPS_POSTGRES_USER") or "profitops",
        "PROFITOPS_POSTGRES_DB": existing.get("PROFITOPS_POSTGRES_DB") or "profitops",
        "PROFITOPS_POSTGRES_PASSWORD": existing.get("PROFITOPS_POSTGRES_PASSWORD") or _secret(),
        "PROFITOPS_AUTH_SECRET": existing.get("PROFITOPS_AUTH_SECRET") or _secret(),
        "PROFITOPS_ANALYST_TOKEN": existing.get("PROFITOPS_ANALYST_TOKEN") or _secret(),
        "PROFITOPS_APPROVER_TOKEN": existing.get("PROFITOPS_APPROVER_TOKEN") or _secret(),
        "PROFITOPS_DEMO_ANALYST_PASSWORD": existing.get("PROFITOPS_DEMO_ANALYST_PASSWORD") or _secret(),
        "PROFITOPS_DEMO_APPROVER_PASSWORD": existing.get("PROFITOPS_DEMO_APPROVER_PASSWORD") or _secret(),
        "PROFITOPS_ACCESS_USER": args.access_user or existing.get("PROFITOPS_ACCESS_USER") or "profitops",
        "PROFITOPS_ACCESS_PASSWORD": args.access_password or existing.get("PROFITOPS_ACCESS_PASSWORD") or _secret(),
    }
    if values["PROFITOPS_ANALYST_TOKEN"] == values["PROFITOPS_APPROVER_TOKEN"]:
        values["PROFITOPS_APPROVER_TOKEN"] = _secret()

    lines = [
        "# Generated by scripts/cloud_prepare.py. Do not commit this file.",
        "# Docker Compose: --env-file .env.cloud",
        *[f"{key}={values[key]}" for key in DEFAULT_KEYS],
        "PROFITOPS_DOMAIN=" + args.domain,
        "PROFITOPS_HTTPS_BIND=" + args.https_bind,
        "PROFITOPS_HTTPS_PORT=" + args.https_port,
        "PROFITOPS_TLS_DIR=" + _mount_path(tls_dir),
        "PROFITOPS_AUTH_DIR=" + _mount_path(auth_dir),
        "LLM_PROVIDER=disabled",
        "LLM_API_KEY=",
        "",
    ]
    _write_private(env_path, "\n".join(lines))

    auth_dir.mkdir(parents=True, exist_ok=True)
    _write_private(
        auth_dir / "htpasswd",
        _htpasswd_line(values["PROFITOPS_ACCESS_USER"], values["PROFITOPS_ACCESS_PASSWORD"]),
    )

    if args.tls_mode == "self-signed":
        _make_cert(tls_dir, args.domain, args.force)
    else:
        missing = [name for name in ("fullchain.pem", "privkey.pem") if not (tls_dir / name).exists()]
        if missing:
            raise SystemExit(f"--tls-mode existing requires {', '.join(missing)} in {tls_dir}")

    print(f"env file:       {env_path}")
    print(f"htpasswd:       {auth_dir / 'htpasswd'}")
    print(f"tls directory:  {tls_dir}")
    print(f"access user:    {values['PROFITOPS_ACCESS_USER']}")
    print(f"access password:{values['PROFITOPS_ACCESS_PASSWORD']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
