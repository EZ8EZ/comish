import pytest

from comish.answer.scope import OUT_OF_SCOPE_REPLY, out_of_scope

OUT = [
    "What are the current scores?",
    "who's winning my matchup",
    "How many more points would I need to score to win this week?",
    "Who has players left to play this week?",
    "Which RB had the highest YAC over the past 2 seasons?",
    "What combination of plays does Zay Flowers need to score 30 points?",
    "Who owns my 2027 first round pick right now?",
]

# Real rules and history questions must reach the normal pipeline.
IN = [
    "What is the current scoring for a rushing touchdown?",
    "What are the current roster slots?",
    "Is there a trade deadline this week?",
    "How many points does a reception score?",
    "What do we need to make the playoffs?",
    "Which round is the rookie draft lottery?",
    "Who won the league last season?",
    "Can I trade my 2027 first round pick?",
    "When did we remove scoring bonuses for big plays?",
]


@pytest.mark.parametrize("question", OUT)
def test_unanswerable_questions_are_diverted(question):
    assert out_of_scope(question)


@pytest.mark.parametrize("question", IN)
def test_rules_questions_are_never_diverted(question):
    assert out_of_scope(question) is None


def test_reply_does_not_promise_a_flag():
    assert "flag" not in OUT_OF_SCOPE_REPLY.lower()
