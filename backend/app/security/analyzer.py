"""Kavach AppSec Intelligence Engine: AST static analysis, secrets detection, and dependency scanner."""

import ast
import re
from dataclasses import dataclass, field
from typing import Any

# Patterns for hardcoded high-entropy secrets and credentials
SECRET_PATTERNS = [
    (r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token)\s*=\s*['\"][A-Za-z0-9_\-]{16,}['\"]", "CWE-798", "Hardcoded API Key / Secret Token"),
    (r"(?i)(password|passwd|pwd)\s*=\s*['\"][^'\"]{6,}['\"]", "CWE-259", "Hardcoded Plaintext Password"),
    (r"AIza[0-9A-Za-z\\-_]{35}", "CWE-798", "Exposed Google Cloud API Key"),
    (r"ghp_[0-9a-zA-Z]{36}", "CWE-798", "Exposed GitHub Personal Access Token"),
    (r"sk-[a-zA-Z0-9]{32,}", "CWE-798", "Exposed OpenAI / Cloud Secret Key"),
]

# Known vulnerable dependencies for offline checking (simulates OSV lookup)
KNOWN_VULNERABLE_PACKAGES = {
    "requests": [("<2.31.0", "CVE-2023-32681", "Leaked Proxy-Authorization header on redirect", "MEDIUM", "CWE-200")],
    "cryptography": [("<41.0.6", "CVE-2023-49083", "NULL-dereference when loading PKCS#7 certificates", "HIGH", "CWE-476")],
    "jinja2": [("<3.1.3", "CVE-2024-22195", "Cross-site scripting (XSS) via xmlattr filter", "MEDIUM", "CWE-79")],
    "urllib3": [("<2.0.7", "CVE-2023-45803", "Request body not stripped after HTTP 303 redirect", "MEDIUM", "CWE-200")],
    "flask": [("<2.2.5", "CVE-2023-30861", "Session cookie missing SameSite attribute", "HIGH", "CWE-1275")],
    "werkzeug": [("<2.3.8", "CVE-2023-46136", "High memory usage during multipart parsing leads to DoS", "HIGH", "CWE-400")],
}

SQL_KEYWORDS = ("SELECT ", "INSERT INTO ", "UPDATE ", "DELETE FROM ", "DROP TABLE ")


@dataclass
class VulnerabilityFinding:
    id: str
    category: str  # "SQL_INJECTION", "COMMAND_INJECTION", "SECRETS", "CRYPTO", "DEPENDENCY", "DESERIALIZATION"
    cwe: str
    severity: str  # "CRITICAL", "HIGH", "MEDIUM", "LOW"
    title: str
    description: str
    file_path: str = "inline_code.py"
    line_number: int | None = None
    snippet: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    remediation_hint: str = ""


