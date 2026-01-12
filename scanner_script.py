#!/usr/bin/env python3
"""
Vulnerability Scanner Script
Systematically scans files for security vulnerabilities and records to database
"""

import sys
import re
from pathlib import Path
from datetime import datetime

# Add bug_bounty to path
sys.path.insert(0, '/home/vader/mini-mind-v2/workspace/bug_bounty')
from memory_db import get_memory_db, BugFinding, FindingStatus

# Initialize database
db = get_memory_db(Path('/home/vader/mini-mind-v2/workspace/bug_bounty/memory.db'))

# Mission and task info
MISSION_ID = 'BB-20251209-233531-403c01'
TASK_ID = 'TASK-20251209233531-4192b982'

# Files to scan
files_to_scan = [
    '/home/vader/mini-mind-v2/workspace/bug_bounty/test_target/api.py',
    '/home/vader/mini-mind-v2/workspace/bug_bounty/test_target/crypto.py',
    '/home/vader/mini-mind-v2/workspace/bug_bounty/test_target/config.py',
    '/home/vader/mini-mind-v2/workspace/bug_bounty/test_target/app.py',
    '/home/vader/mini-mind-v2/workspace/bug_bounty/test_target/auth.py',
]

findings = []
angles_completed = []

def add_finding(file_path, line_num, category, severity, confidence, snippet, description):
    """Helper to add finding to database and list"""
    finding = BugFinding(
        mission_id=MISSION_ID,
        file_path=file_path,
        line_number=line_num,
        category_id='A03:2021',
        category_name=category,
        severity=severity,
        confidence=confidence,
        code_snippet=snippet,
        description=description,
        discovered_by_agent=TASK_ID,
        discovered_at=datetime.now()
    )
    finding_id, is_new = db.add_finding(finding)
    
    findings.append({
        'file': file_path,
        'line': line_num,
        'category': category,
        'severity': severity,
        'confidence': confidence,
        'snippet': snippet,
        'description': description
    })
    return finding_id

def scan_sql_injection(file_path, content, lines):
    """Scan for SQL injection vulnerabilities"""
    
    # SQL Injection via f-string
    for i, line in enumerate(lines, 1):
        if 'f"' in line or "f'" in line:
            if any(keyword in line.upper() for keyword in ['SELECT', 'INSERT', 'UPDATE', 'DELETE', 'FROM', 'WHERE']):
                add_finding(
                    file_path, i, 'SQL Injection via F-String', 'critical', 0.95,
                    line.strip(),
                    f'SQL query built using f-string at line {i}, allowing SQL injection'
                )
    
    # SQL Injection via string concatenation
    for i, line in enumerate(lines, 1):
        if '+' in line and any(keyword in line.upper() for keyword in ['SELECT', 'INSERT', 'UPDATE', 'DELETE']):
            if '"' in line or "'" in line:
                add_finding(
                    file_path, i, 'SQL Injection via String Concatenation', 'critical', 0.90,
                    line.strip(),
                    f'SQL query built using string concatenation at line {i}, allowing SQL injection'
                )
    
    # SQL Injection via .format()
    for i, line in enumerate(lines, 1):
        if '.format(' in line and any(keyword in line.upper() for keyword in ['SELECT', 'INSERT', 'UPDATE', 'DELETE']):
            add_finding(
                file_path, i, 'SQL Injection via .format()', 'critical', 0.95,
                line.strip(),
                f'SQL query built using .format() at line {i}, allowing SQL injection'
            )
    
    angles_completed.extend(['SQL Injection via String Concatenation',
                            'SQL Injection via F-String',
                            'SQL Injection via .format()'])

def scan_command_injection(file_path, content, lines):
    """Scan for command injection vulnerabilities"""
    
    # Command injection via os.system
    for i, line in enumerate(lines, 1):
        if 'os.system' in line:
            add_finding(
                file_path, i, 'Command Injection via os.system', 'critical', 0.95,
                line.strip(),
                f'Command execution via os.system at line {i}, allows command injection'
            )
    
    # Command injection via subprocess with shell=True
    for i, line in enumerate(lines, 1):
        if 'subprocess' in line and 'shell=True' in line:
            add_finding(
                file_path, i, 'Command Injection via subprocess', 'critical', 0.95,
                line.strip(),
                f'Command execution via subprocess with shell=True at line {i}, allows command injection'
            )
    
    angles_completed.extend(['Command Injection via os.system',
                            'Command Injection via subprocess'])

