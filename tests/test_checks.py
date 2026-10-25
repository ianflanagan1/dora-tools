"""Each requirement's check, against a fake Bitbucket and Jira."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from cli_kit.outcome import Precondition

from dora import checks
from dora.checks import Status
from dora.project import Settings

SINCE = datetime(2026, 7, 1, tzinfo=UTC)  # as the ctx fixture's


def at(days: float):
    return SINCE + timedelta(days=days)


def status(check, ctx) -> Status:
    return check(ctx).status


def with_settings(ctx, **settings) -> None:
    ctx.project = replace(ctx.project, settings=Settings("production", **settings))


# Settings


def test_pipelines_on_is_met_when_the_setting_is_on(ctx):
    assert status(checks.pipelines_on, ctx) == Status.MET


def test_pipelines_off_is_missed(ctx, bitbucket_fake):
    bitbucket_fake.enabled = False
    assert status(checks.pipelines_on, ctx) == Status.MISSED


def test_without_the_admin_scope_pipelines_on_is_inferred_from_its_runs(ctx, bitbucket_fake):
    bitbucket_fake.enabled = None
    result = checks.pipelines_on(ctx)
    assert result.status == Status.MET
    assert "admin:repository:bitbucket" in result.details[0]


def test_without_the_admin_scope_or_any_runs_pipelines_on_is_unchecked(ctx, bitbucket_fake):
    bitbucket_fake.enabled, bitbucket_fake.runs = None, []
    assert status(checks.pipelines_on, ctx) == Status.UNCHECKED


def test_the_production_environment_is_matched_ignoring_case(ctx):
    assert status(checks.production_environment, ctx) == Status.MET


def test_a_missing_environment_says_where_to_add_it_or_which_to_name(ctx, bitbucket_fake):
    bitbucket_fake.environment_names = ["Test", "Live"]
    result = checks.production_environment(ctx)
    assert (result.status, result.details) == (Status.MISSED, ("It has: Test, Live.",))
    assert "Repository settings → Pipelines → Deployments" in result.fix[0]
    assert "set production in .config/ianf-dora.toml to one of: Test, Live" in result.fix[1]


def test_production_deploys_from_the_repos_main_branch_unless_set(ctx):
    assert ctx.production_branch == "main"
    with_settings(ctx, production_branch="release")
    assert ctx.production_branch == "release"


def test_the_deployment_step_is_found_in_the_production_branchs_pipeline(ctx):
    assert status(checks.deployment_step, ctx) == Status.MET


def test_a_pipeline_without_a_production_deployment_step_is_missed(ctx, bitbucket_fake):
    bitbucket_fake.files[("main", "bitbucket-pipelines.yml")] = "deployment: staging\n"
    assert status(checks.deployment_step, ctx) == Status.MISSED


def test_a_production_branch_without_a_pipeline_is_missed(ctx, bitbucket_fake):
    bitbucket_fake.files.clear()
    result = checks.deployment_step(ctx)
    assert "No bitbucket-pipelines.yml on main" in result.summary
    assert "set production_branch in .config/ianf-dora.toml" in result.fix[1]


def test_a_project_without_dora_settings_misses_r1_and_leaves_what_needs_it_unchecked(ctx):
    ctx.project = replace(
        ctx.project, settings=None, problem=("No .config/ianf-dora.toml on main.",)
    )
    result = checks.dora_table(ctx)
    assert result.summary == "No .config/ianf-dora.toml on main."
    assert result.fix[0] == "Commit to main a .config/ianf-dora.toml with at least:"
    assert 'version = 1, production = "<your production deployment environment>"' in result.fix
    requirement = checks.Requirement("C2", "", checks.production_environment)
    unchecked = checks.run(requirement, ctx)
    assert unchecked.status == Status.UNCHECKED
    assert "R1" in unchecked.summary


def test_the_dora_table_is_shown_as_toml_writes_it(ctx):
    result = checks.dora_table(ctx)
    assert result.summary == 'production = "production", failure_label = "production-failure".'
    assert result.details == (
        "Production deploys from main; changes reach it through no integration branch.",
    )


def test_the_team_document_is_read_from_the_main_branch_where_dora_doc_says(ctx, bitbucket_fake):
    assert checks.team_document(ctx).summary == "docs/dora-metrics.md is on main."
    del bitbucket_fake.files[("main", "docs/dora-metrics.md")]
    assert checks.team_document(ctx).summary == "No docs/dora-metrics.md on main."


def test_the_tracker_settings_come_from_the_committed_tracker_file(ctx):
    assert ctx.tracker.space == "KAN"


def test_without_a_committed_tracker_file_tracker_checks_are_unchecked(ctx):
    ctx.project = replace(ctx.project, tracker=None)
    result = checks.run(checks.Requirement("T1", "", checks.jira_space), ctx)
    summary = "No .config/ianf-tracker.toml on main."
    assert (result.status, result.summary) == (Status.UNCHECKED, summary)


def test_a_jira_space_the_token_cannot_see_is_missed(ctx, jira_fake):
    assert status(checks.jira_space, ctx) == Status.MET
    jira_fake.spaces.clear()
    assert status(checks.jira_space, ctx) == Status.MISSED


def test_a_site_without_a_causes_link_type_is_only_optional_and_says_how_to_add_one(ctx, jira_fake):
    assert status(checks.causes_link_type, ctx) == Status.MET
    jira_fake.types = jira_fake.types[:1]
    result = checks.causes_link_type(ctx)
    assert result.status == Status.OPTIONAL
    assert "web links instead" in result.summary
    assert "Add link type: name Causes, outward causes, inward is caused by" in result.fix[0]


def test_a_missing_detected_field_is_only_optional(ctx, jira_fake):
    jira_fake.fields = ["Summary"]
    assert status(checks.detected_field, ctx) == Status.OPTIONAL


def test_a_missing_tool_says_to_clone_its_repository_and_set_it_up(ctx, monkeypatch):
    monkeypatch.setattr(checks.shutil, "which", lambda tool: None if tool == "bitbucket" else "/x")
    assert list(checks.tools_set_up(ctx).fix) == [
        "git clone https://github.com/ianflanagan1/repo-tools ~/projects/cli/repo-tools"
        " (unless it's there already)",
        "cd ~/projects/cli/repo-tools && devbox run setup",
    ]


def test_a_bitbucket_token_missing_a_scope_names_it_and_how_to_replace_it(ctx, bitbucket_fake):
    assert status(checks.bitbucket_token, ctx) == Status.MET
    bitbucket_fake.forbidden = {"pullrequests"}
    result = checks.bitbucket_token(ctx)
    assert result.summary == "The token lacks read:pullrequest:bitbucket."
    assert (
        "read:repository:bitbucket, read:pullrequest:bitbucket, read:pipeline:bitbucket"
        in result.fix[0]
    )
    assert any("bitbucket-api-token" in line for line in result.fix)


def test_a_jira_token_not_stored_is_missed_with_how_to_store_it(ctx, jira_fake):
    jira_fake.signed_in = False
    result = checks.jira_token(ctx)
    assert (result.status, result.summary) == (Status.MISSED, "No Jira API token is stored.")
    assert result.fix == ("Create one at …",)


def test_a_check_that_cannot_get_its_data_is_unchecked_saying_why(ctx, bitbucket_fake):
    bitbucket_fake.forbidden = {"environments"}
    requirement = checks.Requirement("C2", "", checks.production_environment)
    assert checks.run(requirement, ctx).status == Status.UNCHECKED


# Habits: no branching model or merge strategy is assumed


@pytest.mark.parametrize("parents", [1, 2])
def test_without_integration_branches_any_merge_strategy_is_fine(ctx, bitbucket_fake, parents):
    bitbucket_fake.merge(
        1, "feature/x", "main", parents=parents, squashed=parents == 1, opened=at(1), merged=at(2)
    )
    assert status(checks.history_kept, ctx) == Status.MET


def test_an_integration_branch_merged_keeping_its_commits_is_met(ctx, bitbucket_fake):
    with_settings(ctx, integration_branches=("develop",))
    bitbucket_fake.merge(1, "develop", "main", parents=2, opened=at(1), merged=at(2))
    bitbucket_fake.merge(
        2, "develop", "main", parents=1, opened=at(3), merged=at(4)
    )  # fast forward
    assert checks.history_kept(ctx).summary == "All 2 merges into main kept the branch's commits."


def test_an_integration_branch_squashed_into_production_is_missed(ctx, bitbucket_fake):
    with_settings(ctx, integration_branches=("develop",))
    bitbucket_fake.merge(1, "develop", "main", squashed=True, opened=at(1), merged=at(2))
    bitbucket_fake.merge(2, "feature/x", "develop", squashed=True, opened=at(1), merged=at(1.5))
    result = checks.history_kept(ctx)
    assert result.status == Status.MISSED
    assert result.details == ("#1 PR 1: develop into main",)
    assert "not a squash" in result.fix[0]


def test_a_branch_gathering_changes_is_checked_even_when_not_named(ctx, bitbucket_fake):
    bitbucket_fake.merge(1, "feature/x", "develop", opened=at(1), merged=at(2))
    bitbucket_fake.merge(2, "develop", "main", squashed=True, opened=at(3), merged=at(4))
    result = checks.history_kept(ctx)
    assert result.status == Status.MISSED
    assert result.details[0] == "#2 PR 2: develop into main"
    assert "develop gathers changes on their way to main" in result.details[1]


def test_a_detected_integration_branch_kept_is_met_and_suggests_naming_it(ctx, bitbucket_fake):
    bitbucket_fake.merge(1, "feature/x", "develop", opened=at(1), merged=at(2))
    bitbucket_fake.merge(2, "develop", "main", parents=2, opened=at(3), merged=at(4))
    result = checks.history_kept(ctx)
    assert result.status == Status.MET
    assert "name it in integration_branches in .config/ianf-dora.toml" in result.details[0]


def test_a_feature_branch_squashed_into_production_is_fine(ctx, bitbucket_fake):
    bitbucket_fake.merge(1, "feature/x", "main", squashed=True, opened=at(1), merged=at(2))
    assert status(checks.history_kept, ctx) == Status.MET


@pytest.mark.parametrize(
    ("source", "title", "message"),
    [
        ("KAN-21-fix-checkout", None, None),
        ("fix-checkout", "KAN-21 Fix checkout", None),
        ("fix-checkout", None, "Fix checkout\n\nFixes KAN-21"),
    ],
)
def test_a_fix_carries_the_key_in_its_branch_title_or_message(
    ctx, jira_fake, bitbucket_fake, source, title, message
):
    jira_fake.failure("KAN-21", at(1), done=True)
    bitbucket_fake.merge(
        1, source, "main", title=title, message=message, opened=at(1.5), merged=at(2)
    )
    assert status(checks.fixes_carry_the_key, ctx) == Status.MET


def test_a_done_failure_with_no_pull_request_carrying_its_key_is_missed(ctx, jira_fake):
    jira_fake.failure("KAN-20", at(1), done=True)
    result = checks.fixes_carry_the_key(ctx)
    assert result.status == Status.MISSED
    assert result.details == ("KAN-20 Failure KAN-20",)
    assert "branch name, pull request title or commit message" in result.fix[0]
    assert (
        result.fix[1]
        == "If KAN-20 wasn't a production failure: jira edit KAN-20 --unlabel production-failure"
    )


def test_open_and_done_failures_are_counted_apart(ctx, jira_fake, bitbucket_fake):
    jira_fake.failure("KAN-20", at(1), done=False)
    jira_fake.failure("KAN-21", at(1), done=True)
    bitbucket_fake.merge(1, "KAN-21-fix", "main", opened=at(1.5), merged=at(2))
    assert checks.fixes_carry_the_key(ctx).summary == (
        "1 done, with a merged pull request carrying its key; 1 open."
    )


def test_with_no_failures_since_h2_says_so(ctx):
    assert checks.fixes_carry_the_key(ctx).summary == "No failures recorded since 2026-07-01."


def test_failures_recorded_before_since_are_left_out(ctx, jira_fake):
    jira_fake.failure("KAN-20", at(-1), done=True)
    assert status(checks.fixes_carry_the_key, ctx) == Status.MET


def test_a_key_merged_again_long_after_its_fix_is_for_review(ctx, jira_fake, bitbucket_fake):
    jira_fake.failure("KAN-20", at(1))
    bitbucket_fake.merge(1, "KAN-20-fix", "main", opened=at(1), merged=at(2))
    bitbucket_fake.merge(2, "KAN-20-more", "main", opened=at(30), merged=at(31))
    result = checks.keys_not_reused(ctx)
    assert result.status == Status.REVIEW
    assert result.details == ("KAN-20: merged again 29 days after its first fix",)


def test_a_second_fix_soon_after_the_first_is_not_reuse(ctx, jira_fake, bitbucket_fake):
    jira_fake.failure("KAN-20", at(1))
    bitbucket_fake.merge(1, "KAN-20-fix", "main", opened=at(1), merged=at(2))
    bitbucket_fake.merge(2, "KAN-20-fix-again", "main", opened=at(3), merged=at(4))
    assert status(checks.keys_not_reused, ctx) == Status.MET


def test_a_cause_is_an_is_caused_by_link_or_a_caused_by_web_link(ctx, jira_fake):
    jira_fake.failure("KAN-20", at(1), caused_by="KAN-7")
    jira_fake.failure("KAN-21", at(2))
    jira_fake.links["KAN-21"] = [{"object": {"title": "Caused by", "url": "https://x/c/1"}}]
    assert status(checks.causes_recorded, ctx) == Status.MET


def test_a_failure_without_a_cause_gets_the_commands_that_record_one(ctx, jira_fake):
    jira_fake.failure("KAN-20", at(1))
    result = checks.causes_recorded(ctx)
    assert result.summary == "1 of 1 failure has no cause recorded."
    assert "the commit that brought in the broken code" in result.fix[0]
    assert "squash" not in " ".join(result.fix)
    assert (
        'jira weblink KAN-20 https://bitbucket.org/team/app/commits/<hash> "Caused by"'
        in result.fix
    )
    assert 'jira link KAN-20 "is caused by" <key of the issue whose work it was>' in result.fix
    assert "jira edit KAN-20 --unlabel production-failure" in result.fix[-1]


def test_without_a_causes_link_type_only_the_web_link_is_offered(ctx, jira_fake):
    jira_fake.types = jira_fake.types[:1]
    jira_fake.failure("KAN-20", at(1))
    assert not any("jira link" in line for line in checks.causes_recorded(ctx).fix)


def test_a_failure_recorded_after_its_fix_began_is_for_review(ctx, jira_fake, bitbucket_fake):
    jira_fake.failure("KAN-20", at(5))
    bitbucket_fake.merge(1, "KAN-20-fix", "main", opened=at(1), merged=at(6))
    result = checks.labelled_on_discovery(ctx)
    assert result.status == Status.REVIEW
    assert result.details[0] == "KAN-20: created after its fix's pull request opened"


def test_jiras_creation_times_are_read_with_their_offset(ctx, jira_fake):
    jira_fake.failure("KAN-20", at(1))
    jira_fake.issues[0]["fields"]["created"] = "2026-07-02T23:01:22.862+0100"
    assert [i["key"] for i in ctx.failures] == ["KAN-20"]


# The list


def test_every_requirement_in_the_contract_is_listed():
    ids = {r.id for group in checks.REQUIREMENTS.values() for r in group}
    assert ids == {
        *(f"C{n}" for n in range(1, 6)),
        *(f"T{n}" for n in range(1, 5)),
        "R1",
        "R2",
        *(f"H{n}" for n in range(1, 6)),
        *(f"M{n}" for n in range(1, 5)),
    }


def test_no_check_assumes_a_merge_strategy_or_branch_names():
    text = open(checks.__file__).read()
    for word in ("develop", "master", "squash merge", "Merged in "):
        assert word.casefold() not in text.casefold() if word.islower() else word not in text, word


def test_a_check_raising_a_precondition_is_unchecked(ctx):
    def needs_token(ctx):
        raise Precondition("No Bitbucket API token is stored.")

    result = checks.run(checks.Requirement("X1", "", needs_token), ctx)
    assert (result.status, result.summary) == (
        Status.UNCHECKED,
        "No Bitbucket API token is stored.",
    )
