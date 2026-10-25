# What the DORA metrics need

dora-tools measures a team's software delivery with the five [DORA](https://dora.dev/guides/dora-metrics/) metrics, from its code host and its tracker. This is everything a project has to have, and do, for the metrics to be calculated and correct: settings in its code host and tracker, files committed to its repository, the team's habits, and the setup on the machine that collects the data. Read it before adopting the metrics in a project.

The metrics work with any branching model and any merge strategy: trunk-based or with a `develop` branch, merge commits, squashes, rebases or fast forwards, fixing forward or rolling back. [How your process affects the numbers](#how-your-process-affects-the-numbers) says what each choice means for them. The few things that would make a metric wrong are requirements below.

Each requirement has an id, such as **C2**, so a project's own documentation can say which it meets, and `dora check` reports each by its id. For each one the tables say who provides it. Where dora-tools can make the change itself, it does so only with the consent of whoever owns what changes, never on its own initiative.

Requirements are grouped by where they're met: the **code host** (C), the **tracker** (T), files committed to the **repository** (R), the team's **habits** (H) and the **machine** that collects the data (M). Bitbucket Cloud and Jira Cloud are supported now.

## The metrics

DORA groups them as throughput and instability. A **production deployment** is a successful deployment to the production environment (**C2**), counted once per commit; a **production failure** is one that degraded the service so that it had to be fixed or rolled back out of turn, recorded as a Jira issue with the failure label (**H1**). A deployment belongs to the period it finished in.

| Metric | Measures | Formula |
|---|---|---|
| Deployment frequency | how often production is deployed | production deployments ÷ weeks in the period |
| Change lead time | how long a change takes to reach production | for each commit a production deployment shipped, leaving out merge commits: the deployment's finish − the commit's authored time. The median over the period. |
| Failed deployment recovery time | how long it takes to restore service after a failure | the restoring deployment's finish − the failure's detection, the issue's creation. The median over the failures restored in the period. |
| Change fail rate | the share of deployments that caused a failure | production deployments that caused at least one failure ÷ all production deployments |
| Deployment rework rate | the share of deployments that were unplanned | production deployments that restored a failure ÷ all production deployments |

- **The commits a deployment shipped** are those in its commit and not in the previous production deployment's commit, as `git log previous..this` lists them.
- **The deployment that caused a failure** is the first production deployment containing the commit recorded as its cause (**H5**).
- **The deployment that restored a failure** is the first production deployment after its detection that either shipped a commit or pull request carrying the failure's key (**H2**), or rolled back: deployed a commit older than the one production had.

## How your process affects the numbers

- **Merge strategy.** Lead time starts at each shipped commit's authored time. Merge commits, rebases and fast forwards keep a branch's commits, so lead time includes the time since each was written. A squash makes one new commit, so lead time starts at the squash. Neither is wrong; they measure from different points, so compare a team with itself, not with a team that merges differently.
- **Integration branches.** A branch that gathers changes before production, such as `develop` or `release/*`, is fine, as long as it reaches production with its commits (**C4**).
- **Manual deployment.** A deployment that waits for someone to start it counts from when it finishes, so lead time includes the wait, as it should: the change wasn't with users until then.
- **Fixing forward or rolling back.** Either restores a failure: a rollback, deploying an older commit, ends recovery when it finishes, as a fix does.
- **Several repositories, or several services in one.** Today a repository is measured as one application. Combining repositories, or splitting one by service, isn't supported yet.
- **Small numbers.** With a few deployments a month, report over a quarter rather than a week.

## The code host

Where the repository lives and its CI deploys it. Each requirement is the same on every code host; the columns say how each meets it. Bitbucket Cloud is supported now, GitHub next.

| Id | Requirement | Why | Bitbucket Cloud | GitHub | Who provides it | Can dora-tools set it? |
|---|---|---|---|---|---|---|
| **C1** | CI is on for the repository | deployments are recorded by CI | Pipelines on: **Repository settings → Pipelines → Settings** | Actions enabled: **Settings → Actions** | a repository admin | Yes, with the repository admin's consent |
| **C2** | A deployment environment for production, named as `production` in `.config/ianf-dora.toml` says (**R1**) | it's where production deployments are recorded | **Repository settings → Pipelines → Deployments**; Bitbucket makes Test, Staging and Production when a repository first runs a deployment step | **Settings → Environments** | a repository admin | Yes, with the repository admin's consent |
| **C3** | Production is deployed by a CI step that names the production environment, on the production branch (**R1**). Each successful run is one production deployment, finished when the step finishes. A manual trigger is fine. | deployments made any other way are invisible | a step in `bitbucket-pipelines.yml` with `deployment: <environment>` | a job with `environment: <environment>` | the developers, in the committed file | It can propose the change, as a commit for the team to review |
| **C4** | A branch that gathers changes on their way to production, such as `develop`, reaches the production branch with its commits: by a merge commit, a rebase or a fast forward, not a squash. `dora check` finds such branches itself; naming them in `integration_branches` in `.config/ianf-dora.toml` makes it certain. | a squash turns every change on the branch into one new commit, so their lead times start at that merge | the merge strategy chosen when merging | the same | everyone who merges | No: it's chosen at each merge |
| C5 | *Recommended.* Only the production branch may deploy to production | stops another branch's deployment counting as production | the environment's settings, where your plan offers it | the environment's deployment branch rule | a repository admin | Yes, where the API offers it, with the repository admin's consent |

Bitbucket stops listing a deployment once its pipeline's artifacts expire, 14 days after it runs. That's why the data is collected daily (**M4**) and why history begins at the first collection. GitHub keeps deployments.

## The tracker

Where the team's issues are, and so where production failures are recorded. Jira Cloud is supported now; other trackers would meet the same requirements their own way.

| Id | Requirement | Why | Jira Cloud | Who provides it | Can dora-tools set it? |
|---|---|---|---|---|---|
| **T1** | A space for the repository's issues, set in `.config/ianf-tracker.toml` (**R1**) | where production failures are recorded | a space (Jira's API calls it a project) | as jira-tools needs it | — |
| **T2** | A way to mark an issue as a production failure: the failure label, `production-failure` unless `failure_label` in `.config/ianf-dora.toml` names another | marks an issue as a production failure | a label, which needs no setup | nothing to set | — |
| T3 | *Optional.* A way to link a failure to the issue whose work caused it | a cause can then be named by its issue; without it, it's recorded as a web link (**H5**) | an issue link type whose inward description reads `is caused by`, such as one named **Causes** (outward `causes`), under **Settings → System → Issue linking**; sites with Jira Service Management usually have one, named Problem/Incident | a tracker admin | Yes, with a site admin's consent, through Jira's API |
| T4 | *Optional, for later.* A detection date on failure issues | lets a failure logged late give its real detection time | a **Detected** date field | a tracker admin | Yes, with a site admin's consent |