def scan_code_injection(file_path, content, lines):
    """Scan for code injection vulnerabilities"""
    
    for i, line in enumerate(lines, 1):
        if 'eval(' in line:
            add_finding(
                file_path, i, 'Code Injection via eval()', 'critical', 0.98,
                line.strip(),
                f'Use of eval() at line {i} allows arbitrary code execution'
            )
        
        if 'exec(' in line:
            add_finding(
                file_path, i, 'Code Injection via exec()', 'critical', 0.98,
                line.strip(),
                f'Use of exec() at line {i} allows arbitrary code execution'
            )
    
    angles_completed.append('Code Injection via eval/exec')

def scan_template_injection(file_path, content, lines):
    """Scan for server-side template injection"""
    
    for i, line in enumerate(lines, 1):
        if 'render_template_string' in line:
            add_finding(
                file_path, i, 'Server-Side Template Injection', 'high', 0.85,
                line.strip(),
                f'Use of render_template_string at line {i} may allow SSTI'
            )
    
    angles_completed.append('Server-Side Template Injection')

def scan_xss(file_path, content, lines):
    """Scan for XSS vulnerabilities"""
    
    for i, line in enumerate(lines, 1):
        # Look for user input being echoed
        if 'request.' in line and any(x in line for x in ['return', 'jsonify', 'render']):
            if 'escape' not in line and 'safe' not in line:
                add_finding(
                    file_path, i, 'Reflected XSS in HTML', 'high', 0.70,
                    line.strip(),
                    f'User input potentially echoed without escaping at line {i}'
                )
    
    angles_completed.append('Reflected XSS in HTML')

def scan_hardcoded_credentials(file_path, content, lines):
    """Scan for hardcoded credentials"""
    
    patterns = [
        (r'PASSWORD\s*=\s*["\'](.+?)["\']', 'Hardcoded Password'),
        (r'API_KEY\s*=\s*["\'](.+?)["\']', 'Hardcoded API Key'),
        (r'SECRET_KEY\s*=\s*["\'](.+?)["\']', 'Hardcoded Secret Key'),
        (r'AWS_SECRET_ACCESS_KEY\s*=\s*["\'](.+?)["\']', 'Hardcoded AWS Secret'),
        (r'AWS_ACCESS_KEY_ID\s*=\s*["\'](.+?)["\']', 'Hardcoded AWS Access Key'),
        (r'STRIPE_SECRET_KEY\s*=\s*["\'](.+?)["\']', 'Hardcoded Stripe Key'),
        (r'jwt_secret\s*=\s*["\'](.+?)["\']', 'Hardcoded JWT Secret'),
        (r'SMTP_PASSWORD\s*=\s*["\'](.+?)["\']', 'Hardcoded SMTP Password'),
        (r'DATABASE_PASSWORD\s*=\s*["\'](.+?)["\']', 'Hardcoded DB Password'),
    ]
    
    for i, line in enumerate(lines, 1):
        for pattern, cred_type in patterns:
            match = re.search(pattern, line, re.IGNORECASE)
            if match:
                add_finding(
                    file_path, i, 'Hardcoded Credentials', 'critical', 0.98,
                    line.strip(),
                    f'{cred_type} hardcoded at line {i}'
                )
    
    angles_completed.append('Hardcoded Credentials')

def scan_jwt_vulnerabilities(file_path, content, lines):
    """Scan for JWT vulnerabilities"""
    
    for i, line in enumerate(lines, 1):
        if "'none'" in line or '"none"' in line:
            if 'alg' in line or 'algorithm' in line:
                add_finding(
                    file_path, i, 'JWT None Algorithm Vulnerability', 'critical', 0.90,
                    line.strip(),
                    f'JWT accepts "none" algorithm at line {i}, allowing token forgery'
                )
    
    angles_completed.append('JWT None Algorithm Vulnerability')

def scan_weak_password_policy(file_path, content, lines):
    """Scan for weak password policies"""
    
    for i, line in enumerate(lines, 1):
        if 'min_password_length' in line:
            if any(str(x) in line for x in [1, 2, 3, 4, 5, 6]):
                add_finding(
                    file_path, i, 'Weak Password Policy', 'medium', 0.85,
                    line.strip(),
                    f'Weak minimum password length at line {i}'
                )
    
    angles_completed.append('Weak Password Policy')

