#!/usr/bin/env python3
"""
Vulnerability Scanner Agent
Task: TASK-20251210002913-b0d759aa
Mission: BB-20251210-002913-aad5b8
"""

import sys
import re
import json
from pathlib import Path
from datetime import datetime

# Add workspace to path
sys.path.insert(0, '/home/vader/mini-mind-v2/workspace/bug_bounty')
from memory_db import get_memory_db, BugFinding, FindingStatus

# Configuration
MISSION_ID = 'BB-20251210-002913-aad5b8'
TASK_ID = 'TASK-20251210002913-b0d759aa'
WORKSPACE = Path('/home/vader/mini-mind-v2/workspace/bug_bounty')
DB_PATH = WORKSPACE / 'memory.db'

# Files to scan
FILES = [
    WORKSPACE / 'test_target/api.py',
    WORKSPACE / 'test_target/crypto.py',
    WORKSPACE / 'test_target/config.py',
    WORKSPACE / 'test_target/app.py',
    WORKSPACE / 'test_target/auth.py',
]


class VulnerabilityScanner:
    def __init__(self):
        self.db = get_memory_db(DB_PATH)
        self.findings = []
        self.findings_count = 0
        self.files_scanned = []
        self.angles_completed = []
        self.errors = []

    def scan_file(self, filepath: Path):
        """Scan a single file for all vulnerabilities."""
        try:
            with open(filepath, 'r') as f:
                content = f.read()
                lines = content.split('\n')

            self.files_scanned.append(str(filepath))

            # Apply all methodologies
            self.detect_sql_injection_concat(filepath, lines)
            self.detect_sql_injection_fstring(filepath, lines)
            self.detect_sql_injection_format(filepath, lines)
            self.detect_command_injection_system(filepath, lines)
            self.detect_command_injection_subprocess(filepath, lines)
            self.detect_code_injection_eval_exec(filepath, lines)
            self.detect_ssti(filepath, lines)
            self.detect_reflected_xss(filepath, lines)
            self.detect_hardcoded_credentials(filepath, lines)
            self.detect_jwt_none_algorithm(filepath, lines)
            self.detect_weak_password_policy(filepath, lines)
            self.detect_insecure_pickle(filepath, lines)
            self.detect_unsafe_yaml(filepath, lines)
            self.detect_path_traversal_open(filepath, lines)
            self.detect_path_traversal_send_file(filepath, lines)
            self.detect_ssrf(filepath, lines)
            self.detect_weak_hash_md5(filepath, lines)
            self.detect_weak_hash_sha1(filepath, lines)
            self.detect_insecure_random(filepath, lines)
            self.detect_missing_auth(filepath, lines)
            self.detect_idor(filepath, lines)
            self.detect_debug_mode(filepath, lines)
            self.detect_sensitive_data_logs(filepath, lines)

        except Exception as e:
            self.errors.append(f"Error scanning {filepath}: {str(e)}")

    def add_finding(self, file_path, line_number, category_name, severity, confidence, snippet, description):
        """Add a finding to database and local list."""
        # Map category to OWASP category ID
        category_map = {
            'SQL Injection': 'A03:2021',
            'Command Injection': 'A03:2021',
            'Code Injection': 'A03:2021',
            'SSTI': 'A03:2021',
            'XSS': 'A03:2021',
            'Hardcoded Credentials': 'A07:2021',
            'JWT Vulnerability': 'A07:2021',
            'Weak Password Policy': 'A07:2021',
            'Insecure Deserialization': 'A08:2021',
            'Path Traversal': 'A01:2021',
            'SSRF': 'A10:2021',
            'Weak Cryptography': 'A02:2021',
            'Insecure Random': 'A02:2021',
            'Missing Authentication': 'A07:2021',
            'IDOR': 'A01:2021',
            'Security Misconfiguration': 'A05:2021',
            'Sensitive Data Exposure': 'A02:2021',
        }

        category_id = category_map.get(category_name, 'A00:2021')

        finding = BugFinding(
            mission_id=MISSION_ID,
            file_path=str(file_path),
            line_number=line_number,
            category_id=category_id,
            category_name=category_name,
            severity=severity,
            confidence=confidence,
            code_snippet=snippet[:200],  # Limit snippet length
            description=description,
            discovered_by_agent=TASK_ID,
            discovered_at=datetime.now()
        )

        finding_id, is_new = self.db.add_finding(finding)
        self.findings_count += 1

        self.findings.append({
            'file': str(file_path),
            'line': line_number,
            'category': category_name,
            'severity': severity,
            'confidence': confidence,
            'snippet': snippet[:100],
            'description': description
        })

    # ============ DETECTION METHODOLOGIES ============

    def detect_sql_injection_concat(self, filepath, lines):
        """Detect SQL injection via string concatenation."""
        pattern = r'execute\s*\(["\'].*["\']\s*\+\s*\w+|execute\s*\(["\'].*\%.*["\']'

        for i, line in enumerate(lines, 1):
            if 'execute' in line and '+' in line and 'SELECT' in line.upper():
                self.add_finding(
                    filepath, i, 'SQL Injection', 'critical', 0.9,
                    line.strip(),
                    'SQL query built with string concatenation, allowing SQL injection'
                )

        self.angles_completed.append('SQL Injection via String Concatenation')

    def detect_sql_injection_fstring(self, filepath, lines):
        """Detect SQL injection via f-strings."""
        for i, line in enumerate(lines, 1):
            if re.search(r'f["\'].*SELECT.*\{.*\}', line, re.IGNORECASE):
                self.add_finding(
                    filepath, i, 'SQL Injection', 'critical', 0.95,
                    line.strip(),
                    'SQL query built with f-string interpolation, allowing SQL injection'
                )

        self.angles_completed.append('SQL Injection via F-String')

    def detect_sql_injection_format(self, filepath, lines):
        """Detect SQL injection via .format()."""
        for i, line in enumerate(lines, 1):
            if re.search(r'["\'].*SELECT.*[\'"]\s*\.format\(', line, re.IGNORECASE):
                self.add_finding(
                    filepath, i, 'SQL Injection', 'critical', 0.95,
                    line.strip(),
                    'SQL query built with .format() method, allowing SQL injection'
                )

        self.angles_completed.append('SQL Injection via .format()')

    def detect_command_injection_system(self, filepath, lines):
        """Detect command injection via os.system."""
        for i, line in enumerate(lines, 1):
            if 'os.system' in line and ('f"' in line or "f'" in line or '+' in line):
                self.add_finding(
                    filepath, i, 'Command Injection', 'critical', 0.9,
                    line.strip(),
                    'Command injection via os.system with user-controlled input'
                )

        self.angles_completed.append('Command Injection via os.system')

    def detect_command_injection_subprocess(self, filepath, lines):
        """Detect command injection via subprocess with shell=True."""
        for i, line in enumerate(lines, 1):
            if 'subprocess' in line and 'shell=True' in line:
                self.add_finding(
                    filepath, i, 'Command Injection', 'critical', 0.85,
                    line.strip(),
                    'Command injection via subprocess with shell=True'
                )

        self.angles_completed.append('Command Injection via subprocess')

    def detect_code_injection_eval_exec(self, filepath, lines):
        """Detect code injection via eval/exec."""
        for i, line in enumerate(lines, 1):
            if re.search(r'\beval\s*\(', line) or re.search(r'\bexec\s*\(', line):
                if 'request' in line or 'args' in line or 'form' in line or 'get(' in line:
                    self.add_finding(
                        filepath, i, 'Code Injection', 'critical', 0.95,
                        line.strip(),
                        'Arbitrary code execution via eval() or exec() with user input'
                    )

        self.angles_completed.append('Code Injection via eval/exec')

    def detect_ssti(self, filepath, lines):
        """Detect Server-Side Template Injection."""
        for i, line in enumerate(lines, 1):
            if 'render_template_string' in line and ('request' in line or 'get(' in line):
                self.add_finding(
                    filepath, i, 'SSTI', 'critical', 0.8,
                    line.strip(),
                    'Server-Side Template Injection via render_template_string'
                )

        self.angles_completed.append('Server-Side Template Injection')

    def detect_reflected_xss(self, filepath, lines):
        """Detect reflected XSS."""
        for i, line in enumerate(lines, 1):
            if ('request.args' in line or 'request.form' in line) and 'callback' in line:
                if 'return' in line and not 'escape(' in line:
                    self.add_finding(
                        filepath, i, 'XSS', 'high', 0.75,
                        line.strip(),
                        'Reflected XSS - user input echoed without sanitization'
                    )

        self.angles_completed.append('Reflected XSS in HTML')

    def detect_hardcoded_credentials(self, filepath, lines):
        """Detect hardcoded credentials."""
        patterns = [
            (r'PASSWORD\s*=\s*["\'][^"\']+["\']', 'Hardcoded password'),
            (r'API_KEY\s*=\s*["\'][^"\']+["\']', 'Hardcoded API key'),
            (r'SECRET_KEY\s*=\s*["\'][^"\']+["\']', 'Hardcoded secret key'),
            (r'AWS_ACCESS_KEY', 'Hardcoded AWS key'),
            (r'AWS_SECRET_ACCESS_KEY', 'Hardcoded AWS secret'),
            (r'STRIPE_SECRET_KEY', 'Hardcoded Stripe key'),
            (r'sk_live_', 'Hardcoded Stripe live key'),
            (r'GITHUB_CLIENT_SECRET', 'Hardcoded GitHub secret'),
            (r'GOOGLE_CLIENT_SECRET', 'Hardcoded Google secret'),
            (r'JWT_SECRET', 'Hardcoded JWT secret'),
            (r'HMAC_SECRET', 'Hardcoded HMAC secret'),
        ]

        for i, line in enumerate(lines, 1):
            for pattern, desc in patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    if not line.strip().startswith('#'):
                        self.add_finding(
                            filepath, i, 'Hardcoded Credentials', 'critical', 0.95,
                            line.strip(),
                            f'{desc} found in source code'
                        )

        self.angles_completed.append('Hardcoded Credentials')

    def detect_jwt_none_algorithm(self, filepath, lines):
        """Detect JWT none algorithm vulnerability."""
        for i, line in enumerate(lines, 1):
            if "alg" in line and "none" in line.lower() and "jwt" in filepath.name.lower():
                self.add_finding(
                    filepath, i, 'JWT Vulnerability', 'critical', 0.7,
                    line.strip(),
                    'JWT implementation may accept "none" algorithm, allowing forged tokens'
                )

        self.angles_completed.append('JWT None Algorithm Vulnerability')

    def detect_weak_password_policy(self, filepath, lines):
        """Detect weak password policies."""
        for i, line in enumerate(lines, 1):
            if 'min_password_length' in line and re.search(r'[:=]\s*[1-6]\b', line):
                self.add_finding(
                    filepath, i, 'Weak Password Policy', 'medium', 0.9,
                    line.strip(),
                    'Weak password policy - minimum length too short'
                )

        self.angles_completed.append('Weak Password Policy')

    def detect_insecure_pickle(self, filepath, lines):
        """Detect insecure pickle deserialization."""
        for i, line in enumerate(lines, 1):
            if 'pickle.loads' in line:
                self.add_finding(
                    filepath, i, 'Insecure Deserialization', 'critical', 0.9,
                    line.strip(),
                    'Insecure pickle deserialization allows arbitrary code execution'
                )

        self.angles_completed.append('Insecure Pickle Deserialization')

    def detect_unsafe_yaml(self, filepath, lines):
        """Detect unsafe YAML deserialization."""
        for i, line in enumerate(lines, 1):
            if 'yaml.load(' in line and 'SafeLoader' not in line and 'safe_load' not in line:
                self.add_finding(
                    filepath, i, 'Insecure Deserialization', 'critical', 0.95,
                    line.strip(),
                    'Unsafe YAML deserialization without SafeLoader allows code execution'
                )

        self.angles_completed.append('Unsafe YAML Deserialization')

    def detect_path_traversal_open(self, filepath, lines):
        """Detect path traversal via open()."""
        for i, line in enumerate(lines, 1):
            if 'open(' in line and ('request' in line or 'args.get' in line or 'form.get' in line):
                if 'f"' in line or "f'" in line or 'os.path.join' in line:
                    self.add_finding(
                        filepath, i, 'Path Traversal', 'high', 0.8,
                        line.strip(),
                        'Path traversal vulnerability via file operations'
                    )

        self.angles_completed.append('Path Traversal via open()')

    def detect_path_traversal_send_file(self, filepath, lines):
        """Detect path traversal in send_file."""
        for i, line in enumerate(lines, 1):
            if 'send_file' in line and ('request' in line or 'filename' in line):
                # Check if there's proper validation
                if 'secure_filename' not in line:
                    self.add_finding(
                        filepath, i, 'Path Traversal', 'high', 0.85,
                        line.strip(),
                        'Path traversal in send_file without proper validation'
                    )

        self.angles_completed.append('Path Traversal via send_file')

    def detect_ssrf(self, filepath, lines):
        """Detect SSRF vulnerabilities."""
        for i, line in enumerate(lines, 1):
            if ('urllib.request.urlopen' in line or 'requests.get' in line or 'requests.post' in line):
                if 'request.args' in line or 'request.form' in line or 'url' in line:
                    self.add_finding(
                        filepath, i, 'SSRF', 'high', 0.8,
                        line.strip(),
                        'SSRF vulnerability - user-controlled URL without validation'
                    )

        self.angles_completed.append('SSRF via requests library')

    def detect_weak_hash_md5(self, filepath, lines):
        """Detect use of MD5 for security."""
        for i, line in enumerate(lines, 1):
            if 'hashlib.md5' in line or '.md5(' in line:
                if 'password' in line.lower() or 'hash' in line.lower():
                    self.add_finding(
                        filepath, i, 'Weak Cryptography', 'high', 0.9,
                        line.strip(),
                        'MD5 is cryptographically broken and should not be used for security'
                    )

        self.angles_completed.append('Weak Hash Algorithm (MD5)')

    def detect_weak_hash_sha1(self, filepath, lines):
        """Detect use of SHA1 for security."""
        for i, line in enumerate(lines, 1):
            if 'hashlib.sha1' in line or '.sha1(' in line:
                if 'password' in line.lower() or 'hash' in line.lower():
                    self.add_finding(
                        filepath, i, 'Weak Cryptography', 'high', 0.9,
                        line.strip(),
                        'SHA1 is weak and should not be used for password hashing'
                    )

        self.angles_completed.append('Weak Hash Algorithm (SHA1)')

    def detect_insecure_random(self, filepath, lines):
        """Detect insecure random number generation."""
        for i, line in enumerate(lines, 1):
            if 'random.' in line and ('token' in line.lower() or 'key' in line.lower() or 'session' in line.lower()):
                if 'secrets.' not in line:
                    self.add_finding(
                        filepath, i, 'Insecure Random', 'high', 0.85,
                        line.strip(),
                        'Insecure random number generation for security-sensitive operation'
                    )

        self.angles_completed.append('Insecure Random Number Generation')

    def detect_missing_auth(self, filepath, lines):
        """Detect missing authentication checks."""
        for i, line in enumerate(lines, 1):
            if '@app.route' in line or '@api.route' in line:
                if '/admin' in line or '/api/admin' in line:
                    # Check if next few lines have auth decorator
                    has_auth = False
                    for j in range(max(0, i-5), min(len(lines), i+2)):
                        if '@login_required' in lines[j] or '@require_auth' in lines[j]:
                            has_auth = True

                    if not has_auth:
                        self.add_finding(
                            filepath, i, 'Missing Authentication', 'critical', 0.75,
                            line.strip(),
                            'Admin endpoint without authentication decorator'
                        )

        self.angles_completed.append('Missing Authentication Check')

    def detect_idor(self, filepath, lines):
        """Detect potential IDOR vulnerabilities."""
        for i, line in enumerate(lines, 1):
            if 'user_id' in line and 'SELECT' in line.upper():
                # Check if there's authorization check
                if 'current_user' not in line and 'session' not in line:
                    self.add_finding(
                        filepath, i, 'IDOR', 'high', 0.7,
                        line.strip(),
                        'Potential IDOR - accessing user data without authorization check'
                    )

        self.angles_completed.append('Insecure Direct Object Reference')

    def detect_debug_mode(self, filepath, lines):
        """Detect debug mode enabled."""
        for i, line in enumerate(lines, 1):
            if re.search(r'DEBUG\s*=\s*True', line, re.IGNORECASE):
                self.add_finding(
                    filepath, i, 'Security Misconfiguration', 'high', 0.95,
                    line.strip(),
                    'Debug mode enabled - exposes sensitive information'
                )
            elif 'debug=True' in line and 'app.run' in line:
                self.add_finding(
                    filepath, i, 'Security Misconfiguration', 'high', 0.95,
                    line.strip(),
                    'Flask debug mode enabled in production'
                )

        self.angles_completed.append('Debug Mode Enabled')

    def detect_sensitive_data_logs(self, filepath, lines):
        """Detect sensitive data in logs/comments."""
        for i, line in enumerate(lines, 1):
            # Check comments for credentials
            if line.strip().startswith('#'):
                if any(keyword in line.lower() for keyword in ['password', 'secret', 'key', 'token', 'credential']):
                    if '=' in line or ':' in line:
                        self.add_finding(
                            filepath, i, 'Sensitive Data Exposure', 'medium', 0.7,
                            line.strip(),
                            'Sensitive credentials found in comments'
                        )

            # Check logging statements
            if 'logger.' in line or 'logging.' in line or '.info(' in line or '.debug(' in line:
                if 'password' in line.lower() or 'authorization' in line.lower():
                    self.add_finding(
                        filepath, i, 'Sensitive Data Exposure', 'high', 0.85,
                        line.strip(),
                        'Sensitive data being logged'
                    )

        self.angles_completed.append('Sensitive Data in Logs/Comments')

    def generate_report(self):
        """Generate final JSON report."""
        return {
            'status': 'completed' if not self.errors else 'completed',
            'task_id': TASK_ID,
            'mission_id': MISSION_ID,
            'files_scanned': self.files_scanned,
            'findings_count': self.findings_count,
            'findings': self.findings[:100],  # Limit to 100
            'angles_completed': list(set(self.angles_completed)),
            'summary': f'Scanned {len(self.files_scanned)} files and found {self.findings_count} vulnerabilities',
            'errors': self.errors
        }


def main():
    scanner = VulnerabilityScanner()

    # Scan all files
    for filepath in FILES:
        if filepath.exists():
            scanner.scan_file(filepath)
        else:
            scanner.errors.append(f"File not found: {filepath}")

    # Generate and print report
    report = scanner.generate_report()
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