The collecting account needs only to read the space; it never changes an issue.

## The repository

| Id | Requirement | Why | Who provides it | Can dora-tools set it? |
|---|---|---|---|---|
| **R1** | A `.config/ianf-dora.toml` committed on the repository's main branch, alongside jira-tools' `.config/ianf-tracker.toml`. It's read from the code host, not from anyone's clone. | what counts as production is the team's to decide, so it's shared, not personal | the developers, in a reviewed commit | It can propose the file, as a commit for the team to review |
| **R2** | A document for the team saying how the metrics apply to their process and what each person does | the habits (**H**) only work if everyone knows them | the developers | It can propose one |

```toml
#:schema https://raw.githubusercontent.com/ianflanagan1/dora-tools/main/schema/ianf-dora.v1.json
# Needed: the version of this file's format, whose schema is in schema/.
version = 1
# Needed: the deployment environment whose deployments count as production, as the code host
# names it, matched ignoring case.
production = "production"
# The label that marks an issue as a production failure.
failure_label = "production-failure"
# The branch production is deployed from. The repository's main branch unless set.
production_branch = "main"
# Branches that gather changes on their way to production, such as develop. None unless set.
integration_branches = []
# The team's document (R2).
doc = "docs/dora-metrics.md"
```

## The team's habits

Nothing can enforce these, and nothing can detect every miss, so the team agrees them and its document (**R2**) states them.