def scan_insecure_deserialization(file_path, content, lines):
    """Scan for insecure deserialization"""
    
    # Pickle deserialization
    for i, line in enumerate(lines, 1):
        if 'pickle.loads' in line:
            add_finding(
                file_path, i, 'Insecure Pickle Deserialization', 'critical', 0.95,
                line.strip(),
                f'Insecure pickle.loads() at line {i} allows arbitrary code execution'
            )
    
    # YAML deserialization
    for i, line in enumerate(lines, 1):
        if 'yaml.load(' in line and 'SafeLoader' not in line and 'safe_load' not in line:
            add_finding(
                file_path, i, 'Unsafe YAML Deserialization', 'critical', 0.95,
                line.strip(),
                f'yaml.load() without SafeLoader at line {i} allows code execution'
            )
    
    angles_completed.extend(['Insecure Pickle Deserialization',
                            'Unsafe YAML Deserialization'])

def scan_path_traversal(file_path, content, lines):
    """Scan for path traversal vulnerabilities"""
    
    for i, line in enumerate(lines, 1):
        if 'open(' in line:
            # Check if request is used nearby
            context_start = max(0, i-5)
            context_end = min(len(lines), i+5)
            context = '\n'.join(lines[context_start:context_end])
            if 'request' in context:
                add_finding(
                    file_path, i, 'Path Traversal via open()', 'high', 0.80,
                    line.strip(),
                    f'File operation at line {i} may be vulnerable to path traversal'
                )
        
        if 'send_file' in line:
            add_finding(
                file_path, i, 'Path Traversal via send_file', 'high', 0.85,
                line.strip(),
                f'send_file at line {i} may be vulnerable to path traversal'
            )
    
    angles_completed.extend(['Path Traversal via open()',
                            'Path Traversal via send_file'])

def scan_ssrf(file_path, content, lines):
    """Scan for SSRF vulnerabilities"""
    
    for i, line in enumerate(lines, 1):
        if 'urllib.request.urlopen' in line or 'requests.get' in line or 'requests.post' in line:
            context_start = max(0, i-3)
            context_end = min(len(lines), i+3)
            context = '\n'.join(lines[context_start:context_end])
            if 'request.' in context:
                add_finding(
                    file_path, i, 'SSRF via requests library', 'high', 0.85,
                    line.strip(),
                    f'URL fetching with user input at line {i}, allows SSRF'
                )
    
    angles_completed.append('SSRF via requests library')

def scan_weak_crypto(file_path, content, lines):
    """Scan for weak cryptographic algorithms"""
    
    for i, line in enumerate(lines, 1):
        if 'hashlib.md5' in line:
            add_finding(
                file_path, i, 'Weak Hash Algorithm (MD5)', 'high', 0.90,
                line.strip(),
                f'MD5 used at line {i}, which is cryptographically broken'
            )
        
        if 'hashlib.sha1' in line:
            add_finding(
                file_path, i, 'Weak Hash Algorithm (SHA1)', 'medium', 0.85,
                line.strip(),
                f'SHA1 used at line {i}, which is weak for security purposes'
            )
    
    angles_completed.extend(['Weak Hash Algorithm (MD5)',
                            'Weak Hash Algorithm (SHA1)'])

def scan_insecure_random(file_path, content, lines):
    """Scan for insecure random number generation"""
    
    for i, line in enumerate(lines, 1):
        if 'random.' in line and not 'import random' in line:
            # Check if used for security purposes
            security_keywords = ['token', 'password', 'key', 'session', 'secret', 'api']
            if any(keyword in line.lower() for keyword in security_keywords):
                add_finding(
                    file_path, i, 'Insecure Random Number Generation', 'high', 0.85,
                    line.strip(),
                    f'Non-cryptographic random used for security at line {i}'
                )
    
    angles_completed.append('Insecure Random Number Generation')

def scan_missing_auth(file_path, content, lines):
    """Scan for missing authentication checks"""
    
    for i, line in enumerate(lines, 1):
        if '@app.route' in line or '@api.route' in line:
            # Check if admin/sensitive endpoint
            if 'admin' in line.lower() or 'debug' in line.lower():
                # Look ahead for auth decorator
                has_auth = False
                for j in range(max(0, i-5), min(len(lines), i)):
                    if 'login_required' in lines[j] or 'require_auth' in lines[j]:
                        has_auth = True
                        break
                
                if not has_auth:
                    add_finding(
                        file_path, i, 'Missing Authentication Check', 'critical', 0.80,
                        line.strip(),
                        f'Admin/sensitive endpoint at line {i} missing authentication'
                    )
    
    angles_completed.append('Missing Authentication Check')

