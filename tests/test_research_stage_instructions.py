from tradingagents.research.stages import ROLE_INSTRUCTIONS, AnalysisOutput, instruction


def test_all_roles_receive_evidence_language_and_identifier_contracts():
    for role in ROLE_INSTRUCTIONS:
        prompt = instruction(role, AnalysisOutput)["system"]
        assert "Use only the payload language" in prompt
        assert "translation is a separate finalization call" in prompt
        assert "never generated claim, finding or question IDs" in prompt
        assert "stage name" in prompt
        assert "Preserve supplied question IDs" in prompt


def test_planner_is_a_plan_not_a_bilingual_final_report():
    prompt = instruction("planner", AnalysisOutput)["system"]
    assert "3-5 decisive investment questions" in prompt
    assert "not the final report" in prompt
    assert "within 200 words" in prompt