| Id | Habit | If it's missed |
|---|---|---|
| **H1** | When a production problem has to be fixed or rolled back out of turn, dropping or interrupting planned work, create an issue for it in the tracker **on discovery**, marked as a failure (**T2**). Any issue type. Not for problems found before production, bugs fixed in turn, or deploy steps that fail, since production didn't change. | the failure isn't counted, or, created late, its recovery time is too short: a tracker can't change an issue's creation time |
| **H2** | Put the issue's key in the fix: its branch name, its pull request's title, or a commit message. With Jira and jira-tools, `jira start <key>` names the branch. A rollback needs no key. | a fix-forward can't be found in a deployment, so the failure stays open and the fix isn't counted as rework |
| **H3** | Don't reuse a failure's key for unrelated work afterwards. If the first fix doesn't restore service, fix it again under the same key. | recovery would run to the unrelated work |
| **H4** | Deploy production only through the CI step (**C3**). | the deployment isn't counted |
| **H5** | When the cause is found, record the commit that introduced it, which `git blame` on the broken line shows, or the pull request that merged it: a web link titled `Caused by` to the commit or pull request, or, with **T3**, a link to the issue whose work it was. With Jira and jira-tools: `jira weblink <key> <url> "Caused by"`, or `jira link <key> "is caused by" <other key>`. | the failure isn't counted against a deployment, so change fail rate is too low; recovery time and rework rate are unaffected |

## The machine that collects the data

| Id | Requirement | Who provides it | Can dora-tools set it? |
|---|---|---|---|
| **M1** | The commands for the code host and tracker set up: for Bitbucket and Jira, repo-tools and jira-tools, with `bitbucket` and `jira` on the PATH, as their READMEs say | the person collecting | — |
| **M2** | A code host token, stored in the system credential store. For Bitbucket, an API token with only `read:repository:bitbucket`, `read:pullrequest:bitbucket` and `read:pipeline:bitbucket`; for GitHub, `gh`'s login. | the person collecting, from their Atlassian account | No: only its owner can create and store a token |
| **M3** | A tracker token: for Jira, an API token, stored as jira-tools' README says | the person collecting | No, as for M2 |
| **M4** | The collection runs daily, as a scheduled job | the person collecting | Yes, with the machine owner's consent: see [The scheduled job](#the-scheduled-job) |

### What collecting does

- **Reads**, never writes, from the code host and tracker: the production deployments, the commits each shipped, merged pull requests, and the issues with the failure label and their links.
- **Writes** only to a SQLite file on the collecting machine, at a path stated when it's installed. It holds commit hashes, authored times and first lines of commit messages, pull request titles and authors' names, deployment ids and times, and failure issues' keys, creation times and links. It holds no code and no tokens.
- **Is safe to run again**, and catches up on what it missed since its last run, as long as that's less than 14 days.

### The scheduled job

With the machine owner's consent, dora-tools can install one entry in the user's crontab, on macOS or Linux, that runs the collection once a day and appends its output to a log file. Before installing it, it shows the exact line and the paths of the database and the log; removing it is deleting that line, with `crontab -e`. cron doesn't run a job missed while the machine was off or asleep; the next day's run catches up, within the 14 days.

Up to about 10 days without a run loses nothing; 14 days or more, such as a holiday with the laptop off, may lose the deployments from the start of the gap. A collection run by a scheduled pipeline, into a shared store, would remove that risk; it's not built yet.

## Checking a project

`dora check`, given the repository's name or run in a clone of it, checks every requirement that can be checked and reports each as met, missed with how to fix it, for a person to look at, or not checked and why. It reads the code host and tracker with the collecting machine's tokens and changes nothing; it ends ⚠️ when a requirement is missed.

| Kind | Requirements | Limits |
|---|---|---|
| Settings, as they are now | C1, C2, C3, R1, R2, T1, T3, T4, M1–M4 | C1 reads the setting only with `admin:repository:bitbucket`; without it, Pipelines having run shows it's on. C3 finds the step, not that nothing else deploys. R2 finds the document, not that it's read. |
| Habits, in what happened since `--since` | C4, H1, H2, H3, H5 | H1 finds only a failure recorded after its fix began; H3 only flags keys for a person to look at; H5 finds a failure with no cause, not a wrong one. |
| Not checkable | C5, T2, H4 | C5 isn't in Bitbucket's API; T2 needs no setup; a deploy outside CI leaves no trace. |

## Consent, in one place

dora-tools changes nothing on its own initiative. These are the changes it can make, and whose consent each needs:

| Change | Where | Consent of |
|---|---|---|
| Turn CI on (**C1**), create the production environment (**C2**), or restrict it to the production branch (**C5**) | the code host's repository settings | its repository admin |
| Add a causes link type (**T3**), or a detection date (**T4**) | the tracker's site, for every space on it | a tracker admin |
| Propose `.config/ianf-dora.toml` (**R1**), the pipeline step (**C3**) or the team's document (**R2**) | the repository | the developers, by reviewing the commit |
| Install the daily job (**M4**) | the collecting machine | its owner |

It never creates or stores a token, deploys, changes an issue, or writes to the code host or tracker while collecting.
