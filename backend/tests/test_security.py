"""Tests for Kavach AppSec Security Analysis & Multi-Agent Compliance Engine."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security.agents import SecurityAgentPipeline
from app.security.analyzer import scan_code, scan_dependencies
from app.security.knowledge import lookup_cwe

client = TestClient(app)

VULNERABLE_SAMPLE_CODE = """
import os
import sqlite3
import pickle

API_KEY = "AIzaSyD-1234567890abcdefghijklmnopqrstuvw"

def get_user_data(user_id):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    # SQL injection vulnerability
    query = f"SELECT * FROM users WHERE id = {user_id}"
    cursor.execute(query)
    return cursor.fetchall()

def run_command(cmd):
    # Command injection vulnerability
    os.system("echo " + cmd)

def load_payload(raw_data):
    # Deserialization vulnerability
    return pickle.loads(raw_data)
"""

SAFE_SAMPLE_CODE = """
import os
import sqlite3
import json

API_KEY = os.getenv("API_KEY", "")

def get_user_data(user_id):
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    return cursor.fetchall()

def run_command(cmd):
    import subprocess
    subprocess.run(["echo", cmd], shell=False)

def load_payload(raw_data):
    return json.loads(raw_data)
"""


def test_ast_scanner_detects_multiple_vulnerabilities():
    findings = scan_code(VULNERABLE_SAMPLE_CODE)
    categories = {f.category for f in findings}
    cwes = {f.cwe for f in findings}

    assert "SQL_INJECTION" in categories
    assert "COMMAND_INJECTION" in categories
    assert "DESERIALIZATION" in categories
    assert "SECRETS" in categories

    assert "CWE-89" in cwes
    assert "CWE-78" in cwes
    assert "CWE-502" in cwes
    assert "CWE-798" in cwes


def test_ast_scanner_clean_on_safe_code():
    findings = scan_code(SAFE_SAMPLE_CODE)
    high_critical = [f for f in findings if f.severity in ("CRITICAL", "HIGH")]
    assert len(high_critical) == 0


def test_dependency_scanner_flags_known_cves():
    reqs = """
    requests==2.28.0
    jinja2==3.0.1
    fastapi==0.115.0
    """
    findings = scan_dependencies(reqs)
    pkg_names = [f.evidence.get("package") for f in findings]
    assert "requests" in pkg_names
    assert "jinja2" in pkg_names
    assert "fastapi" not in pkg_names


def test_cwe_knowledge_lookup():
    cwe89 = lookup_cwe("CWE-89")
    assert "SQL Injection" in cwe89["name"]
    assert "CC6.1" in cwe89["soc2_controls"]
    assert "A03:2021-Injection" in cwe89["owasp"]


import asyncio

def test_multi_agent_pipeline_workflow():
    findings = scan_code(VULNERABLE_SAMPLE_CODE)
    sqli = next(f for f in findings if f.cwe == "CWE-89")

    # Step 1: Investigation
    inv = SecurityAgentPipeline.step_1_investigate(sqli, VULNERABLE_SAMPLE_CODE)
    assert inv["finding_id"] == sqli.id
    assert inv["target_line"] == sqli.line_number

    # Step 2: RAG Research
    rag = SecurityAgentPipeline.step_2_rag_research(sqli.cwe)
    assert "A03:2021-Injection" in rag["owasp"]

    # Step 3: Risk Prioritization
    risk = SecurityAgentPipeline.step_3_risk_prioritize(inv, rag)
    assert risk["cvss_score"] >= 8.0
    assert risk["urgency"] == "IMMEDIATE"

    # Step 4: Remediation
    rem = asyncio.run(SecurityAgentPipeline.step_4_remediate(sqli, VULNERABLE_SAMPLE_CODE, rag))
    assert "diff" in rem
    assert "patched_code" in rem

    # Step 5: Verification (deterministic verification of the patch)
    ver = SecurityAgentPipeline.step_5_verify(SAFE_SAMPLE_CODE, sqli)
    assert ver["verified"] is True
    assert ver["verification_status"] == "PROVEN_FIXED"

    # Step 6: Compliance
    comp = SecurityAgentPipeline.step_6_compliance(findings)
    assert comp["soc_2_type_ii"]["status"] == "NON_COMPLIANT"
    assert "CC6.1" in comp["soc_2_type_ii"]["affected_controls"]


def test_security_scan_api():
    resp = client.post("/security/scan", json={"source_code": VULNERABLE_SAMPLE_CODE})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_findings"] >= 4
    assert data["critical_count"] >= 3


def test_security_pipeline_api():
    resp = client.post(
        "/security/pipeline",
        json={"source_code": VULNERABLE_SAMPLE_CODE, "finding_id": "AST-SQLI-1"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "agent_workflow" in data
    assert "1_investigation" in data["agent_workflow"]
    assert "5_verification" in data["agent_workflow"]


def test_security_compliance_api():
    resp = client.post(
        "/security/compliance",
        json={"source_code": VULNERABLE_SAMPLE_CODE, "requirements": "requests==2.28.0"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "soc_2_type_ii" in data
    assert "iso_27001_2022" in data
