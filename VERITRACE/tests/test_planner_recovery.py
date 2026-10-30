"""A malformed Planner action can be corrected without losing session state."""

from veritrace.agent.runtime.program import PlannerProgram
from veritrace.agent.runtime.scheduler import TaskScheduler


def test_restructure_requires_reasoning_and_accepts_corrected_action():
    scheduler = TaskScheduler(llm=object())
    program = PlannerProgram()
    program.inject("propose_restructure", scheduler.propose_restructure)
    try:
        failure = program.execute('propose_restructure("R_conc")')
        assert not failure.success
        assert "reasoning" in failure.to_error_feedback()
        assert not scheduler.has_pending_restructure()

        corrected = program.execute('propose_restructure("R_conc", "Split the concept into supported groups")')
        assert corrected.success
        assert scheduler.consume_restructure_proposal() == {
            "op_type": "R_conc",
            "reasoning": "Split the concept into supported groups",
        }
    finally:
        scheduler.close()
