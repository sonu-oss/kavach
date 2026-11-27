"""Security knowledge base and compliance mapping dictionary (CWE, OWASP, ISO 27001, SOC 2, NIST CSF)."""

CWE_KNOWLEDGE = {
    "CWE-89": {
        "name": "Improper Neutralization of Special Elements used in an SQL Command ('SQL Injection')",
        "owasp": "A03:2021-Injection",
        "description": "The software constructs all or part of an SQL command using externally-influenced input from an upstream component, but it does not neutralize or incorrectly neutralizes special elements that could modify the intended SQL command when it is sent to a downstream database component.",
        "soc2_controls": ["CC6.1", "CC6.6", "CC6.8"],
        "iso27001_controls": ["A.8.28 Secure Coding", "A.8.8 Management of Technical Vulnerabilities"],
        "nist_csf": ["PR.DS-01", "PR.PS-02"],
        "remediation_best_practice": "Bind query parameters using prepared statements or object-relational mapping (ORM) parameter binding rather than string concatenation or string interpolation.",
    },
    "CWE-78": {
        "name": "Improper Neutralization of Special Elements used in an OS Command ('OS Command Injection')",
        "owasp": "A03:2021-Injection",
        "description": "The software constructs all or part of an OS command using externally-influenced input, but it does not neutralize or incorrectly neutralizes special elements that could modify the command execution environment.",
        "soc2_controls": ["CC6.1", "CC6.8"],
        "iso27001_controls": ["A.8.28 Secure Coding", "A.8.8 Management of Technical Vulnerabilities"],
        "nist_csf": ["PR.PS-02", "DE.CM-01"],
        "remediation_best_practice": "Avoid executing OS shell commands directly. If required, invoke subprocess functions with argument arrays (`shell=False`) and strict whitelist input validation.",
    },
    "CWE-502": {
        "name": "Deserialization of Untrusted Data",
        "owasp": "A08:2021-Software and Data Integrity Failures",
        "description": "The application deserializes untrusted data without sufficiently verifying that the resulting data will be valid.",
        "soc2_controls": ["CC6.6", "CC6.8"],
        "iso27001_controls": ["A.8.28 Secure Coding"],
        "nist_csf": ["PR.DS-01"],
        "remediation_best_practice": "Use safe, non-executable serialization formats such as JSON or Protocol Buffers. Never deserialize untrusted Python pickle objects.",
    },
    "CWE-798": {
        "name": "Use of Hard-coded Credentials",
        "owasp": "A07:2021-Identification and Authentication Failures",
        "description": "The software contains hard-coded credentials, such as a password or cryptographic key, which it uses for its own inbound authentication, outbound communication to external components, or encryption of internal data.",
        "soc2_controls": ["CC6.1", "CC6.2", "CC6.3"],
        "iso27001_controls": ["A.8.24 Use of Cryptography", "A.8.5 Secure Authentication"],
        "nist_csf": ["PR.AC-01", "PR.AC-07"],
        "remediation_best_practice": "Extract all secrets into environment variables, or retrieve them at runtime using Google Cloud Secret Manager / HashiCorp Vault.",
    },
    "CWE-327": {
        "name": "Use of a Broken or Risky Cryptographic Algorithm",
        "owasp": "A02:2021-Cryptographic Failures",
        "description": "The use of a broken or risky cryptographic algorithm is an unnecessary risk that may result in the exposure of sensitive information.",
        "soc2_controls": ["CC6.1", "CC6.7"],
        "iso27001_controls": ["A.8.24 Use of Cryptography"],
        "nist_csf": ["PR.DS-01", "PR.DS-02"],
        "remediation_best_practice": "Upgrade algorithms to modern NIST-approved standards: AES-256-GCM for symmetric encryption, Argon2id for password hashing, and SHA-256/SHA-3 for cryptographic hashing.",
    },
    "CWE-200": {
        "name": "Exposure of Sensitive Information to an Unauthorized Actor",
        "owasp": "A01:2021-Broken Access Control",
        "description": "The product exposes sensitive information to an actor that is not explicitly authorized to have access to that information.",
        "soc2_controls": ["CC6.1", "CC6.3", "CC6.6"],
        "iso27001_controls": ["A.8.12 Data Leakage Prevention", "A.8.8 Management of Technical Vulnerabilities"],
        "nist_csf": ["PR.DS-01", "RS.MI-01"],
        "remediation_best_practice": "Upgrade affected library dependencies to patched versions and enforce strict egress header filtering.",
    },
}


def lookup_cwe(cwe_id: str) -> dict:
    """Retrieve trusted security knowledge for a given CWE identifier."""
    return CWE_KNOWLEDGE.get(
        cwe_id,
        {
            "name": "Generic Software Weakness",
            "owasp": "A04:2021-Insecure Design",
            "description": "Identified potential security weakness in code or system configuration.",
            "soc2_controls": ["CC6.1", "CC7.1"],
            "iso27001_controls": ["A.8.8 Management of Technical Vulnerabilities"],
            "nist_csf": ["PR.IP-01"],
            "remediation_best_practice": "Review code in accordance with OWASP Secure Coding Practices.",
        },
    )

