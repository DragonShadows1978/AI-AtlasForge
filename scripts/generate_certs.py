#!/usr/bin/env python3
"""
SSL Certificate Generator for AtlasForge Dashboard

Generates self-signed certificates for HTTPS support.
Certificates are valid for localhost and local network access.

Usage:
    python scripts/generate_certs.py

Output:
    certs/cert.pem - SSL certificate
    certs/key.pem  - Private key (do not share!)
"""

import subprocess
import sys
from pathlib import Path


def generate_certificates(output_dir: Path, days: int = 365) -> bool:
    """
    Generate self-signed SSL certificates using OpenSSL.

    Args:
        output_dir: Directory to store certificates
        days: Certificate validity in days

    Returns:
        True if successful, False otherwise
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    cert_path = output_dir / "cert.pem"
    key_path = output_dir / "key.pem"

    # OpenSSL configuration for Subject Alternative Names
    # Covers: localhost, local IPs, and wildcard local domains
    san_config = """
[req]
default_bits = 4096
distinguished_name = req_distinguished_name
x509_extensions = v3_req
prompt = no

[req_distinguished_name]
CN = AtlasForge Dashboard

[v3_req]
basicConstraints = CA:FALSE
keyUsage = nonRepudiation, digitalSignature, keyEncipherment
subjectAltName = @alt_names

[alt_names]
DNS.1 = localhost
DNS.2 = *.local
DNS.3 = *.lan
DNS.4 = *.home
IP.1 = 127.0.0.1
IP.2 = ::1
"""

    # Write temporary config file
    config_path = output_dir / "openssl.cnf"
    config_path.write_text(san_config)

    try:
        # Generate certificate and key in one command
        cmd = [
            "openssl", "req",
            "-x509",
            "-newkey", "rsa:4096",
            "-nodes",  # No passphrase
            "-keyout", str(key_path),
            "-out", str(cert_path),
            "-days", str(days),
            "-config", str(config_path)
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60
        )

        if result.returncode != 0:
            print(f"OpenSSL error: {result.stderr}")
            return False

        # Verify files were created
        if not cert_path.exists() or not key_path.exists():
            print("Certificate files not created")
            return False

        # Set restrictive permissions on private key
        key_path.chmod(0o600)

        print(f"Certificate generated: {cert_path}")
        print(f"Private key generated: {key_path}")
        print(f"Valid for: {days} days")
        print()
        print("To trust the certificate in Chrome:")
        print("  1. Open https://localhost:5010")
        print("  2. Click 'Advanced' -> 'Proceed to localhost (unsafe)'")
        print("  3. Or import cert.pem into your browser's trusted certificates")

        return True

    except subprocess.TimeoutExpired:
        print("OpenSSL command timed out")
        return False
    except FileNotFoundError:
        print("OpenSSL not found. Install it with: sudo apt install openssl")
        return False
    except Exception as e:
        print(f"Error generating certificates: {e}")
        return False
    finally:
        # Clean up config file
        if config_path.exists():
            config_path.unlink()


def main():
    # Determine output directory relative to script location
    script_dir = Path(__file__).parent.resolve()
    base_dir = script_dir.parent  # AI-AtlasForge root
    certs_dir = base_dir / "certs"

    print("=" * 50)
    print("AtlasForge SSL Certificate Generator")
    print("=" * 50)
    print()

    # Check if certificates already exist
    cert_path = certs_dir / "cert.pem"
    key_path = certs_dir / "key.pem"

    if cert_path.exists() and key_path.exists():
        response = input("Certificates already exist. Regenerate? [y/N]: ")
        if response.lower() != 'y':
            print("Keeping existing certificates")
            return 0

    success = generate_certificates(certs_dir)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