class SecurityASTVisitor(ast.NodeVisitor):
    def __init__(self, filename: str = "input.py"):
        self.filename = filename
        self.findings: list[VulnerabilityFinding] = []
        self.tainted_sql_vars: set[str] = set()

    def visit_Assign(self, node: ast.Assign):
        # Taint tracking: detect SQL strings constructed with f-strings or string concatenation
        is_dynamic_sql = False
        if isinstance(node.value, ast.JoinedStr):
            for part in node.value.values:
                if isinstance(part, ast.Constant) and isinstance(part.value, str):
                    if any(kw in part.value.upper() for kw in SQL_KEYWORDS):
                        is_dynamic_sql = True
                        break
        elif isinstance(node.value, ast.BinOp):
            if isinstance(node.value.left, ast.Constant) and isinstance(node.value.left.value, str):
                if any(kw in node.value.left.value.upper() for kw in SQL_KEYWORDS):
                    is_dynamic_sql = True

        if is_dynamic_sql:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.tainted_sql_vars.add(target.id)
            self.findings.append(
                VulnerabilityFinding(
                    id=f"AST-SQLI-{len(self.findings)+1}",
                    category="SQL_INJECTION",
                    cwe="CWE-89",
                    severity="CRITICAL",
                    title="SQL Injection via Dynamic Query Construction",
                    description="Database query constructed using string interpolation or concatenation rather than parameterized query bindings.",
                    file_path=self.filename,
                    line_number=node.lineno,
                    remediation_hint="Use parameterized queries (e.g. `cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))`).",
                )
            )

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        func_name = ""
        module_name = ""
        if isinstance(node.func, ast.Name):
            func_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            func_name = node.func.attr
            if isinstance(node.func.value, ast.Name):
                module_name = node.func.value.id

        # 1. Command Injection
        # Flag os.system / os.popen directly
        if module_name == "os" and func_name in ("system", "popen"):
            self.findings.append(
                VulnerabilityFinding(
                    id=f"AST-CMD-{len(self.findings)+1}",
                    category="COMMAND_INJECTION",
                    cwe="CWE-78",
                    severity="CRITICAL",
                    title="Potential OS Command Injection",
                    description=f"Direct invocation of `{module_name}.{func_name}` allows shell command execution.",
                    file_path=self.filename,
                    line_number=node.lineno,
                    remediation_hint="Use `subprocess.run(..., shell=False)` with arguments passed as an explicit list of strings.",
                )
            )
        # Flag subprocess calls only if shell=True is passed
        elif module_name == "subprocess" and func_name in ("run", "call", "Popen"):
            is_shell = any(
                isinstance(kw, ast.keyword) and kw.arg == "shell" and getattr(kw.value, "value", False) is True
                for kw in node.keywords
            )
            if is_shell:
                self.findings.append(
                    VulnerabilityFinding(
                        id=f"AST-CMD-{len(self.findings)+1}",
                        category="COMMAND_INJECTION",
                        cwe="CWE-78",
                        severity="CRITICAL",
                        title="Potential OS Command Injection via shell=True",
                        description=f"Invocation of `subprocess.{func_name}` with `shell=True` exposes command execution to shell injection.",
                        file_path=self.filename,
                        line_number=node.lineno,
                        remediation_hint="Set `shell=False` and supply executable arguments as a list.",
                    )
                )

        # 2. Insecure Deserialization: pickle.loads, pickle.load
        if func_name in ("loads", "load") and module_name == "pickle":
            self.findings.append(
                VulnerabilityFinding(
                    id=f"AST-DESER-{len(self.findings)+1}",
                    category="DESERIALIZATION",
                    cwe="CWE-502",
                    severity="CRITICAL",
                    title="Insecure Deserialization via pickle",
                    description="`pickle.load/loads` can execute arbitrary bytecode during object reconstruction.",
                    file_path=self.filename,
                    line_number=node.lineno,
                    remediation_hint="Replace pickle with safe serialization formats like JSON, Protocol Buffers, or HMAC-signed envelopes.",
                )
            )

        # 3. Direct SQL execution with formatted strings or tainted variables
        if func_name in ("execute", "executemany") and node.args:
            first_arg = node.args[0]
            is_direct_format = isinstance(first_arg, (ast.JoinedStr, ast.BinOp))
            is_tainted_var = isinstance(first_arg, ast.Name) and first_arg.id in self.tainted_sql_vars
            if is_direct_format and not any(f.category == "SQL_INJECTION" and f.line_number == node.lineno for f in self.findings):
                self.findings.append(
                    VulnerabilityFinding(
                        id=f"AST-SQLI-{len(self.findings)+1}",
                        category="SQL_INJECTION",
                        cwe="CWE-89",
                        severity="CRITICAL",
                        title="SQL Injection via Dynamic Query Construction",
                        description="Database query constructed using string interpolation or concatenation rather than parameterized query bindings.",
                        file_path=self.filename,
                        line_number=node.lineno,
                        remediation_hint="Use parameterized queries (e.g. `cursor.execute('SELECT * FROM users WHERE id = ?', (user_id,))`).",
                    )
                )

        # 4. Insecure Cryptography: hashlib.md5, hashlib.sha1
        if func_name in ("md5", "sha1") and module_name == "hashlib":
            self.findings.append(
                VulnerabilityFinding(
                    id=f"AST-CRYPTO-{len(self.findings)+1}",
                    category="BROKEN_CRYPTO",
                    cwe="CWE-327",
                    severity="MEDIUM",
                    title=f"Use of Cryptographically Broken Hash Algorithm ({func_name.upper()})",
                    description=f"`hashlib.{func_name}` is vulnerable to collision attacks and must not be used for security-sensitive hashing or passwords.",
                    file_path=self.filename,
                    line_number=node.lineno,
                    remediation_hint="Use SHA-256 / SHA-3 for digests, or Argon2id / bcrypt for password hashing.",
                )
            )

        self.generic_visit(node)


