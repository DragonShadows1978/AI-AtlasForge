from adversarial_testing.blind_agent_runner import BlindAgentRedTeam


def test_complete_bug_after_incomplete_bug_is_preserved():
    content = """---BUG---
File: bad.py
Line: 1
Severity: HIGH
Type: logic_error
Description: incomplete first

---BUG---
File: good.py
Line: 2
Severity: CRITICAL
Type: crash
Description: complete second
Reproduction: run it
---END BUG---
"""

    findings = BlindAgentRedTeam._parse_markdown_findings(content)

    assert any(
        f["affected_code"] == "good.py:2"
        and f["description"] == "complete second"
        and f["severity"] == "critical"
        for f in findings
    )
    assert any(f["title"].startswith("[PARTIAL] incomplete first") for f in findings)


def test_complete_suspected_after_incomplete_suspected_is_preserved():
    content = """---SUSPECTED---
File: first.py
Line: 1
Description: incomplete suspected

---SUSPECTED---
File: second.py
Line: 9
Description: complete suspected
---END SUSPECTED---
"""

    findings = BlindAgentRedTeam._parse_markdown_findings(content)

    assert len(findings) == 1
    assert findings[0]["affected_code"] == "second.py:9"
    assert findings[0]["description"] == "complete suspected"
    assert findings[0]["_suspected"] is True
