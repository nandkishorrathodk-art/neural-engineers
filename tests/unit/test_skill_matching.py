from types import SimpleNamespace

from neural.constants import required_skills_satisfied


def _employee(*skills: str) -> SimpleNamespace:
    return SimpleNamespace(skills=[SimpleNamespace(skill_name=name) for name in skills])


def test_no_requirements_imposes_no_gate():
    assert required_skills_satisfied(_employee(), None) is True
    assert required_skills_satisfied(_employee(), []) is True


def test_spelling_variants_of_the_same_skill_match():
    # A task requirement and an employee skill are authored independently; the
    # gate must not reject a qualified employee over a generic suffix word.
    assert required_skills_satisfied(_employee("Python"), ["Python programming"]) is True
    assert required_skills_satisfied(_employee("Python programming"), ["Python"]) is True
    assert required_skills_satisfied(_employee("Software Engineering"), ["engineering"]) is True


def test_distinct_technologies_do_not_match():
    assert required_skills_satisfied(_employee("JavaScript"), ["Python programming"]) is False
    assert required_skills_satisfied(_employee("Python"), ["Rust"]) is False


def test_token_superset_and_subset_match():
    assert required_skills_satisfied(_employee("React"), ["React Native"]) is True
    assert required_skills_satisfied(_employee("React Native"), ["React"]) is True


def test_case_and_whitespace_are_ignored():
    assert required_skills_satisfied(_employee("  pYtHoN  "), ["PYTHON"]) is True


def test_requirement_with_only_stopwords_is_not_a_gate():
    # A requirement made entirely of generic words carries no signal; it must not
    # permanently strand every employee.
    assert required_skills_satisfied(_employee("Python"), ["programming"]) is True


def test_employee_without_skills_fails_an_explicit_requirement():
    assert required_skills_satisfied(_employee(), ["Python"]) is False