def scan_code(source: str, filename: str = "source.py") -> list[VulnerabilityFinding]:
    """Run AST static analysis and regex secret detection over code."""
    findings: list[VulnerabilityFinding] = []

    # 1. Regex secrets scanning
    for idx, line in enumerate(source.splitlines(), start=1):
        for pattern, cwe, title in SECRET_PATTERNS:
            if re.search(pattern, line):
                findings.append(
                    VulnerabilityFinding(
                        id=f"SEC-{len(findings)+1}",
                        category="SECRETS",
                        cwe=cwe,
                        severity="HIGH",
                        title=title,
                        description="Hardcoded cryptographic key or credential found in source code.",
                        file_path=filename,
                        line_number=idx,
                        snippet=line.strip()[:80],
                        remediation_hint="Move secrets to environment variables or Google Cloud Secret Manager.",
                    )
                )

    # 2. Python AST scanning
    try:
        tree = ast.parse(source, filename=filename)
        visitor = SecurityASTVisitor(filename=filename)
        visitor.visit(tree)
        findings.extend(visitor.findings)
    except SyntaxError as e:
        findings.append(
            VulnerabilityFinding(
                id="AST-SYNTAX-ERR",
                category="SYNTAX",
                cwe="CWE-74",
                severity="LOW",
                title="Syntax Error Parsing Code",
                description=str(e),
                file_path=filename,
                line_number=e.lineno,
            )
        )

    return findings


def scan_dependencies(requirements_text: str) -> list[VulnerabilityFinding]:
    """Inspect requirements.txt dependencies against vulnerability database."""
    findings: list[VulnerabilityFinding] = []
    lines = requirements_text.splitlines()

    for line_no, line in enumerate(lines, start=1):
        clean = line.strip().split("#")[0].strip()
        if not clean:
            continue

        match = re.match(r"^([a-zA-Z0-9_\-]+)\s*(?:==|<=|<|>=|>)?\s*([0-9a-zA-Z\.\-]+)?", clean)
        if not match:
            continue

        pkg, version = match.group(1).lower(), match.group(2)
        if pkg in KNOWN_VULNERABLE_PACKAGES:
            for rule_ver, cve, desc, sev, cwe in KNOWN_VULNERABLE_PACKAGES[pkg]:
                findings.append(
                    VulnerabilityFinding(
                        id=f"DEP-{pkg.upper()}-{line_no}",
                        category="DEPENDENCY",
                        cwe=cwe,
                        severity=sev,
                        title=f"Vulnerable Dependency: {pkg} ({cve})",
                        description=f"Package {pkg} version {version or 'installed'} is affected: {desc}.",
                        file_path="requirements.txt",
                        line_number=line_no,
                        snippet=clean,
                        evidence={"package": pkg, "version": version, "cve": cve},
                        remediation_hint=f"Upgrade {pkg} to the latest patched release ({rule_ver.replace('<', '>=')}).",
                    )
                )
    return findings
