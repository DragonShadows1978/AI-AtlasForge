#!/usr/bin/env python3
"""
Vulnerability Scanner for Bug Bounty Task
Scans assigned files and records findings to shared database
"""

import sys
import re
from pathlib import Path
from datetime import datetime

# Add workspace to path
sys.path.insert(0, '/home/vader/mini-mind-v2/workspace/bug_bounty')

from memory_db import get_memory_db, BugFinding, FindingStatus

# Configuration
MISSION_ID = 'BB-20251210-005335-d7c830'
TASK_ID = 'TASK-20251210005335-00068271'
DB_PATH = Path('/home/vader/mini-mind-v2/workspace/bug_bounty/memory.db')

# Files to scan
TARGET_FILES = [
    '/home/vader/mini-mind-v2/workspace/bug_bounty/test_target/api.py',
    '/home/vader/mini-mind-v2/workspace/bug_bounty/test_target/crypto.py',
    '/home/vader/mini-mind-v2/workspace/bug_bounty/test_target/config.py',
    '/home/vader/mini-mind-v2/workspace/bug_bounty/test_target/app.py',
    '/home/vader/mini-mind-v2/workspace/bug_bounty/test_target/auth.py',
]

class VulnerabilityScanner:
    def __init__(self):
        self.db = get_memory_db(DB_PATH)
        self.findings = []
        self.angles_completed = []

    def scan_all_files(self):
        """Scan all target files for vulnerabilities."""
        for filepath in TARGET_FILES:
            if Path(filepath).exists():
                with open(filepath, 'r') as f:
                    lines = f.readlines()
                    self.scan_file(filepath, lines)

        return self.findings

    def add_finding(self, file_path, line_number, category_name, severity, confidence,
                   code_snippet, description, category_id='A03:2025'):
        """Add a finding to the list and database."""
        finding = BugFinding(
            mission_id=MISSION_ID,
            file_path=file_path,
            line_number=line_number,
            category_id=category_id,
            category_name=category_name,
            severity=severity,
            confidence=confidence,
            code_snippet=code_snippet,
            description=description,
            discovered_by_agent=TASK_ID,
            discovered_at=datetime.now()
        )

        finding_id, is_new = self.db.add_finding(finding)

        self.findings.append({
            'file': file_path,
            'line': line_number,
            'category': category_name,
            'severity': severity,
            'confidence': confidence,
            'snippet': code_snippet[:100],
            'description': description
        })

    def scan_file(self, filepath, lines):
        """Scan a single file for all vulnerability types."""
        content = ''.join(lines)

        # SQL Injection patterns
        self.scan_sql_injection_fstring(filepath, lines)
        self.scan_sql_injection_concatenation(filepath, lines)
        self.scan_sql_injection_format(filepath, lines)

        # Command Injection
        self.scan_command_injection_system(filepath, lines)
        self.scan_command_injection_subprocess(filepath, lines)

        # Code Injection
        self.scan_code_injection_eval_exec(filepath, lines)

        # XSS and Template Injection
        self.scan_template_injection(filepath, lines)
        self.scan_reflected_xss(filepath, lines)

        # Credentials and Secrets
        self.scan_hardcoded_credentials(filepath, lines)
        self.scan_secrets_in_comments(filepath, lines)

        # JWT and Authentication
        self.scan_jwt_none_algorithm(filepath, lines)
        self.scan_missing_authentication(filepath, lines)

        # Weak Password Policy
        self.scan_weak_password_policy(filepath, lines)

        # Deserialization
        self.scan_insecure_pickle(filepath, lines)
        self.scan_unsafe_yaml(filepath, lines)

        # Path Traversal
        self.scan_path_traversal_open(filepath, lines)
        self.scan_path_traversal_sendfile(filepath, lines)

        # SSRF
        self.scan_ssrf_requests(filepath, lines)

        # Weak Crypto
        self.scan_weak_hash_md5(filepath, lines)
        self.scan_weak_hash_sha1(filepath, lines)
        self.scan_insecure_random(filepath, lines)

        # IDOR
        self.scan_idor(filepath, lines)

        # Debug and Info Disclosure
        self.scan_debug_mode(filepath, lines)
        self.scan_sensitive_data_logs(filepath, lines)

        # Flask specific
        self.scan_flask_secret_exposure(filepath, lines)
        self.scan_flask_ssti(filepath, lines)

        # API Keys in URLs
        self.scan_api_keys_in_urls(filepath, lines)

    def scan_sql_injection_fstring(self, filepath, lines):
        """Detect SQL injection via f-strings."""
        pattern = re.compile(r'f["\'].*SELECT.*FROM.*\{.*\}', re.IGNORECASE)

        for i, line in enumerate(lines, 1):
            if pattern.search(line) and 'cursor.execute' in ''.join(lines[max(0, i-2):min(len(lines), i+2)]):
                self.add_finding(
                    filepath, i, 'SQL Injection via F-String', 'critical', 0.95,
                    line.strip(),
                    'SQL query constructed using f-string with user-controlled data, allowing SQL injection',
                    'A03:2025'
                )
        self.angles_completed.append('SQL Injection via F-String')

    def scan_sql_injection_concatenation(self, filepath, lines):
        """Detect SQL injection via string concatenation."""
        for i, line in enumerate(lines, 1):
            if re.search(r'(SELECT|INSERT|UPDATE|DELETE).*\+.*["\']', line, re.IGNORECASE):
                if 'cursor.execute' in ''.join(lines[max(0, i-2):min(len(lines), i+3)]):
                    self.add_finding(
                        filepath, i, 'SQL Injection via String Concatenation', 'critical', 0.95,
                        line.strip(),
                        'SQL query built with string concatenation using potentially untrusted data',
                        'A03:2025'
                    )
        self.angles_completed.append('SQL Injection via String Concatenation')

    def scan_sql_injection_format(self, filepath, lines):
        """Detect SQL injection via .format()."""
        pattern = re.compile(r'["\'].*SELECT.*FROM.*["\']\.format\(', re.IGNORECASE)

        for i, line in enumerate(lines, 1):
            if pattern.search(line):
                self.add_finding(
                    filepath, i, 'SQL Injection via .format()', 'critical', 0.95,
                    line.strip(),
                    'SQL query constructed using .format() method with user input, enabling SQL injection',
                    'A03:2025'
                )
        self.angles_completed.append('SQL Injection via .format()')

    def scan_command_injection_system(self, filepath, lines):
        """Detect command injection via os.system."""
        for i, line in enumerate(lines, 1):
            if 'os.system' in line and ('{' in line or '+' in line or 'f"' in line or "f'" in line):
                self.add_finding(
                    filepath, i, 'Command Injection via os.system', 'critical', 0.95,
                    line.strip(),
                    'os.system() called with user-controlled data, allowing arbitrary command execution',
                    'A03:2025'
                )
        self.angles_completed.append('Command Injection via os.system')

    def scan_command_injection_subprocess(self, filepath, lines):
        """Detect command injection via subprocess with shell=True."""
        for i, line in enumerate(lines, 1):
            if 'subprocess' in line:
                # Check next few lines for shell=True
                context = ''.join(lines[max(0, i-1):min(len(lines), i+5)])
                if 'shell=True' in context and ('{' in line or 'f"' in line or "f'" in line):
                    self.add_finding(
                        filepath, i, 'Command Injection via subprocess', 'critical', 0.95,
                        line.strip(),
                        'subprocess called with shell=True and user input, enabling command injection',
                        'A03:2025'
                    )
        self.angles_completed.append('Command Injection via subprocess')

    def scan_code_injection_eval_exec(self, filepath, lines):
        """Detect code injection via eval/exec."""
        for i, line in enumerate(lines, 1):
            if re.search(r'\b(eval|exec)\s*\(', line):
                # Check if it uses request data
                context = ''.join(lines[max(0, i-3):min(len(lines), i+2)])
                if 'request.' in context or 'args.get' in context or 'form.get' in context:
                    self.add_finding(
                        filepath, i, 'Code Injection via eval/exec', 'critical', 0.95,
                        line.strip(),
                        'eval() or exec() called with user-controlled input, allowing arbitrary code execution',
                        'A03:2025'
                    )
        self.angles_completed.append('Code Injection via eval/exec')

    def scan_template_injection(self, filepath, lines):
        """Detect Server-Side Template Injection."""
        ssti_patterns = [
            r'render_template_string\(',
            r'Template\(.*\)\.render\(',
            r'Jinja2.*from_string'
        ]

        for i, line in enumerate(lines, 1):
            for pattern in ssti_patterns:
                if re.search(pattern, line):
                    context = ''.join(lines[max(0, i-2):min(len(lines), i+2)])
                    if 'request.' in context:
                        self.add_finding(
                            filepath, i, 'Server-Side Template Injection', 'critical', 0.90,
                            line.strip(),
                            'Template rendered with user-controlled input, enabling SSTI attacks',
                            'A03:2025'
                        )
        self.angles_completed.append('Server-Side Template Injection')

    def scan_reflected_xss(self, filepath, lines):
        """Detect reflected XSS vulnerabilities."""
        for i, line in enumerate(lines, 1):
            if 'request.' in line and ('return' in line or 'jsonify' in line):
                context = ''.join(lines[max(0, i-1):min(len(lines), i+2)])
                # Check if output is not escaped
                if 'escape' not in context and 'safe' not in context:
                    if re.search(r'request\.(args|form|json)\.get', line):
                        self.add_finding(
                            filepath, i, 'Reflected XSS in HTML', 'high', 0.75,
                            line.strip(),
                            'User input reflected in response without proper escaping, enabling XSS attacks',
                            'A03:2025'
                        )
        self.angles_completed.append('Reflected XSS in HTML')

    def scan_hardcoded_credentials(self, filepath, lines):
        """Detect hardcoded credentials and secrets."""
        patterns = [
            (r'PASSWORD\s*=\s*["\']([^"\']+)["\']', 'Password'),
            (r'API_KEY\s*=\s*["\']([^"\']+)["\']', 'API Key'),
            (r'SECRET\s*=\s*["\']([^"\']+)["\']', 'Secret Key'),
            (r'AWS_ACCESS_KEY_ID\s*=\s*["\']([^"\']+)["\']', 'AWS Access Key'),
            (r'AWS_SECRET_ACCESS_KEY\s*=\s*["\']([^"\']+)["\']', 'AWS Secret Key'),
            (r'STRIPE_.*KEY\s*=\s*["\']([^"\']+)["\']', 'Stripe Key'),
            (r'_SECRET\s*=\s*["\']([^"\']+)["\']', 'Secret'),
            (r'TOKEN\s*=\s*["\']([^"\']+)["\']', 'Token'),
        ]

        for i, line in enumerate(lines, 1):
            for pattern, cred_type in patterns:
                match = re.search(pattern, line, re.IGNORECASE)
                if match:
                    value = match.group(1)
                    # Skip obvious placeholders
                    if len(value) > 5 and value not in ['your_key_here', 'change_me']:
                        self.add_finding(
                            filepath, i, 'Hardcoded Credentials', 'high', 0.90,
                            line.strip(),
                            f'Hardcoded {cred_type} found in source code: {value[:20]}...',
                            'A07:2025'
                        )
        self.angles_completed.append('Hardcoded Credentials')

    def scan_secrets_in_comments(self, filepath, lines):
        """Detect sensitive data in comments."""
        for i, line in enumerate(lines, 1):
            if line.strip().startswith('#'):
                if re.search(r'(password|key|secret|token|credential).*[:=]', line, re.IGNORECASE):
                    # Look for patterns like "password: value" or "KEY=value"
                    if re.search(r'[:=]\s*["\']?[a-zA-Z0-9_\-]{8,}', line, re.IGNORECASE):
                        self.add_finding(
                            filepath, i, 'Sensitive Data in Logs/Comments', 'medium', 0.70,
                            line.strip(),
                            'Sensitive credential information found in code comments',
                            'A07:2025'
                        )
        self.angles_completed.append('Sensitive Data in Logs/Comments')

    def scan_jwt_none_algorithm(self, filepath, lines):
        """Detect JWT None algorithm vulnerability."""
        for i, line in enumerate(lines, 1):
            if 'alg' in line and 'none' in line.lower():
                context = ''.join(lines[max(0, i-5):min(len(lines), i+5)])
                if 'jwt' in context.lower() or 'token' in context.lower():
                    self.add_finding(
                        filepath, i, 'JWT None Algorithm Vulnerability', 'critical', 0.90,
                        line.strip(),
                        'JWT implementation accepts "none" algorithm, allowing unsigned token forgery',
                        'A02:2025'
                    )
        self.angles_completed.append('JWT None Algorithm Vulnerability')

    def scan_missing_authentication(self, filepath, lines):
        """Detect endpoints missing authentication checks."""
        for i, line in enumerate(lines, 1):
            if '@app.route' in line or '@api.route' in line:
                # Check if admin/sensitive endpoint
                if re.search(r'["\']/(api/)?admin', line, re.IGNORECASE):
                    # Look ahead for authentication decorator
                    context = ''.join(lines[max(0, i-3):min(len(lines), i+10)])
                    if not re.search(r'@(login_required|auth_required|require_auth)', context):
                        self.add_finding(
                            filepath, i, 'Missing Authentication Check', 'high', 0.85,
                            line.strip(),
                            'Admin/sensitive endpoint missing authentication decorator or check',
                            'A01:2025'
                        )
        self.angles_completed.append('Missing Authentication Check')

    def scan_weak_password_policy(self, filepath, lines):
        """Detect weak password validation."""
        for i, line in enumerate(lines, 1):
            # Check for weak minimum password length
            if 'min_password_length' in line.lower() or 'password.*length' in line.lower():
                if re.search(r'[:=]\s*[0-7]', line):
                    self.add_finding(
                        filepath, i, 'Weak Password Policy', 'medium', 0.85,
                        line.strip(),
                        'Minimum password length is too short (< 8 characters)',
                        'A07:2025'
                    )
        self.angles_completed.append('Weak Password Policy')

    def scan_insecure_pickle(self, filepath, lines):
        """Detect insecure pickle deserialization."""
        for i, line in enumerate(lines, 1):
            if 'pickle.loads' in line or 'pickle.load(' in line:
                context = ''.join(lines[max(0, i-3):min(len(lines), i+2)])
                if 'request.' in context:
                    self.add_finding(
                        filepath, i, 'Insecure Pickle Deserialization', 'critical', 0.95,
                        line.strip(),
                        'pickle.loads() used on untrusted data, enabling arbitrary code execution',
                        'A08:2025'
                    )
        self.angles_completed.append('Insecure Pickle Deserialization')

    def scan_unsafe_yaml(self, filepath, lines):
        """Detect unsafe YAML deserialization."""
        for i, line in enumerate(lines, 1):
            if 'yaml.load(' in line and 'Loader' not in line:
                self.add_finding(
                    filepath, i, 'Unsafe YAML Deserialization', 'critical', 0.95,
                    line.strip(),
                    'yaml.load() without SafeLoader allows arbitrary code execution',
                    'A08:2025'
                )
        self.angles_completed.append('Unsafe YAML Deserialization')

    def scan_path_traversal_open(self, filepath, lines):
        """Detect path traversal via open()."""
        for i, line in enumerate(lines, 1):
            if 'open(' in line:
                context = ''.join(lines[max(0, i-3):min(len(lines), i+1)])
                if 'request.' in context and 'realpath' not in context and 'abspath' not in context:
                    # Check if filename comes from user input
                    if re.search(r'request\.(args|form|json)\.get', context):
                        self.add_finding(
                            filepath, i, 'Path Traversal via open()', 'high', 0.85,
                            line.strip(),
                            'File path from user input used in open() without sanitization',
                            'A01:2025'
                        )
        self.angles_completed.append('Path Traversal via open()')

    def scan_path_traversal_sendfile(self, filepath, lines):
        """Detect path traversal in send_file."""
        for i, line in enumerate(lines, 1):
            if 'send_file' in line:
                context = ''.join(lines[max(0, i-5):min(len(lines), i+1)])
                if 'request.' in context or 'filename' in context:
                    if 'safe_join' not in context and 'realpath' not in context:
                        self.add_finding(
                            filepath, i, 'Path Traversal via send_file', 'high', 0.85,
                            line.strip(),
                            'send_file() used with user-controlled path without proper validation',
                            'A01:2025'
                        )
        self.angles_completed.append('Path Traversal via send_file')

    def scan_ssrf_requests(self, filepath, lines):
        """Detect SSRF via requests library."""
        ssrf_funcs = ['urllib.request.urlopen', 'requests.get', 'requests.post',
                      'urllib.request.Request', 'httplib']

        for i, line in enumerate(lines, 1):
            for func in ssrf_funcs:
                if func in line:
                    context = ''.join(lines[max(0, i-3):min(len(lines), i+1)])
                    if 'request.' in context or 'args.get' in context:
                        self.add_finding(
                            filepath, i, 'SSRF via requests library', 'high', 0.85,
                            line.strip(),
                            'HTTP request made to user-controlled URL without validation (SSRF)',
                            'A10:2025'
                        )
        self.angles_completed.append('SSRF via requests library')

    def scan_weak_hash_md5(self, filepath, lines):
        """Detect MD5 usage for security purposes."""
        for i, line in enumerate(lines, 1):
            if 'hashlib.md5' in line:
                context = ''.join(lines[max(0, i-5):min(len(lines), i+5)])
                if any(word in context.lower() for word in ['password', 'secret', 'token', 'auth']):
                    self.add_finding(
                        filepath, i, 'Weak Hash Algorithm (MD5)', 'high', 0.90,
                        line.strip(),
                        'MD5 used for security-sensitive hashing (passwords/tokens) - cryptographically broken',
                        'A02:2025'
                    )
        self.angles_completed.append('Weak Hash Algorithm (MD5)')

    def scan_weak_hash_sha1(self, filepath, lines):
        """Detect SHA1 usage for security purposes."""
        for i, line in enumerate(lines, 1):
            if 'hashlib.sha1' in line:
                context = ''.join(lines[max(0, i-5):min(len(lines), i+5)])
                if any(word in context.lower() for word in ['password', 'secret', 'token', 'auth']):
                    self.add_finding(
                        filepath, i, 'Weak Hash Algorithm (SHA1)', 'high', 0.90,
                        line.strip(),
                        'SHA1 used for security-sensitive hashing - considered weak for passwords',
                        'A02:2025'
                    )
        self.angles_completed.append('Weak Hash Algorithm (SHA1)')

    def scan_insecure_random(self, filepath, lines):
        """Detect non-cryptographic random for security purposes."""
        for i, line in enumerate(lines, 1):
            if re.search(r'\brandom\.(choice|randint|random)\(', line):
                context = ''.join(lines[max(0, i-5):min(len(lines), i+5)])
                if any(word in context.lower() for word in ['token', 'password', 'secret', 'key', 'session']):
                    self.add_finding(
                        filepath, i, 'Insecure Random Number Generation', 'high', 0.85,
                        line.strip(),
                        'Non-cryptographic random used for security tokens/passwords (use secrets module)',
                        'A02:2025'
                    )
        self.angles_completed.append('Insecure Random Number Generation')

    def scan_idor(self, filepath, lines):
        """Detect potential IDOR vulnerabilities."""
        for i, line in enumerate(lines, 1):
            if '@app.route' in line or '@api.route' in line:
                if '<int:user_id>' in line or '<user_id>' in line or '<int:id>' in line:
                    # Check for authorization in next 15 lines
                    context = ''.join(lines[i:min(len(lines), i+15)])
                    if 'SELECT' in context.upper():
                        # Check if there's authorization check
                        if not any(check in context.lower() for check in
                                 ['current_user', 'authorize', 'check_permission', 'verify_owner']):
                            self.add_finding(
                                filepath, i, 'Insecure Direct Object Reference', 'high', 0.75,
                                line.strip(),
                                'Endpoint accesses user data by ID without authorization check (IDOR)',
                                'A01:2025'
                            )
        self.angles_completed.append('Insecure Direct Object Reference')

    def scan_debug_mode(self, filepath, lines):
        """Detect debug mode enabled."""
        for i, line in enumerate(lines, 1):
            if re.search(r'DEBUG\s*=\s*True', line, re.IGNORECASE):
                self.add_finding(
                    filepath, i, 'Debug Mode Enabled', 'medium', 0.90,
                    line.strip(),
                    'Debug mode enabled in configuration - exposes sensitive information',
                    'A05:2025'
                )
            if 'app.run' in line and 'debug=True' in line:
                self.add_finding(
                    filepath, i, 'Flask Debug Mode Enabled', 'high', 0.95,
                    line.strip(),
                    'Flask debug mode enabled - allows code execution via debug console',
                    'A05:2025'
                )
        self.angles_completed.append('Debug Mode Enabled')
        self.angles_completed.append('Flask Debug Mode Enabled')

    def scan_sensitive_data_logs(self, filepath, lines):
        """Detect sensitive data in logging."""
        for i, line in enumerate(lines, 1):
            if 'logger.' in line or 'logging.' in line or 'log.' in line:
                if any(word in line.lower() for word in ['password', 'secret', 'token', 'key', 'authorization']):
                    self.add_finding(
                        filepath, i, 'Sensitive Data in Logs/Comments', 'medium', 0.80,
                        line.strip(),
                        'Logging statement may include sensitive data like passwords or tokens',
                        'A09:2025'
                    )
        # Already added to angles_completed in scan_secrets_in_comments

    def scan_flask_secret_exposure(self, filepath, lines):
        """Detect exposed Flask secret keys."""
        for i, line in enumerate(lines, 1):
            if 'SECRET_KEY' in line and '=' in line:
                if re.search(r'["\'][a-zA-Z0-9_\-]{10,}["\']', line):
                    self.add_finding(
                        filepath, i, 'Flask Secret Key Exposure', 'high', 0.90,
                        line.strip(),
                        'Flask SECRET_KEY hardcoded in source code - should use environment variable',
                        'A02:2025'
                    )
        self.angles_completed.append('Flask Secret Key Exposure')

    def scan_flask_ssti(self, filepath, lines):
        """Detect Flask SSTI vulnerabilities."""
        # Covered in scan_template_injection
        self.angles_completed.append('Flask Server-Side Template Injection')

    def scan_api_keys_in_urls(self, filepath, lines):
        """Detect API keys passed in URL parameters."""
        for i, line in enumerate(lines, 1):
            if 'request.args.get' in line:
                if re.search(r'["\']token["\']|["\']api_key["\']|["\']key["\']', line, re.IGNORECASE):
                    self.add_finding(
                        filepath, i, 'API Keys in URL Parameters', 'medium', 0.80,
                        line.strip(),
                        'API key or token accepted via URL parameter - should use headers instead',
                        'A07:2025'
                    )
        self.angles_completed.append('API Keys in URL Parameters')

def main():
    scanner = VulnerabilityScanner()

    print(f"[*] Starting vulnerability scan for mission {MISSION_ID}")
    print(f"[*] Task ID: {TASK_ID}")
    print(f"[*] Scanning {len(TARGET_FILES)} files...")

    findings = scanner.scan_all_files()

    # Deduplicate angles
    angles = list(set(scanner.angles_completed))

    print(f"\n[+] Scan complete!")
    print(f"[+] Files scanned: {len(TARGET_FILES)}")
    print(f"[+] Findings: {len(findings)}")
    print(f"[+] Methodologies applied: {len(angles)}")

    # Output JSON result
    result = {
        'status': 'completed',
        'task_id': TASK_ID,
        'mission_id': MISSION_ID,
        'files_scanned': TARGET_FILES,
        'findings_count': len(findings),
        'findings': findings,
        'angles_completed': angles,
        'summary': f'Scanned {len(TARGET_FILES)} files and found {len(findings)} security vulnerabilities across {len(angles)} different vulnerability categories',
        'errors': []
    }

    import json
    print("\n" + "="*80)
    print("SCAN RESULTS (JSON)")
    print("="*80)
    print(json.dumps(result, indent=2))

    return result

if __name__ == '__main__':
    main()