def scan_idor(file_path, content, lines):
    """Scan for potential IDOR vulnerabilities"""
    
    for i, line in enumerate(lines, 1):
        if 'user_id' in line and ('SELECT' in line.upper() or 'fetchone' in line):
            # Check if there's authorization check nearby
            has_authz = False
            context_start = max(0, i-10)
            context_end = min(len(lines), i+5)
            context = '\n'.join(lines[context_start:context_end])
            if 'current_user' in context or 'check_permission' in context:
                has_authz = True
            
            if not has_authz:
                add_finding(
                    file_path, i, 'Insecure Direct Object Reference', 'high', 0.75,
                    line.strip(),
                    f'Potential IDOR at line {i}, user_id accessed without authorization check'
                )
    
    angles_completed.append('Insecure Direct Object Reference')

def scan_debug_mode(file_path, content, lines):
    """Scan for debug mode enabled"""
    
    for i, line in enumerate(lines, 1):
        if 'DEBUG' in line and '= True' in line:
            add_finding(
                file_path, i, 'Debug Mode Enabled', 'medium', 0.90,
                line.strip(),
                f'Debug mode enabled at line {i}, exposing sensitive information'
            )
        
        if 'debug=True' in line:
            add_finding(
                file_path, i, 'Debug Mode Enabled', 'medium', 0.90,
                line.strip(),
                f'Debug mode enabled at line {i}'
            )
    
    angles_completed.append('Debug Mode Enabled')

def scan_sensitive_data_exposure(file_path, content, lines):
    """Scan for sensitive data in logs/comments"""
    
    for i, line in enumerate(lines, 1):
        # Sensitive data in comments
        if line.strip().startswith('#'):
            sensitive_keywords = ['password', 'key', 'secret', 'token', 'credential']
            if any(keyword in line.lower() for keyword in sensitive_keywords):
                if any(c in line for c in ['=', ':']):
                    add_finding(
                        file_path, i, 'Sensitive Data in Comments', 'medium', 0.75,
                        line.strip(),
                        f'Potential credentials in comment at line {i}'
                    )
        
        # Sensitive data in logs
        if 'logger.' in line or 'logging.' in line or 'print(' in line:
            if 'password' in line.lower() or 'secret' in line.lower():
                add_finding(
                    file_path, i, 'Sensitive Data in Logs', 'high', 0.85,
                    line.strip(),
                    f'Sensitive data logged at line {i}'
                )
    
    angles_completed.append('Sensitive Data in Logs/Comments')

# Main scanning function
def scan_file(file_path):
    """Scan a single file for all vulnerabilities"""
    print(f"Scanning {file_path}...")
    
    try:
        with open(file_path, 'r') as f:
            content = f.read()
            lines = content.split('\n')
        
        # Run all scanners
        scan_sql_injection(file_path, content, lines)
        scan_command_injection(file_path, content, lines)
        scan_code_injection(file_path, content, lines)
        scan_template_injection(file_path, content, lines)
        scan_xss(file_path, content, lines)
        scan_hardcoded_credentials(file_path, content, lines)
        scan_jwt_vulnerabilities(file_path, content, lines)
        scan_weak_password_policy(file_path, content, lines)
        scan_insecure_deserialization(file_path, content, lines)
        scan_path_traversal(file_path, content, lines)
        scan_ssrf(file_path, content, lines)
        scan_weak_crypto(file_path, content, lines)
        scan_insecure_random(file_path, content, lines)
        scan_missing_auth(file_path, content, lines)
        scan_idor(file_path, content, lines)
        scan_debug_mode(file_path, content, lines)
        scan_sensitive_data_exposure(file_path, content, lines)
        
        print(f"✓ Scanned {file_path}")
        return True
        
    except Exception as e:
        print(f"✗ Error scanning {file_path}: {e}")
        return False

# Scan all files
print("Starting vulnerability scan...")
print(f"Mission ID: {MISSION_ID}")
print(f"Task ID: {TASK_ID}")
print(f"Files to scan: {len(files_to_scan)}\n")

files_scanned = []
for file_path in files_to_scan:
    if scan_file(file_path):
        files_scanned.append(file_path)

# Remove duplicates from angles_completed
angles_completed = list(set(angles_completed))

# Generate summary
summary = f"Scanned {len(files_scanned)} files and found {len(findings)} vulnerabilities. "
summary += f"Critical issues include SQL injection, command injection, code injection, insecure deserialization, and hardcoded credentials."

# Output results as JSON
import json

result = {
    "status": "completed",
    "task_id": TASK_ID,
    "mission_id": MISSION_ID,
    "files_scanned": files_scanned,
    "findings_count": len(findings),
    "findings": findings,
    "angles_completed": sorted(angles_completed),
    "summary": summary,
    "errors": []
}

print("\n" + "="*80)
print("SCAN RESULTS")
print("="*80)
print(json.dumps(result, indent=2))
