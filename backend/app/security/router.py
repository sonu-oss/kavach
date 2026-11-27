"""FastAPI router for Kavach AppSec Intelligence Platform."""

from typing import Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.security.agents import SecurityAgentPipeline
from app.security.analyzer import scan_code, scan_dependencies
from app.security.knowledge import lookup_cwe

router = APIRouter(prefix="/security", tags=["appsec"])


class ScanRequest(BaseModel):
    source_code: str = Field(default="", description="Python source code to analyze")
    requirements: str = Field(default="", description="requirements.txt content to audit for CVEs")
    filename: str = Field(default="app.py", description="Source code file name")


class RemediateRequest(BaseModel):
    source_code: str = Field(..., description="Vulnerable source code")
    finding_id: str = Field(..., description="ID of finding to remediate")
    filename: str = Field(default="app.py", description="Source code file name")


@router.post("/scan")
async def scan_endpoint(req: ScanRequest) -> dict[str, Any]:
    """Execute static AST analysis, secrets detection, and dependency CVE checks."""
    ast_findings = scan_code(req.source_code, filename=req.filename) if req.source_code else []
    dep_findings = scan_dependencies(req.requirements) if req.requirements else []
    all_findings = ast_findings + dep_findings

    return {
        "total_findings": len(all_findings),
        "critical_count": sum(1 for f in all_findings if f.severity == "CRITICAL"),
        "high_count": sum(1 for f in all_findings if f.severity == "HIGH"),
        "medium_count": sum(1 for f in all_findings if f.severity == "MEDIUM"),
        "findings": [
            {
                "id": f.id,
                "category": f.category,
                "cwe": f.cwe,
                "severity": f.severity,
                "title": f.title,
                "description": f.description,
                "file_path": f.file_path,
                "line_number": f.line_number,
                "snippet": f.snippet,
                "remediation_hint": f.remediation_hint,
                "cwe_info": lookup_cwe(f.cwe),
            }
            for f in all_findings
        ],
    }


@router.post("/pipeline")
async def run_pipeline_endpoint(req: RemediateRequest) -> dict[str, Any]:
    """Execute full 6-agent collaborative workflow:
    1. Investigator -> 2. RAG Researcher -> 3. Risk Prioritizer -> 4. Remediation -> 5. Verification -> 6. Compliance.
    """
    findings = scan_code(req.source_code, filename=req.filename)
    target = next((f for f in findings if f.id == req.finding_id), None)
    if not target:
        if findings:
            target = findings[0]
        else:
            raise HTTPException(status_code=404, detail="No matching vulnerability found in provided source code")

    # Step 1: Investigation
    investigation = SecurityAgentPipeline.step_1_investigate(target, req.source_code)

    # Step 2: RAG Research
    rag_data = SecurityAgentPipeline.step_2_rag_research(target.cwe)

    # Step 3: Risk Prioritization
    risk_assessment = SecurityAgentPipeline.step_3_risk_prioritize(investigation, rag_data)

    # Step 4: Remediation
    remediation = await SecurityAgentPipeline.step_4_remediate(target, req.source_code, rag_data)

    # Step 5: Verification (deterministic AST verification)
    verification = SecurityAgentPipeline.step_5_verify(remediation["patched_code"], target)

    # Step 6: Compliance
    compliance = SecurityAgentPipeline.step_6_compliance(findings)

    return {
        "finding": {
            "id": target.id,
            "title": target.title,
            "cwe": target.cwe,
            "severity": target.severity,
        },
        "agent_workflow": {
            "1_investigation": investigation,
            "2_rag_research": rag_data,
            "3_risk_prioritization": risk_assessment,
            "4_remediation": remediation,
            "5_verification": verification,
            "6_compliance": compliance,
        },
    }


@router.post("/compliance")
async def compliance_report_endpoint(req: ScanRequest) -> dict[str, Any]:
    """Generate enterprise compliance report mapped to SOC 2 Type II, ISO 27001, and NIST CSF."""
    ast_findings = scan_code(req.source_code, filename=req.filename) if req.source_code else []
    dep_findings = scan_dependencies(req.requirements) if req.requirements else []
    findings = ast_findings + dep_findings

    return SecurityAgentPipeline.step_6_compliance(findings)

